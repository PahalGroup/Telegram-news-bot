import re
from datetime import datetime
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup
from curl_cffi import requests as cureq

IST = ZoneInfo("Asia/Kolkata")

class SEBIScraper:
    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        }
        self.url = "https://www.sebi.gov.in/sebiweb/home/HomeAction.do?doListing=yes&sid=6&ssid=23&smid=0"

    def fetch_recent(self) -> list[dict]:
        """Fetches SEBI press releases and regulatory actions."""
        items = []
        try:
            r = cureq.get(self.url, headers=self.headers, impersonate="chrome120", timeout=15)
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "html.parser")
                rows = soup.find_all("tr")
                for row in rows:
                    cols = row.find_all("td")
                    if len(cols) >= 3:
                        date_str = cols[0].get_text(strip=True)
                        pr_no = cols[1].get_text(strip=True)
                        link_tag = cols[2].find("a")
                        title = link_tag.get_text(strip=True) if link_tag else cols[2].get_text(strip=True)
                        href = link_tag.get("href") if link_tag else ""
                        
                        if not title or title.lower() == "title":
                            continue
                            
                        full_url = href if href.startswith("http") else f"https://www.sebi.gov.in{href}" if href else "https://www.sebi.gov.in"
                        full_headline = f"PR {pr_no}: {title}" if pr_no else title
                        
                        items.append({
                            "source": "SEBI",
                            "id": f"SEBI_{pr_no.replace('/', '_') or hash(title)}",
                            "company": "SEBI",
                            "headline": full_headline,
                            "body": title,
                            "raw_text": f"SEBI ({full_headline}): {title}".strip(),
                            "timestamp": date_str or datetime.now(IST).isoformat(),
                            "url": full_url
                        })
        except Exception:
            pass
            
        return items
