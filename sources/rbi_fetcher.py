import urllib.request
import xml.etree.ElementTree as ET
import ssl
from typing import List, Dict, Any
from html import unescape
import re
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
                    
                    title = self._clean_html(title)
                    desc = self._clean_html(desc)
                    
                    if not title:
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
                        r"underwriting auction"
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
            logger.error(f"Error fetching RBI press releases: {e}")
            return []
