import time
import re
from curl_cffi import requests
from typing import List, Dict, Any
from datetime import datetime
from zoneinfo import ZoneInfo
import logging

logger = logging.getLogger(__name__)

class BSEFetcher:
    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Referer": "https://www.bseindia.com/",
            "Origin": "https://www.bseindia.com",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
        }
        self.primary_api = "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"
        self.session = requests.Session(impersonate="chrome120")
        self.pdf_cache = {}

    def fetch_announcements(self, skip_pdf: bool = False) -> List[Dict[str, Any]]:
        """Fetch latest corporate announcements from BSE across recent pages."""
        now_ist = datetime.now(ZoneInfo("Asia/Kolkata"))
        today_ist = now_ist.strftime("%Y%m%d")
        all_parsed = []
        seen_ids = set()

        for page in (1, 2, 3):
            payload = {
                "pageno": page,
                "strCat": "-1",
                "strPrevDate": today_ist,
                "strScrip": "",
                "strSearch": "P",
                "strToDate": today_ist,
                "strType": "C",
                "subcategory": "",
            }
            try:
                r = requests.get(self.primary_api, headers=self.headers, params=payload, impersonate="chrome", timeout=12)
                if r.status_code == 200:
                    data = r.json()
                    items = data.get("Table", [])
                    if items:
                        for item in items:
                            company = item.get("SLONGNAME", "").strip()
                            raw_subj = item.get("NEWSSUB", "").strip()
                            headline = item.get("HEADLINE", "").strip()
                            more = item.get("MORE", "").strip()
                            news_dt = item.get("NEWS_DT", "").strip()
                            newsid = str(item.get("NEWSID") or item.get("SCRIP_CD") or raw_subj)

                            if newsid in seen_ids:
                                continue
                            seen_ids.add(newsid)

                            # Freshness check: Skip filings older than 6 hours
                            if news_dt:
                                try:
                                    dt_clean = news_dt.split(".")[0]
                                    f_dt = datetime.fromisoformat(dt_clean).replace(tzinfo=ZoneInfo("Asia/Kolkata"))
                                    if (now_ist - f_dt).total_seconds() > 21600:
                                        continue
                                except Exception:
                                    pass

                            # Extract clean subject from NEWSSUB: "Company - SCRIP - Subject"
                            if " - " in raw_subj:
                                parts = raw_subj.split(" - ")
                                subj = parts[-1].strip()
                            else:
                                subj = raw_subj

                            # Pre-drop check: Do not waste time downloading PDFs for blacklisted paperwork
                            try:
                                from src.filter_engine import FilterEngine
                                pre_check = f"{company} {subj} {raw_subj} {headline}"
                                if FilterEngine.should_drop(pre_check):
                                    continue
                            except Exception:
                                pass

                            att_name = item.get("ATTACHMENTNAME", "")
                            pdf_url = f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{att_name}" if att_name else None

                            # Extract structured Regulation 30 bullets directly from PDF attachment
                            is_target_filing = any(k in f"{subj} {raw_subj} {headline}".lower() for k in [
                                "order", "contract", "results", "directorate", "board meeting",
                                "mou", "acquisition", "expansion", "dividend", "qip", "ipo",
                                "buyback", "financial", "tender", "rating"
                            ])
                            if not skip_pdf and is_target_filing and pdf_url and str(pdf_url).lower().endswith(".pdf"):
                                if att_name in self.pdf_cache:
                                    lead_sum, pdf_bullets = self.pdf_cache[att_name]
                                else:
                                    try:
                                        from sources.annexure_parser import AnnexureParser
                                        lead_sum, pdf_bullets = AnnexureParser.extract_annexure_bullets(pdf_url, self.session)
                                        self.pdf_cache[att_name] = (lead_sum, pdf_bullets)
                                    except Exception as pe:
                                        logger.debug(f"BSE PDF parse error for {att_name}: {pe}")
                                        lead_sum, pdf_bullets = "", []

                                if pdf_bullets:
                                    formatted_b = [f"• {b}" if not b.startswith(('•', '-', '▪️')) else b for b in pdf_bullets]
                                    if lead_sum:
                                        more = f"{lead_sum}\n" + "\n".join(formatted_b)
                                    else:
                                        more = "\n".join(formatted_b)

                            all_parsed.append({
                                "company": company,
                                "subject": subj,
                                "raw_subject": raw_subj,
                                "headline": headline,
                                "more": more,
                                "news_dt": news_dt,
                                "newsid": newsid,
                                "pdf_url": pdf_url
                            })
            except Exception as e:
                logger.warning(f"BSE primary page {page} failed ({e})")
                break

        return all_parsed
