from concurrent.futures import ThreadPoolExecutor
import urllib.request
import xml.etree.ElementTree as ET
import ssl
from typing import List, Dict, Any
from html import unescape
import re
import logging
from sources.feed_utils import fresh_rss_date
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

# Noise / Clickbait / Non-Market filter
DISCARD_PATTERNS = [
    r"\brecalled\b", r"\bspotted\b", r"\bfiring incident\b", r"\bactor\b",
    r"\bwhy are stocks falling\b", r"\bwhy is market falling\b",
    r"\btop 10 stocks\b", r"\bstocks to buy today\b",
    r"\bcrypto\b", r"\bwallet breach\b", r"\bbitcoin\b", r"\bethereum\b",
    r"\bhoroscope\b", r"\blifestyle\b", r"\bentertainment\b",
    r"\bweb stories\b", r"\bphotos:\b", r"\bvideo:\b",
    r"\bdating\b", r"\bcelebrity\b"
]

class NewsFetcher:
    def __init__(self):
        self.ctx = ssl.create_default_context()
        self.last_health = {}
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
        }
        self.session = requests.Session(impersonate="chrome120")

    @staticmethod
    def _clean_text(text: str) -> str:
        if not text:
            return ""
        clean = re.sub(r'<[^>]+>', ' ', text)
        clean = unescape(clean)
        clean = re.sub(r'\s+', ' ', clean).strip()
        return clean

    @classmethod
    def is_noise(cls, text: str) -> bool:
        t_low = text.lower()
        return any(re.search(pat, t_low) for pat in DISCARD_PATTERNS)

    def fetch_feed(self, feed_info: Dict[str, str]) -> List[Dict[str, Any]]:
        name = feed_info["source"]
        url = feed_info["url"]
        items = []
        try:
            req = urllib.request.Request(url, headers=self.headers)
            with urllib.request.urlopen(req, context=self.ctx, timeout=10) as resp:
                data = resp.read()
                root = ET.fromstring(data)
                if root.tag != "rss" or root.find("channel") is None:
                    raise ValueError("Response is not an RSS channel")
                for it in root.findall(".//item"):
                    title_el = it.find("title")
                    desc_el = it.find("description")
                    pub_el = it.find("pubDate")
                    link_el = it.find("link")
                    
                    title = title_el.text if title_el is not None and title_el.text else ""
                    desc = desc_el.text if desc_el is not None and desc_el.text else ""
                    pub = pub_el.text if pub_el is not None and pub_el.text else ""
                    link = link_el.text if link_el is not None and link_el.text else ""
                    
                    title = self._clean_text(title)
                    desc = self._clean_text(desc)
                    
                    if not title or self.is_noise(f"{title} {desc}"):
                        continue

                    if not fresh_rss_date(pub, 2700):
                        continue

                    items.append({
                        "source": name,
                        "title": title,
                        "description": desc,
                        "pub_date": pub,
                        "link": link
                    })
        except Exception as e:
            raise RuntimeError(f"RSS fetch failed: {name} ({url}): {type(e).__name__}: {e}") from e
        return items

    def fetch_live_web_wires(self) -> List[Dict[str, Any]]:
        """Scrapes real-time stock wires and broker notes from live market pages."""
        items = []
        for page in LIVE_WEB_PAGES:
            name = page["source"]
            url = page["url"]
            try:
                r = self.session.get(url, timeout=10)
                if r.status_code == 200:
                    h2_list = re.findall(r'<h2[^>]*>(.*?)</h2>', r.text, re.DOTALL)
                    for raw_h in h2_list:
                        clean_title = self._clean_text(raw_h)
                        if clean_title and len(clean_title) > 20 and not self.is_noise(clean_title):
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
        # RSS requests use independent urllib connections. Untimestamped category
        # page headings are not live flashes and cannot establish freshness.
        def fetch_checked(feed):
            try:
                items = self.fetch_feed(feed)
                return items, {"status": "items" if items else "empty_unverified", "items": len(items)}
            except Exception as exc:
                logger.warning("%s", exc)
                return [], {"status": "error", "error": str(exc)}
        with ThreadPoolExecutor(max_workers=5) as pool:
            results = list(pool.map(fetch_checked, NEWS_FEEDS))
        self.last_health = {feed["url"]: result[1] for feed, result in zip(NEWS_FEEDS, results)}
        return [item for items, _ in results for item in items]
