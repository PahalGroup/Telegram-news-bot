import re
from datetime import datetime
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup
from curl_cffi import requests as cureq

IST = ZoneInfo("Asia/Kolkata")

class LiveQuoteWire:
    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        }
        # Explicit query strictly for Indian Union Finance Minister and RBI Governor
        self.query = '(%22Nirmala+Sitharaman%22+OR+%22Union+Finance+Minister%22+OR+%22Finance+Minister+Nirmala%22+OR+%22RBI+Governor%22+OR+%22Shaktikanta+Das%22)+when:24h'
        self.rss_url = f"https://news.google.com/rss/search?q={self.query}&hl=en-IN&gl=IN&ceid=IN:en"

    def fetch_recent(self) -> list[dict]:
        """Fetches breaking spoken quotes from Finance Minister and RBI Governor."""
        items = []
        try:
            r = cureq.get(self.rss_url, headers=self.headers, impersonate="chrome120", timeout=12)
            if r.status_code == 200:
                soup = BeautifulSoup(r.content, "xml")
                raw_items = soup.find_all("item")
                for item in raw_items:
                    title_elem = item.find("title")
                    link_elem = item.find("link")
                    pubdate_elem = item.find("pubDate")
                    source_elem = item.find("source")
                    
                    title = title_elem.get_text(strip=True) if title_elem else ""
                    link = link_elem.get_text(strip=True) if link_elem else ""
                    pubdate = pubdate_elem.get_text(strip=True) if pubdate_elem else datetime.now(IST).isoformat()
                    origin_media = source_elem.get_text(strip=True) if source_elem else "Financial Wire"
                    
                    if not title:
                        continue
                        
                    # Filter for active spoken commentary or policy actions
                    tl = title.lower()
                    if any(w in tl for w in ["says", "speaks", "warns", "hails", "urges", "plans", "clears", "announces", "flags", "cautions", "rate", "inflation", "growth", "gst", "economy", "visit", "budget"]):
                        entity = "FINANCE MINISTER" if any(k in tl for k in ["finance minister", "nirmala sitharaman", "finmin"]) else "RBI GOVERNOR"
                        
                        items.append({
                            "source": "LIVE_WIRE",
                            "id": f"WIRE_{abs(hash(title))}",
                            "company": entity,
                            "headline": title,
                            "body": f"{title} ({origin_media})",
                            "raw_text": f"{entity}: {title}".strip(),
                            "timestamp": pubdate,
                            "url": link
                        })
        except Exception:
            pass
            
        return items
