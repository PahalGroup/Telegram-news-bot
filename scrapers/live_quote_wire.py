import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup
from curl_cffi import requests as cureq

IST = ZoneInfo("Asia/Kolkata")

class LiveQuoteWire:
    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        }
        # Explicit query strictly for Indian Union Finance Minister and sitting RBI Governor
        self.query = '(%22Nirmala+Sitharaman%22+OR+%22Union+Finance+Minister%22+OR+%22Finance+Minister+Nirmala%22+OR+%22RBI+Governor%22+OR+%22Shaktikanta+Das%22)+when:24h'
        self.rss_url = f"https://news.google.com/rss/search?q={self.query}&hl=en-IN&gl=IN&ceid=IN:en"

    def fetch_recent(self) -> list[dict]:
        """Fetches breaking, highly recent (<2h) spoken quotes from Union Finance Minister and sitting RBI Governor."""
        items = []
        now_utc = datetime.now(timezone.utc)
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
                    pubdate = pubdate_elem.get_text(strip=True) if pubdate_elem else ""
                    origin_media = source_elem.get_text(strip=True) if source_elem else "Financial Wire"
                    
                    if not title:
                        continue

                    # 1. Recency Gate: Discard any news older than 2.0 hours
                    if pubdate:
                        try:
                            dt = parsedate_to_datetime(pubdate)
                            age_hours = (now_utc - dt).total_seconds() / 3600.0
                            if age_hours > 2.0:
                                continue
                        except Exception:
                            pass

                    tl = title.lower()
                    
                    # 2. Exclude former governors or historical op-eds
                    if any(ex in tl for ex in ["ex-guv", "former governor", "ex-governor", "past governor", "former rbi", "subbarao", "raghuram rajan", "urjit patel"]):
                        continue

                    # 3. Exclude state finance ministers or state GST updates
                    if any(st in tl for st in ["state finance minister", "tamil nadu", "kerala", "punjab", "bihar", "bengal", "up finmin"]):
                        continue

                    # 4. Exclude third-party letters / petitions to FM
                    if any(bad in tl for bad in ["writes to", "urges fm", "pleads with", "letter to", "petition to", "demands fm"]):
                        continue
                    
                    # 5. Strictly identify entity - must be explicitly in title!
                    entity = None
                    if any(k in tl for k in ["nirmala sitharaman", "union finance minister", "finance minister nirmala"]):
                        entity = "FINANCE MINISTER NIRMALA SITHARAMAN"
                    elif any(k in tl for k in ["rbi governor", "shaktikanta das", "reserve bank governor"]):
                        entity = "RBI GOVERNOR SHAKTIKANTA DAS"
                    else:
                        continue

                    # 6. Validate monetary/market policy verbs (exclude routine travel or general visits)
                    has_policy_verb = any(w in tl for w in ["says", "speaks", "warns", "hails", "urges", "plans", "clears", "announces", "flags", "cautions", "rate", "inflation", "growth", "gst", "economy", "budget", "liquidity", "rupee", "repo"])
                    if not has_policy_verb:
                        continue

                    # 7. Filter out non-financial visits/tributes/ceremonies
                    if any(bad in tl for bad in ["visit odisha", "inaugurate", "inauguration", "condoles", "birthday", "cricket", "memorial"]):
                        continue

                    # Clean headline
                    clean_head = re.sub(r'\s*-\s*(?:The\s+Economic\s+Times|CNBC|Moneycontrol|NDTV|Business\s+Today|Reuters|Bloomberg|News\s+On\s+AIR).*$', '', title, flags=re.IGNORECASE).strip()

                    items.append({
                        "source": "LIVE_WIRE",
                        "id": f"WIRE_{abs(hash(title))}",
                        "company": entity,
                        "headline": clean_head,
                        "body": f"{clean_head} ({origin_media})",
                        "raw_text": f"{entity}: {clean_head}".strip(),
                        "timestamp": pubdate or datetime.now(IST).isoformat(),
                        "url": link
                    })
        except Exception:
            pass
            
        return items

