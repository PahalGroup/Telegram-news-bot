import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from curl_cffi import requests as cureq

from scripts.pdf_disclosure_reader import PDFDisclosureReader

IST = ZoneInfo("Asia/Kolkata")

class NSEScraper:
    def __init__(self):
        self.session = None
        self.pdf_reader = PDFDisclosureReader(timeout=8)
        self.init_session()

    def init_session(self):
        """Initializes fresh session with NSE cookies."""
        try:
            self.session = cureq.Session(impersonate="chrome120")
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9"
            }
            # NSE requires hitting homepage first
            self.session.get("https://www.nseindia.com", headers=headers, timeout=12)
        except Exception:
            pass

    def fetch_recent(self) -> list[dict]:
        """Fetches corporate announcements from NSE with automatic cookie refresh."""
        items = []
        url = "https://www.nseindia.com/api/corporate-announcements?index=equities"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-announcements",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "en-US,en;q=0.9"
        }
        
        for attempt in range(2):
            try:
                if not self.session:
                    self.init_session()
                r = self.session.get(url, headers=headers, timeout=12)
                if r.status_code in (401, 403):
                    # Cookie expired, refresh
                    self.init_session()
                    continue
                if r.status_code == 200:
                    data = r.json()
                    if isinstance(data, list):
                        for row in data:
                            symbol = (row.get("symbol") or "").strip()
                            company = (row.get("sm_name") or symbol).strip()
                            desc = (row.get("desc") or "").strip()
                            att_file = (row.get("attmntFile") or "").strip()
                            an_dt = (row.get("an_dt") or "").strip()
                            
                            pdf_url = att_file if att_file.startswith("http") else f"https://nsearchives.nseindia.com/corporate/{att_file}" if att_file else ""
                            more = desc
                            
                            # Fast pre-check: Skip downloading PDFs for routine non-material paperwork
                            is_routine = any(r in desc.lower() for r in [
                                "loss of share", "duplicate share", "closure of trading",
                                "newspaper publication", "loss of certificate", "compliance certificate",
                                "kyc", "investor grievance"
                            ])
                            if not is_routine and pdf_url and pdf_url.lower().endswith(".pdf"):
                                try:
                                    pdf_data = self.pdf_reader.extract_from_url(pdf_url)
                                    if pdf_data:
                                        if pdf_data.get("clean_subject"):
                                            desc = pdf_data["clean_subject"]
                                        if pdf_data.get("bullets"):
                                            bullet_str = "\n".join(f"- {b}" for b in pdf_data["bullets"])
                                            more = f"Transaction Details:\n{bullet_str}"
                                        elif pdf_data.get("raw_snippet") and not more:
                                            more = pdf_data["raw_snippet"]
                                except Exception:
                                    pass
                            
                            items.append({
                                "source": "NSE",
                                "id": f"NSE_{symbol}_{an_dt}_{desc[:20]}",
                                "company": company,
                                "symbol": symbol,
                                "headline": desc,
                                "body": more or desc,
                                "raw_text": f"{company} ({symbol}): {desc}\n{more}".strip(),
                                "timestamp": an_dt,
                                "url": pdf_url or f"https://www.nseindia.com/get-quotes/equity?symbol={symbol}"
                            })
                        break
            except Exception:
                self.init_session()
                
        return items
