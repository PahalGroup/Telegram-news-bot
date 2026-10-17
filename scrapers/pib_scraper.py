import re
from datetime import datetime
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup
from curl_cffi import requests as cureq

IST = ZoneInfo("Asia/Kolkata")

class PIBScraper:
    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        }
        self.rss_url = "https://pib.gov.in/RssMain.aspx?ModId=6"
        self.target_keywords = [
            "finance", "ccea", "cabinet", "cbdt", "cbic", "gst", "tax", "budget", "customs",
            "economy", "revenue", "fiscal", "disinvestment", "idbi", "banking", "lic",
            "nirmala sitharaman", "finmin", "monetary", "pli"
        ]

    def fetch_recent(self) -> list[dict]:
        """Fetches PIB press releases filtering specifically for Finance, Cabinet & Tax updates."""
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
                    
                    title = title_elem.get_text(strip=True) if title_elem else ""
                    link = link_elem.get_text(strip=True) if link_elem else ""
                    pubdate = pubdate_elem.get_text(strip=True) if pubdate_elem else datetime.now(IST).isoformat()
                    
                    if not title:
                        continue
                        
                    # Filter for Finance / Economic / Cabinet relevance
                    title_lower = title.lower()
                    is_relevant = any(k in title_lower for k in self.target_keywords)
                    
                    # Also include any IDBI / Banking / CCEA / Cabinet approval stories
                    if "आईडीबीआई" in title or "कैबिनेट" in title or "वित्त मंत्रालय" in title or "जीएसटी" in title:
                        is_relevant = True
                        
                    if is_relevant:
                        # Extract PRID
                        prid_match = re.search(r'PRID=(\d+)', link)
                        prid = prid_match.group(1) if prid_match else str(hash(title))
                        
                        items.append({
                            "source": "PIB",
                            "id": f"PIB_{prid}",
                            "company": "GOVERNMENT OF INDIA",
                            "headline": title,
                            "body": title,
                            "raw_text": f"GOVERNMENT OF INDIA (PIB): {title}",
                            "timestamp": pubdate,
                            "url": link
                        })
        except Exception:
            pass
            
        return items
