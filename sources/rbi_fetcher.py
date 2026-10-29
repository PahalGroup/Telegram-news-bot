import urllib.request
import xml.etree.ElementTree as ET
import ssl
from typing import List, Dict, Any
from html import unescape
import re
import logging
from sources.feed_utils import fresh_rss_date

logger = logging.getLogger(__name__)

class RBIFetcher:
    def __init__(self):
        self.last_health = {}
        self.url = "https://rbi.org.in/pressreleases_rss.xml"
        self.ctx = ssl.create_default_context()
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }

    @staticmethod
    def _clean_html(text: str) -> str:
        """Strip HTML tags, table markup, and extra whitespace."""
        if not text:
            return ""
        # Strip HTML tags
        clean = re.sub(r'<[^>]+>', ' ', text)
        clean = unescape(clean)
        clean = re.sub(r'\s+', ' ', clean).strip()
        return clean

    def fetch_press_releases(self) -> List[Dict[str, Any]]:
        """Fetch latest RBI press releases, dropping routine operational auction notices."""
        try:
            req = urllib.request.Request(self.url, headers=self.headers)
            with urllib.request.urlopen(req, context=self.ctx, timeout=12) as resp:
                data = resp.read()
                root = ET.fromstring(data)
                if root.tag != "rss" or root.find("channel") is None:
                    raise ValueError("RBI response is not an RSS channel")
                items = []
                raw_items = root.findall(".//item")
                self.last_health = {"raw_items": len(raw_items), "rejected_dates": 0,
                                    "sample_dates": [it.findtext("pubDate", "") for it in raw_items[:3]]}
                for it in root.findall(".//item"):
                    title_el = it.find("title")
                    desc_el = it.find("description")
                    pub_el = it.find("pubDate")
                    link_el = it.find("link")
                    
                    title = title_el.text if title_el is not None and title_el.text else ""
                    desc = desc_el.text if desc_el is not None and desc_el.text else ""
                    pub = pub_el.text if pub_el is not None and pub_el.text else ""
                    link = link_el.text if link_el is not None and link_el.text else ""
                    
                    title = self._clean_html(title)
                    desc = self._clean_html(desc)
                    
                    if not title:
                        continue
                    if not fresh_rss_date(pub, 86400):
                        self.last_health["rejected_dates"] += 1
                        continue

                    # Filter out routine operational State Govt auction circulars & daily LAF ops
                    routine_patterns = [
                        r"offered to sell stock by way of auction",
                        r"state governments have offered to sell",
                        r"liquidity adjustment facility",
                        r"marginal standing facility",
                        r"overnight variable rate repo",
                        r"overnight variable rate reverse repo",
                        r"auction of government of india dated securities",
                        r"underwriting auction",
                        r"auction result",
                        r"result of .*auction of state government"
                    ]
                    combined_check = f"{title} {desc}".lower()
                    if any(re.search(pat, combined_check) for pat in routine_patterns):
                        # Only allow if major monetary policy / MPC rate action
                        if not any(k in combined_check for k in ["repo rate", "mpc", "policy rate", "monetary policy statement"]):
                            continue

                    items.append({
                        "company": "RESERVE BANK OF INDIA",
                        "title": title,
                        "description": desc,
                        "pub_date": pub,
                        "link": link
                    })
                return items
        except Exception as e:
            raise RuntimeError("RBI RSS request or parsing failed") from e

