import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from curl_cffi import requests as cureq

from scripts.pdf_disclosure_reader import PDFDisclosureReader

IST = ZoneInfo("Asia/Kolkata")

class BSEScraper:
    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Referer": "https://www.bseindia.com/",
            "Origin": "https://www.bseindia.com",
            "Accept": "application/json, text/plain, */*"
        }
        self.primary_url = "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"
        self.fallback_url = "https://api.bseindia.com/BseIndiaAPI/api/CorpAnn/w"
        self.pdf_reader = PDFDisclosureReader(timeout=8)

    def fetch_recent(self) -> list[dict]:
        """Fetches latest corporate filings from BSE with fallback and resilient headers."""
        items = []
        today = datetime.now(IST).strftime("%Y%m%d")
        
        # 1. Try Primary AnnSubCategoryGetData endpoint
        try:
            url = f"{self.primary_url}?pageno=1&strCat=-1&strPrevDate={today}&strScrip=&strSearch=P&strToDate={today}&strType=C&subcategory="
            r = cureq.get(url, headers=self.headers, impersonate="chrome120", timeout=12)
            if r.status_code == 200:
                data = r.json()
                table = data.get("Table", [])
                for row in table:
                    company = (row.get("SLONGNAME") or "").strip()
                    news_sub = (row.get("NEWSSUB") or "").strip()
                    headline = (row.get("HEADLINE") or "").strip()
                    more = (row.get("MORE") or "").strip()
                    news_dt = (row.get("NEWS_DT") or "").strip()
                    newsid = str(row.get("NEWSID") or row.get("SCRIP_CD") or news_sub)
                    pdf_file = (row.get("ATTACHMENTNAME") or "").strip()
                    
                    pdf_url = f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{pdf_file}" if pdf_file else ""
                    
                    # Extract clean subject from web form
                    subj = news_sub.split(" - ")[-1].strip() if " - " in news_sub else news_sub
                    
                    # Fast pre-check: Skip downloading PDFs for routine non-material paperwork
                    is_routine = any(r in news_sub.lower() for r in [
                        "loss of share", "duplicate share", "closure of trading",
                        "newspaper publication", "loss of certificate", "compliance certificate",
                        "kyc", "investor grievance"
                    ])
                    if not is_routine and pdf_url and pdf_url.lower().endswith(".pdf"):
                        try:
                            pdf_data = self.pdf_reader.extract_from_url(pdf_url)
                            if pdf_data:
                                if pdf_data.get("clean_subject"):
                                    subj = pdf_data["clean_subject"]
                                if pdf_data.get("bullets"):
                                    bullet_str = "\n".join(f"- {b}" for b in pdf_data["bullets"])
                                    more = f"Transaction Details:\n{bullet_str}"
                                elif pdf_data.get("raw_snippet") and not more:
                                    more = pdf_data["raw_snippet"]
                        except Exception:
                            pass
                    
                    items.append({
                        "source": "BSE",
                        "id": f"BSE_{newsid}",
                        "company": company,
                        "headline": headline or subj,
                        "body": more or headline or news_sub,
                        "raw_text": f"{company}: {headline or subj}\n{more}".strip(),
                        "timestamp": news_dt,
                        "url": pdf_url or f"https://www.bseindia.com/stock-share-price/{company}/"
                    })
        except Exception:
            pass

        # 2. Try Fallback CorpAnn/w endpoint if primary returned 0
        if not items:
            try:
                r = cureq.get(self.fallback_url, headers=self.headers, impersonate="chrome120", timeout=12)
                if r.status_code == 200:
                    data = r.json()
                    if isinstance(data, list):
                        for row in data:
                            raw_subj = (row.get("Subject") or "").strip()
                            newsid = str(row.get("Newsid") or raw_subj)
                            if " -" in raw_subj:
                                parts = raw_subj.split(" -", 1)
                                company = parts[0].strip()
                                subj = parts[1].strip()
                            else:
                                company = ""
                                subj = raw_subj
                                
                            items.append({
                                "source": "BSE",
                                "id": f"BSE_{newsid}",
                                "company": company,
                                "headline": subj,
                                "body": raw_subj,
                                "raw_text": f"{company}: {subj}" if company else subj,
                                "timestamp": datetime.now(IST).isoformat(),
                                "url": "https://www.bseindia.com/corporates/ann.html"
                            })
            except Exception:
                pass
                
        return items
