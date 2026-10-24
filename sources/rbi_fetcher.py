import urllib.request
import xml.etree.ElementTree as ET
import ssl
from typing import List, Dict, Any
from html import unescape
import logging

logger = logging.getLogger(__name__)

class RBIFetcher:
    def __init__(self):
        self.url = "https://rbi.org.in/pressreleases_rss.xml"
        self.ctx = ssl.create_default_context()
        self.ctx.check_hostname = False
        self.ctx.verify_mode = ssl.CERT_NONE
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }

    def fetch_press_releases(self) -> List[Dict[str, Any]]:
        """Fetch latest RBI press releases."""
        try:
            req = urllib.request.Request(self.url, headers=self.headers)
            with urllib.request.urlopen(req, context=self.ctx, timeout=12) as resp:
                data = resp.read()
                root = ET.fromstring(data)
                items = []
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
                    if title:
                        items.append({
                            "title": title,
                            "description": desc,
                            "pub_date": pub,
                            "link": link
                        })
                return items
        except Exception as e:
            logger.error(f"Error fetching RBI press releases: {e}")
            return []
