import urllib.request
import xml.etree.ElementTree as ET
import ssl
from typing import List, Dict, Any
from html import unescape
import re
import logging
from curl_cffi import requests

logger = logging.getLogger(__name__)

NEWS_FEEDS = [
    {"source": "Economic Times", "url": "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"},
    {"source": "Economic Times", "url": "https://economictimes.indiatimes.com/markets/stocks/news/rssfeeds/2146842.cms"},
    {"source": "Moneycontrol", "url": "https://www.moneycontrol.com/rss/latestnews.xml"},
    {"source": "Moneycontrol", "url": "https://www.moneycontrol.com/rss/MCtopnews.xml"},
    {"source": "Moneycontrol", "url": "https://www.moneycontrol.com/rss/business.xml"},
    {"source": "Moneycontrol", "url": "https://www.moneycontrol.com/rss/marketreports.xml"},
    {"source": "Mint", "url": "https://www.livemint.com/rss/markets"},
    {"source": "Mint", "url": "https://www.livemint.com/rss/companies"},
    {"source": "BusinessLine", "url": "https://www.thehindubusinessline.com/markets/feeder/default.rss"},
    {"source": "BusinessLine", "url": "https://www.thehindubusinessline.com/companies/feeder/default.rss"},
]

LIVE_WEB_PAGES = [
    {"source": "Moneycontrol Live", "url": "https://www.moneycontrol.com/news/business/stocks/"},
    {"source": "Moneycontrol Live", "url": "https://www.moneycontrol.com/news/business/markets/"},
]

class NewsFetcher:
    def __init__(self):
        self.ctx = ssl.create_default_context()
        self.ctx.check_hostname = False
        self.ctx.verify_mode = ssl.CERT_NONE
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
        }
        self.session = requests.Session(impersonate="chrome120")

    def fetch_feed(self, feed_info: Dict[str, str]) -> List[Dict[str, Any]]:
        name = feed_info["source"]
        url = feed_info["url"]
        items = []
        try:
            req = urllib.request.Request(url, headers=self.headers)
            with urllib.request.urlopen(req, context=self.ctx, timeout=10) as resp:
                data = resp.read()
                root = ET.fromstring(data)
                for it in root.findall(".//item"):
                    title_el = it.find("title")
                    desc_el = it.find("description")
                    pub_el = it.find("pubDate")
                    link_el = it.find("link")
                    
                    title = title_el.text if title_el is not None and title_el.text else ""
                    desc = desc_el.text if desc_el is not None and desc_el.text else ""
                    pub = pub_el.text if pub_el is not None and pub_el.text else ""
                    link = link_el.text if link_el is not None and link_el.text else ""
                    
                    title = unescape(title).strip()
                    desc = unescape(desc).strip()
                    desc = re.sub(r'<[^>]+>', ' ', desc).strip()
                    
                    if pub:
                        try:
                            import email.utils
                            from datetime import datetime, timezone
                            dt = email.utils.parsedate_to_datetime(pub)
                            now_utc = datetime.now(timezone.utc)
                            if (now_utc - dt).total_seconds() > 2700:
                                continue
                        except Exception:
                            pass

                    if title:
                        items.append({
                            "source": name,
                            "title": title,
                            "description": desc,
                            "pub_date": pub,
                            "link": link
                        })
        except Exception as e:
            logger.debug(f"Feed fetch error ({name} - {url}): {e}")
        return items

    def fetch_live_web_wires(self) -> List[Dict[str, Any]]:
        """Scrapes real-time stock wires, broker notes, and flashes from live market pages."""
        items = []
        for page in LIVE_WEB_PAGES:
            name = page["source"]
            url = page["url"]
            try:
                r = self.session.get(url, timeout=10)
                if r.status_code == 200:
                    h2_list = re.findall(r'<h2[^>]*>(.*?)</h2>', r.text, re.DOTALL)
                    for raw_h in h2_list:
                        clean_title = re.sub(r'<[^>]+>', '', raw_h).strip()
                        clean_title = unescape(clean_title)
                        if clean_title and len(clean_title) > 15:
                            items.append({
                                "source": name,
                                "title": clean_title,
                                "description": "",
                                "pub_date": "",
                                "link": url
                            })
            except Exception as e:
                logger.debug(f"Live web wires fetch error ({url}): {e}")
        return items

    def fetch_all_feeds(self) -> List[Dict[str, Any]]:
        all_items = []
        for f in NEWS_FEEDS:
            feed_items = self.fetch_feed(f)
            all_items.extend(feed_items)
        
        # Also include fast live web wires
        live_items = self.fetch_live_web_wires()
        all_items.extend(live_items)
        
        return all_items
