import re
from datetime import datetime
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup
from curl_cffi import requests as cureq

IST = ZoneInfo("Asia/Kolkata")

class RBIScraper:
    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        }
        self.feeds = [
            ("https://rbi.org.in/pressreleases_rss.xml", "Press Release"),
            ("https://rbi.org.in/notifications_rss.xml", "Notification")
        ]

    def fetch_recent(self) -> list[dict]:
        """Fetches RBI press releases, MPC decisions, and notifications."""
        items = []
        for feed_url, feed_type in self.feeds:
            try:
                r = cureq.get(feed_url, headers=self.headers, impersonate="chrome120", timeout=12)
                if r.status_code == 200:
                    soup = BeautifulSoup(r.content, "xml")
                    raw_items = soup.find_all("item")
                    for item in raw_items:
                        title_elem = item.find("title")
                        link_elem = item.find("link")
                        desc_elem = item.find("description")
                        pubdate_elem = item.find("pubDate")
                        
                        title = title_elem.get_text(strip=True) if title_elem else ""
                        link = link_elem.get_text(strip=True) if link_elem else ""
                        desc = desc_elem.get_text(strip=True) if desc_elem else ""
                        pubdate = pubdate_elem.get_text(strip=True) if pubdate_elem else datetime.now(IST).isoformat()
                        
                        if not title:
                            continue
                            
                        # Unique ID based on PR url or title hash
                        prid_match = re.search(r'prid=(\d+)', link, re.IGNORECASE)
                        rbi_id = prid_match.group(1) if prid_match else str(abs(hash(title)))
                        
                        clean_desc = BeautifulSoup(desc, "html.parser").get_text(separator=" ").strip() if desc else title
                        
                        items.append({
                            "source": "RBI",
                            "id": f"RBI_{rbi_id}",
                            "company": "RESERVE BANK OF INDIA",
                            "headline": title,
                            "body": clean_desc,
                            "raw_text": f"RESERVE BANK OF INDIA: {title}\n{clean_desc}".strip(),
                            "timestamp": pubdate,
                            "url": link or "https://www.rbi.org.in"
                        })
            except Exception:
                pass
                
        return items
