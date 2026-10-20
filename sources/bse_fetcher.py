import time
from curl_cffi import requests
from typing import List, Dict, Any
from datetime import datetime
from zoneinfo import ZoneInfo
import logging

logger = logging.getLogger(__name__)

class BSEFetcher:
    def __init__(self):
        self.session = requests.Session(impersonate="chrome120")
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Referer": "https://www.bseindia.com/",
            "Origin": "https://www.bseindia.com",
            "Accept": "application/json, text/plain, */*",
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
        }
        self.primary_api = "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"
        self.fallback_url = "https://api.bseindia.com/BseIndiaAPI/api/CorpAnn/w"
        self.pdf_cache = {}

    def fetch_announcements(self, skip_pdf: bool = True) -> List[Dict[str, Any]]:
        """Fetch latest corporate announcements from BSE across recent pages."""
        today = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y%m%d")
        now_ist = datetime.now(ZoneInfo("Asia/Kolkata"))
        all_parsed = []
        seen_ids = set()

        for page in (1, 2, 3):
            ts = int(time.time() * 1000)
            url = f"{self.primary_api}?pageno={page}&strCat=-1&strPrevDate={today}&strScrip=&strSearch=P&strToDate={today}&strType=C&_ts={ts}"
            try:
                r = self.session.get(url, headers=self.headers, timeout=10)
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

                            # Freshness check: Skip filings older than 45 minutes
                            if news_dt:
                                try:
                                    dt_clean = news_dt.split(".")[0]
                                    f_dt = datetime.fromisoformat(dt_clean).replace(tzinfo=ZoneInfo("Asia/Kolkata"))
                                    # Freshness check: Skip filings older than 3 hours (captures full evening session)
                                    if (now_ist - f_dt).total_seconds() > 10800:
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

                            # Extract structured Regulation 30 bullets directly from PDF attachment
                            att_name = item.get("ATTACHMENTNAME", "")
                            if not skip_pdf and att_name and att_name.lower().endswith(".pdf"):
                                if att_name in self.pdf_cache:
                                    lead_sum, pdf_bullets = self.pdf_cache[att_name]
                                else:
                                    from sources.annexure_parser import AnnexureParser
                                    pdf_url = f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{att_name}"
                                    lead_sum, pdf_bullets = AnnexureParser.extract_annexure_bullets(pdf_url, self.session)
                                    self.pdf_cache[att_name] = (lead_sum, pdf_bullets)

                                if pdf_bullets:
                                    formatted_b = [f"- {b}" if not b.startswith(('-', '•')) else b for b in pdf_bullets]
                                    if lead_sum:
                                        more = f"{lead_sum}\n\n" + "\n\n".join(formatted_b)
                                    else:
                                        more = "\n\n".join(formatted_b)

                            all_parsed.append({
                                "company": company,
                                "subject": subj,
                                "raw_subject": raw_subj,
                                "headline": headline,
                                "more": more,
                                "news_dt": news_dt,
                                "newsid": newsid
                            })
            except Exception as e:
                logger.warning(f"BSE primary page {page} failed ({e})")
                break

        if all_parsed:
            return all_parsed

    def extract_pdf_highlights(self, attachment_name: str) -> List[str]:
        """Downloads exchange PDF disclosure and extracts key metrics/bullets."""
        if not attachment_name:
            return []
        url = f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{attachment_name}"
        try:
            r = self.session.get(url, headers=self.headers, timeout=6)
            if r.status_code == 200 and len(r.content) > 1000:
                import pymupdf
                doc = pymupdf.open(stream=r.content, filetype="pdf")
                full_text = ""
                for page in doc[:3]:
                    full_text += page.get_text() + "\n"
                
                bullets = []
                for line in full_text.splitlines():
                    cl = line.strip()
                    if not cl:
                        continue
                    if cl.startswith(('•', '-', '*', '–')) or re.search(r'\b(?:orders? of|valued at|worth|growth of|investments? of|awarded|cross(?:ed)?)\b', cl, re.I):
                        cleaned_b = re.sub(r'^[•\-\*–\s]+', '', cl).strip()
                        if 25 < len(cleaned_b) < 140 and not cleaned_b.startswith('http'):
                            bullets.append(f"- {cleaned_b}")
                return bullets[:5]
        except Exception as e:
            logger.debug(f"PDF extract error for {attachment_name}: {e}")
        return []

        # Fallback to simple CorpAnn/w
        try:
            r = self.session.get(self.fallback_url, headers=self.headers, timeout=10)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list):
                    parsed_items = []
                    for item in data:
                        raw_subj = item.get("Subject", "")
                        newsid = item.get("Newsid", "")
                        if " -" in raw_subj:
                            parts = raw_subj.split(" -", 1)
                            company = parts[0].strip()
                            subj = parts[1].strip()
                        else:
                            company = ""
                            subj = raw_subj.strip()

                        parsed_items.append({
                            "company": company,
                            "subject": subj,
                            "raw_subject": raw_subj,
                            "headline": "",
                            "more": "",
                            "news_dt": "",
                            "newsid": newsid
                        })
                    return parsed_items
        except Exception as e:
            logger.error(f"Error fetching BSE announcements fallback: {e}")

        return []
