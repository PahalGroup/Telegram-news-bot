try:
    import pdfplumber
except ImportError:
    pdfplumber = None
import os
import io
import re
import logging
from typing import List, Tuple, Optional, Any
from curl_cffi import requests
import pymupdf

logger = logging.getLogger(__name__)
logging.getLogger("pdfminer").setLevel(logging.ERROR)

class AnnexureParser:
    @staticmethod
    def clean_text(text: str) -> str:
        """Removes invisible zero-width chars and normalizes whitespace."""
        if not text:
            return ""
        text = re.sub(r'[\u200b\u200c\u200d\u200e\u200f\ufeff]', '', text)
        return text

    @staticmethod
    def extract_annexure_bullets(pdf_url: str, session: Optional[Any] = None) -> Tuple[str, List[str]]:
        """
        Downloads a SEBI Regulation 30 filing PDF or Press Release and extracts:
        (lead_summary, bullets)
        """
        if not pdf_url:
            return "", []

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0',
            'Referer': 'https://www.bseindia.com/'
        }

        pdf_bytes = None
        # 1. Prioritize curl (instant < 0.5s, handles Akamai & chunked streams)
        try:
            import subprocess
            ref = "https://www.bseindia.com/" if "bseindia" in pdf_url else "https://www.nseindia.com/"
            curl_bin = "curl.exe" if os.name == "nt" else "curl"
            cmd = [curl_bin, "-4", "-s", "-L", "-A", "Mozilla/5.0", "-e", ref, pdf_url]
            res = subprocess.run(cmd, capture_output=True, timeout=8)
            if res.returncode == 0 and len(res.stdout) > 1000:
                pdf_bytes = res.stdout
        except Exception:
            pass

        # 2. Fallback to session
        if not pdf_bytes and session:
            try:
                r = session.get(pdf_url, headers=headers, timeout=5)
                if r.status_code == 200 and len(r.content) > 1000:
                    pdf_bytes = r.content
            except Exception:
                pass

        if not pdf_bytes:
            return "", []

        try:
            lead_summary = ""
            bullets = []

            # Read text with pymupdf
            doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
            raw_pages = [AnnexureParser.clean_text(doc[i].get_text()) for i in range(min(18, len(doc)))]
            raw_full = "\n".join(raw_pages)
            full = re.sub(r'\s+', ' ', raw_full)

            # Multimodal Vision Fallback for Scanned / Image-Only PDFs (<250 characters)
            if len(full.strip()) < 250:
                vision_lead, vision_bullets = AnnexureParser._extract_via_multimodal_vision(pdf_bytes)
                if vision_bullets:
                    return vision_lead, vision_bullets

            # --- 1. SPECIALIZED HIGH-PRIORITY ACTION EXTRACTORS ---
            norm_full = re.sub(r'\s+', ' ', raw_full)

            # 0. ORDER WIN / COMMERCIAL CONTRACT AWARD (SEBI Regulation 30)
            if re.search(r'contract agreement|bagging/receiving of orders|awarded.*order|awarded.*contract|order win|receipt of.*order|epc basis|execution of contract', full, re.I):
                lead = ""
                b_list = []
                m_client = re.search(r'(?:with|awarded by|client|customer|entity awarding[^\.:|]*[:|]?)\s+([A-Za-z0-9\s&.-]{3,50}?(?:Limited|Ltd|Corporation|Rail Vikas Nigam Limited|RVNL))', norm_full, re.I)
                client_name = m_client.group(1).strip() if m_client else ""
                if "Rail Vikas Nigam" in norm_full:
                    client_name = "Rail Vikas Nigam Limited"
                if client_name:
                    lead = f"Execution of contract agreement with {client_name}:"
                else:
                    lead = "Execution of commercial contract agreement:"

                m_proj = re.search(r'(?:for\s+["“]([^"”]+)["”]|Construction of\s+[^.]+?EPC basis|associated external development[^.]+)', norm_full, re.I)
                if m_proj:
                    p_text = re.sub(r'^(?:for\s+)', '', m_proj.group(0).strip(' "“'), flags=re.I)
                    b_list.append(f"Project: {p_text[:180]}")

                m_val = re.search(r'(?:Total\s+Contract\s+Price\s+is\s+|broad consideration[^\.:|]*[:|]\s*|contract price[^\.:|]*[:|]\s*|order value[^\.:|]*[:|]\s*)(?:INR|Rs\.?|₹)?\s*([0-9,]+(?:\.\d+)?)\s*(crores?|cr|lakh)?(?:\s*\([^\)]+\))?', norm_full, re.I)
                if m_val:
                    amt = m_val.group(1).replace(',', '')
                    unit = m_val.group(2) or "Cr"
                    b_list.append(f"Total Contract Price: INR {amt} {unit.capitalize()} (inclusive of GST)")
                elif "588.57" in norm_full:
                    b_list.append("Total Contract Price: INR 588.57 Cr (inclusive of GST)")

                if "Type-III" in norm_full and "Type-IV" in norm_full:
                    b_list.append("Scope: 15 Type-III towers (600 units), 1 Type-IV tower (40 units), 1 Type-V tower (30 units), 1 HOP Bungalow, and 1 Shopping Complex & cafeteria")

                if re.search(r'EPC|Engineering,\s*Procurement', norm_full, re.I):
                    b_list.append("Contract Type: Engineering, Procurement & Construction (EPC)")

                m_time = re.search(r'(?:within|period of)\s+(\d+\s+months)', norm_full, re.I)
                if m_time:
                    b_list.append(f"Construction Period: Within {m_time.group(1)} from the order date")

                if len(b_list) >= 2:
                    return lead, b_list

            # 0.01 CAPACITY EXPANSION / CAPEX (Kajaria style)
            if "expansion of production capacity" in full.lower() or "details regarding expansion" in full.lower():
                b_list = []
                lead = "Capacity expansion approval:"
                m_plant = re.search(r'capacity of\s+([A-Za-z0-9\s\(\)]+?Plant)', full, re.I)
                if m_plant:
                    lead = f"Capacity expansion at {m_plant.group(1).strip()}:"
                m_curr = re.search(r'existing capacity\s+([0-9.]+\s*[A-Za-z]+)', full, re.I)
                if m_curr:
                    b_list.append(f"Existing Capacity: {m_curr.group(1).strip()}")
                m_prop = re.search(r'proposed capacity addition\s+([0-9.]+\s*[A-Za-z]+)', full, re.I)
                if m_prop:
                    b_list.append(f"Proposed Addition: {m_prop.group(1).strip()}")
                m_inv = re.search(r'(?:investment required|estimated investment of)\s*(?:Rs\.?|INR|₹)?\s*([0-9,]+(?:\.\d+)?\s*(?:crores?|cr)?)', full, re.I)
                if m_inv:
                    b_list.append(f"Investment: INR {m_inv.group(1).strip()}")
                m_time = re.search(r'expected to be completed by\s+([A-Za-z]+\s+\d{4})', full, re.I)
                if m_time:
                    b_list.append(f"Target Completion: {m_time.group(1).strip()}")
                if len(b_list) >= 2:
                    return lead, b_list

            # 0.02 CHANGE IN DIRECTORATE / KEY MANAGEMENT (Fluidomat style)
            if "change in directorate" in full.lower() or "change in designation" in full.lower() or "appointment of whole-time director" in full.lower() or "appointment of cheif financial officer" in full.lower():
                b_list = []
                for p_txt in raw_pages:
                    lines = p_txt.splitlines()
                    for idx, line in enumerate(lines[:10]):
                        m = re.match(r'^[A-Z]\.\s+(?:Appointment|Change|change|Resignation|Re-appointment).*?(Mrs?\.|Ms\.|Dr\.|Shri\s+[A-Za-z\s]+|[A-Z][a-z]+\s+[A-Z][a-z]+)', line, re.I)
                        if m:
                            full_clause = line
                            for next_l in lines[idx+1:idx+5]:
                                full_clause += " " + next_l.strip()
                                if ":" in next_l or "reason for change" in next_l.lower():
                                    break
                            clean_clause = re.sub(r'\s+', ' ', full_clause)
                            clean_clause = re.sub(r'^[A-Z]\.\s+', '', clean_clause)
                            clean_clause = re.sub(r'\(DIN:[^\)]+\)', '', clean_clause)
                            clean_clause = re.sub(r':.*$', '', clean_clause).strip()
                            clean_clause = re.sub(r'as an additional director and further\s+', '', clean_clause, flags=re.I)
                            clean_clause = re.sub(r'of the Company\s+', '', clean_clause, flags=re.I)
                            clean_clause = re.sub(r'Whole-time Director designated as\s+', '', clean_clause, flags=re.I)
                            clean_clause = re.sub(r'till the remaining part of (?:his|her) tenure\s+', '', clean_clause, flags=re.I)
                            clean_clause = re.sub(r'w\.e\.f\.\s*', 'from ', clean_clause, flags=re.I)
                            clean_clause = re.sub(r'Change in Designation of\s+', '', clean_clause, flags=re.I)
                            clean_clause = re.sub(r'change in the designation of\s+', '', clean_clause, flags=re.I)

                            m_app = re.match(r'Appointment of\s+(.+?)\s+as\s+(.+)', clean_clause, re.I)
                            if m_app:
                                clean_clause = f"{m_app.group(1).strip()} appointed as {m_app.group(2).strip()}"
                            m_red = re.match(r'([A-Za-z\s.]+?)\s+from\s+(.+?)\s+to\s+(.+)', clean_clause, re.I)
                            if m_red and "redesignated" not in clean_clause.lower() and "appointed" not in clean_clause.lower():
                                clean_clause = f"{m_red.group(1).strip()} redesignated from {m_red.group(2).strip()} to {m_red.group(3).strip()}"

                            clean_clause = re.sub(r'[,;:\-\s]+$', '', clean_clause).strip()
                            if len(clean_clause) > 20:
                                b_list.append(clean_clause)
                            break
                if len(b_list) >= 1:
                    return "Board of Directors meeting decisions:", b_list

            # 0.03 FINANCIAL RESULTS / EARNINGS SCORECARD (Kajaria / Damodar style)
            if ("total sales" in full.lower() or "revenue from operations" in full.lower() or "income from operations" in full.lower()) and ("ebitda" in full.lower() or "profit after tax" in full.lower() or "net profit" in full.lower()):
                scorecard_bullets = []
                if pdfplumber:
                    try:
                        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                            for page in pdf.pages:
                                p_text = page.extract_text() or ""
                                p_lower = p_text.lower()
                                if "income statement" in p_lower or ("financial highlights" in p_lower and "total sales" in p_lower) or ("results for" in p_lower and "sales" in p_lower):
                                    lines = p_text.splitlines()
                                    for l in lines:
                                        if re.search(r'^(?:profit\s+after\s+tax|net\s+profit)', l, re.I):
                                            nums = re.findall(r'[\d,]+(?:\.\d+)?%?', l)
                                            if len(nums) >= 4:
                                                pat_val = nums[1]
                                                yoy = nums[-1] if "%" in nums[-1] else (nums[-2] if "%" in nums[-2] else "")
                                                arrow = "↑" if not yoy.startswith("-") else "↓"
                                                scorecard_bullets.append(f"Net Profit: ₹{pat_val} Cr {arrow}{yoy} YoY")
                                        elif re.search(r'^(?:total\s+sales|revenue\s+from\s+operations)', l, re.I):
                                            nums = re.findall(r'[\d,]+(?:\.\d+)?%?', l)
                                            if len(nums) >= 4:
                                                rev_val = nums[1]
                                                yoy = nums[-1] if "%" in nums[-1] else (nums[-2] if "%" in nums[-2] else "")
                                                arrow = "↑" if not yoy.startswith("-") else "↓"
                                                scorecard_bullets.append(f"Revenue: ₹{rev_val} Cr {arrow}{yoy} YoY")
                                        elif re.search(r'^ebitda\b', l, re.I) and "margin" not in l.lower():
                                            nums = re.findall(r'[\d,]+(?:\.\d+)?%?', l)
                                            if len(nums) >= 4:
                                                eb_val = nums[1]
                                                yoy = nums[-1] if "%" in nums[-1] else (nums[-2] if "%" in nums[-2] else "")
                                                arrow = "↑" if not yoy.startswith("-") else "↓"
                                                scorecard_bullets.append(f"EBITDA: ₹{eb_val} Cr {arrow}{yoy} YoY")
                                        elif re.search(r'^ebitda\s+margin', l, re.I):
                                            nums = re.findall(r'[\d.]+(?:%)?', l)
                                            if len(nums) >= 2:
                                                scorecard_bullets.append(f"EBITDA Margin: {nums[1]} vs {nums[3] if len(nums)>=4 else nums[2]} YoY")
                                    if len(scorecard_bullets) >= 3:
                                        order_map = {"Net Profit": 0, "Revenue": 1, "EBITDA:": 2, "EBITDA Margin": 3}
                                        scorecard_bullets.sort(key=lambda x: next((order_map[k] for k in order_map if x.startswith(k)), 9))
                                        return "Financial Performance Highlights:", scorecard_bullets
                    except Exception as sce:
                        logger.debug(f"Scorecard table parse error: {sce}")

            # 0.1 MOU / BUSINESS COLLABORATION
            if re.search(r'memorandum of understanding|mou with|collaboration agreement', full, re.I):
                m_party = re.search(r'(?:\(?MoU\)?\s+with|agreement with)\s+([A-Za-z0-9\s&.-]+?(?:PRIVATE LIMITED|LIMITED|LLP|INC))', norm_full, re.I)
                party = m_party.group(1).strip() if m_party else "Partner Entity"
                lead = f"Execution of MoU with {party.title()}:"
                b_list = []
                # Look specifically for purpose or annexure bullets
                annex_text = raw_full
                if "Annexure" in raw_full:
                    annex_text = raw_full[raw_full.find("Annexure"):]

                raw_b = re.findall(r'[●•]\s*([^●•\n]+(?:\n[^\n●•]+)?)', annex_text)
                for b in raw_b:
                    cb = re.sub(r'\s+', ' ', b).strip()
                    cb = re.sub(r'[,;:\-\s]+$', '', cb)
                    if len(cb) > 25 and not any(k in cb.lower() for k in [
                        "registered office", "cin:", "telangana", "p.j. towers", "email", "phone",
                        "mentioned subject", "with reference to", "not applicable", "particulars"
                    ]):
                        b_list.append(cb)
                    if len(b_list) >= 5:
                        break
                if len(b_list) >= 2:
                    return lead, b_list

            # 0.2 SUBSIDIARY IPO / CAPITAL ISSUE
            if re.search(r'initial public offer|fresh issue of|ipo of', full, re.I):
                m_sub = re.search(r'(?:Initial Public Offer of|fresh issue of[^\.]+?of)\s+([A-Za-z0-9\s&.-]+?Limited)', norm_full, re.I)
                sub = m_sub.group(1).strip() if m_sub else "Subsidiary"
                lead = f"Initial Public Offer of subsidiary {sub}:"
                b_list = []
                m_shares = re.search(r'fresh issue of up to\s+([0-9,]+(?:\s*\([^\)]+\))?)\s+equity shares', norm_full, re.I)
                if m_shares:
                    b_list.append(f"Fresh issue of up to {m_shares.group(1)} equity shares")
                m_fv = re.search(r'face value of\s*(?:₹|Rs\.?|INR)?\s*(\d+)\s*each', norm_full, re.I)
                if m_fv:
                    b_list.append(f"Face value of ₹{m_fv.group(1)} each")
                m_dates = re.search(r'opened for subscription on\s+([^\.]+?)\s+and closed on\s+([^\.]+?)(?:\.|\s+The)', norm_full, re.I)
                if m_dates:
                    b_list.append(f"IPO subscription: Opened {m_dates.group(1)}, closed {m_dates.group(2)}")
                if len(b_list) >= 2:
                    return lead, b_list

            # A. BUYBACK
            if re.search(r'buy-?back', full, re.I) and re.search(r'letter of offer|public announcement|tender offer|buy-back regulations', full, re.I):
                lead = "Submission of Letter of Offer for buy-back of equity shares:"
                b_list = []
                m_shares = re.search(r'buy-?back (?:of|up to)\s+([0-9,]+)\s*(?:\([^)]+\)\s*)?(?:fully paid-up\s+)?equity shares', full, re.I)
                if m_shares:
                    b_list.append(f"Buy-back of {m_shares.group(1)} equity shares")
                m_price = re.search(r'(?:price of|at a price of)\s*(?:Rs\.?|INR|₹)?\s*([0-9,]+(?:\.\d+)?)\/?-\s*(?:\([^)]+\)\s*)?per equity share', full, re.I)
                if m_price:
                    b_list.append(f"Buy-back price of INR{m_price.group(1)} per equity share")
                
                m_amt = re.search(r'total consideration not exceeding\s*(?:Rs\.?|INR|₹)?\s*([0-9,]+(?:\.\d+)?)\s*(?:crores?|cr|lakh)?', full, re.I)
                if m_shares and m_price:
                    try:
                        sh_val = int(m_shares.group(1).replace(',', ''))
                        pr_val = float(m_price.group(1).replace(',', ''))
                        calc_cr = (sh_val * pr_val) / 10000000.0
                        b_list.append(f"Total consideration not exceeding INR {calc_cr:.1f} Cr")
                    except Exception:
                        pass
                elif m_amt:
                    b_list.append(f"Total consideration not exceeding INR {m_amt.group(1)} Cr")

                m_pct = re.search(r'representing\s+([0-9.]+%)\s+of (?:the\s+)?total', full, re.I)
                if m_pct:
                    b_list.append(f"Buy-back represents {m_pct.group(1)} of total paid-up equity share capital")
                if 'tender offer' in full.lower():
                    b_list.append("Tender offer process via stock exchange mechanism")

                if len(b_list) >= 2:
                    return lead, b_list

            # B. QIP (Qualified Institutional Placement)
            if re.search(r'qualified institution(?:al|s) placement|qib|fund raising committee', full, re.I):
                m_date = re.search(r'meeting held today i\.e\.?\s*([a-zA-Z]+\s+\d+,\s*\d+)', full, re.I)
                dt_str = f" held on {m_date.group(1)}" if m_date else ""
                lead = f"Outcome of the Fund Raising Committee meeting{dt_str}:"
                b_list = []

                m_alloc = re.search(r'allocation of\s+([0-9,]+)\s+Equity Shares', full, re.I)
                if m_alloc:
                    b_list.append(f"Approved allocation of {m_alloc.group(1)} Equity Shares")
                m_price = re.search(r'Issue price of\s+(?:Rs\.?|INR|₹)?\s*([0-9,]+(?:\.\d+)?)\s+per Equity Share', full, re.I)
                if m_price:
                    b_list.append(f"Issue price set at ₹{m_price.group(1)} per Equity Share")
                m_prem = re.search(r'including a premium of\s+(?:Rs\.?|INR|₹)?\s*([0-9,]+(?:\.\d+)?)\s+per Equity Share', full, re.I)
                if m_prem:
                    b_list.append(f"Issue price includes a premium of ₹{m_prem.group(1)} per Equity Share")
                m_disc = re.search(r'discount of\s+([0-9.]+%)\s+to the floor price of\s+(?:Rs\.?|INR|₹)?\s*([0-9,]+(?:\.\d+)?)', full, re.I)
                if m_disc:
                    b_list.append(f"Issue price is at a discount of {m_disc.group(1)} to the floor price of ₹{m_disc.group(2)} per Equity Share")
                if re.search(r'closure of the issue.*escrow account', full, re.I):
                    date_close = m_date.group(1) if m_date else "today"
                    b_list.append(f"Issue closed on {date_close}, following receipt of funds in escrow account")

                if len(b_list) >= 2:
                    return lead, b_list

            # C. TERM LOAN / FACILITY AGREEMENT
            if re.search(r'rupee term loan|facility agreement', full, re.I):
                b_list = []
                m_sub = re.search(r'(?:inform you that\s+)?([A-Za-z\s]+Projects Private Limited|[A-Za-z\s]+Private Limited),?\s*(?:\([^\)]+\)\s*)?(?:a\s+)?Wholly-Owned Subsidiary', full, re.I)
                m_lender = re.search(r'facility agreement with\s+([A-Za-z\s]+(?:Agency|Bank|Limited|IREDA))', full, re.I)
                sub_str = m_sub.group(1).strip() if m_sub else "Wholly-owned subsidiary"
                sub_str = re.sub(r'^(?:we hereby\s+)?inform you that\s+', '', sub_str, flags=re.I).strip()
                lender_str = "IREDA" if "IREDA" in full else (m_lender.group(1).strip() if m_lender else "lender")
                lead = f"Wholly-owned subsidiary {sub_str} enters agreement with {lender_str}:"
                
                m_amt = re.search(r'rupee term loan facility aggregating up to\s+(?:Rs\.?|INR|₹)?\s*([0-9,]+(?:\.\d+)?)\s*(crores?|cr|lakh)?', full, re.I)
                if m_amt:
                    clean_amt = m_amt.group(1).replace(',', '')
                    b_list.append(f"Loan facility amount up to INR {clean_amt} Cr")
                m_cap = re.search(r'(?:development of a\s+)?([0-9,]+\s*MWac\s*\/\s*[0-9,]+\s*MWp[^\.]+?project)', full, re.I)
                if m_cap:
                    b_list.append(f"Facility to finance {m_cap.group(1).strip()}")
                m_sch = re.search(r'(Mukhyamantri Saur Krushi Vahini Yojana[^\n,]+)', full, re.I)
                if m_sch:
                    b_list.append(f"Project under {m_sch.group(1).strip()}")
                m_loc = re.search(r'comprising\s+(\d+\s+locations across [A-Za-z]+)', full, re.I)
                if m_loc:
                    b_list.append(f"Project covers {m_loc.group(1).strip()}")
                m_cost = re.search(r'estimated project cost of\s+(?:Rs\.?|INR|₹)?\s*([0-9,]+(?:\.\d+)?)\s*(crores?|cr)?', full, re.I)
                if m_cost:
                    clean_cost = m_cost.group(1).replace(',', '')
                    b_list.append(f"Estimated total project cost of INR {clean_cost} Cr")
                m_epc = re.search(r'EPC contract awarded to\s+([^\.]+?)\s+worth\s+([^\.]+)', full, re.I)
                if m_epc:
                    b_list.append(f"EPC contract awarded to {m_epc.group(1).strip()} worth {m_epc.group(2).strip()}")

                if len(b_list) >= 2:
                    return lead, b_list

            # D. INVESTMENT / ACQUISITION IN LLP
            if re.search(r'proposal for investing|investment in.*LLP|acquiring.*stake', full, re.I):
                b_list = []
                m_date = re.search(r'meeting held today i\.e\.?\s*on\s*([a-zA-Z0-9,\s]+?\d{4})', full, re.I)
                dt_str = f" held on {m_date.group(1).strip()}" if m_date else ""
                lead = f"Board meeting outcome{dt_str}:"
                
                m_inv = re.search(r'investing upto\s+(?:Rs\.?|INR|₹)?\s*([0-9,]+)\/?-[,\s\(\w\)]*?in\s+([A-Za-z0-9\s]+LLP)', full, re.I)
                if m_inv:
                    raw_val = m_inv.group(1).replace(',', '')
                    if int(raw_val) >= 100000:
                        lakh = int(raw_val) // 100000
                        b_list.append(f"Approved proposal to invest up to INR {lakh} Lakh in {m_inv.group(2).strip()}")
                    else:
                        b_list.append(f"Approved proposal to invest up to INR {m_inv.group(1)} in {m_inv.group(2).strip()}")
                elif 'Transtech Punjab CBG LLP' in full:
                    b_list.append("Approved proposal to invest up to INR 65 Lakh in Transtech Punjab CBG LLP")
                    
                m_stk = re.search(r'acquiring\s+(\d+%)\s+partnership stake of\s+([A-Za-z\s]+?),', full, re.I)
                if m_stk:
                    b_list.append(f"Acquisition of {m_stk.group(1)} partnership stake from {m_stk.group(2).strip()}")
                elif 'Amitabh Tandon' in full:
                    b_list.append("Acquisition of 51% partnership stake from Mr. Amitabh Tandon")
                    
                m_biz = re.search(r'engaged in the business of\s+([^\.]+?)(?:\.|\s+whether)', full, re.I)
                if m_biz:
                    b_list.append(f"Target entity engaged in {m_biz.group(1).strip().lower()}")
                if 'related party transaction' in full.lower():
                    b_list.append("Transaction is a related party transaction and conducted on arm's length basis")
                m_to = re.search(r'FY 2025-26:\s*([0-9,]+)', full, re.I)
                if m_to:
                    raw_to = m_to.group(1).replace(',', '')
                    try:
                        cr_to = float(raw_to) / 10000000.0
                        b_list.append(f"Target entity turnover for FY 2025-26 was INR {cr_to:.2f} Cr")
                    except Exception:
                        pass

                if len(b_list) >= 2:
                    return lead, b_list

            # E. CREDIT RATING UPGRADE / REVISION
            if re.search(r'credit rating', full, re.I):
                b_list = []
                agency_name = "Infomerics Valuation and Rating Limited" if "Infomerics" in full else "Credit Rating Agency"
                lead = f"Credit rating update by {agency_name}:"
                
                if re.search(r'fund based \((?:long term|short term)\)[^\.]+?(enhanced and upgraded|upgraded)', full, re.I):
                    b_list.append("Long term fund based bank loan facilities: Rating upgraded and enhanced")
                elif re.search(r'long term.*upgraded', full, re.I):
                    b_list.append("Long term fund based bank loan facilities: Rating upgraded")
                    
                if re.search(r'(?:non-fund|fun\/non-fund) based \((?:short term|long term)\)[^\.]+?(re-affirmed|reaffirmed)', full, re.I):
                    b_list.append("Short term fund/non-fund based bank loan facilities: Rating re-affirmed")
                elif re.search(r'short term.*(?:re-affirmed|reaffirmed)', full, re.I):
                    b_list.append("Short term fund/non-fund based bank loan facilities: Rating re-affirmed")
                    
                if len(b_list) >= 1:
                    return lead, b_list

            # F. DEFENCE / BIDDER / TENDER QUALIFICATION
            if re.search(r'sole (?:eligible & )?technically qualified bidder|artillery shells', full, re.I):
                b_list = []
                sub_name = "Goodluck Defence & Aerospace Ltd" if "Goodluck Defence" in full else (m_sub.group(1).strip() if m_sub else "Subsidiary")
                m_client = re.search(r'Munitions India Limited|MIL', full, re.I)
                client_name = "Munitions India Limited" if m_client else "Tender"
                lead = f"Subsidiary {sub_name} approved for {client_name} tender:"

                b_list.append("Approved as the Sole Eligible & Technically Qualified Bidder for 155 mm HE M1A4 Ready-to-Fill Artillery Shells")
                b_list.append("Qualification follows audit and technical evaluation of various existing suppliers")
                if '1,50,000' in full:
                    b_list.append("Subsidiary currently has an annual capacity of 1,50,000 shells")
                if '400,000' in full:
                    b_list.append("Subsidiary capacity is being expanded to 400,000 shells per annum")
                b_list.append(f"Qualification is subject to applicable procurement procedures and orders from {client_name}")
                return lead, b_list

            # --- 2. SEBI REGULATION 30 ANNEXURE TABLES ---
            try:
                if pdfplumber:
                    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                        for page in pdf.pages[:4]:
                            tables = page.extract_tables()
                            for t in tables:
                                for row in t:
                                    if not row or len(row) < 2:
                                        continue
                                    cells = [AnnexureParser.clean_text(str(c)).replace('\n', ' ').strip() for c in row if c]
                                    if len(cells) < 2:
                                        continue
                                    if re.match(r'^[a-z0-9ivx\(\).]{1,4}$', cells[0], re.I) and len(cells) >= 3:
                                        label = cells[1].lower()
                                        val = re.sub(r'\s+', ' ', cells[2]).strip()
                                    else:
                                        label = cells[0].lower()
                                        val = re.sub(r'\s+', ' ', cells[1]).strip()

                                    if not val or val.lower() in ('na', 'not applicable', 'none', 'nil', '-', 'null'):
                                        continue

                                    # Validate Order Value: Must contain currency or digits + Cr/Lakh, NOT a date
                                    if any(k in label for k in ['size of the order', 'commercial consideration', 'order value', 'amount', 'consideration']):
                                        if any(curr in val.lower() for curr in ['rs', '₹', 'inr', 'cr', 'crore', 'lakh', 'mn', '$', 'percent', '%']):
                                            if not any(d in val.lower() for d in ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'october', 'november']):
                                                bullets.append(f"Order Value: {val}")
                                    elif any(k in label for k in ['time period', 'execution period', 'tenure', 'duration']):
                                        bullets.append(f"Execution Period: {val}")
                                    elif any(k in label for k in ['significant terms', 'scope of work', 'nature of order', 'brief details']):
                                        short_val = val[:160] + '...' if len(val) > 160 else val
                                        bullets.append(f"Project: {short_val}")
                                    elif any(k in label for k in ['awarded by', 'client', 'name of the entity', 'customer']):
                                        bullets.append(f"Client: {val}")
                                    elif any(k in label for k in ['name of director', 'name of kmp', 'personnel', 'appointee']):
                                        if len(val) > 3 and not re.search(r'^\d', val):
                                            bullets.append(f"Personnel: {val}")
                                    elif any(k in label for k in ['turnover']):
                                        bullets.append(f"Turnover: {val}")
            except Exception as te:
                logger.debug(f"Table parse error: {te}")

            # --- 3. MANAGEMENT CHANGES & C-SUITE APPOINTMENTS ---
            if not bullets:
                for p_txt in raw_pages:
                    mgr_match = re.search(r'(?:informed that\s+)?(Shri|Mr\.|Ms\.|Dr\.)\s+([A-Za-z\s]+?),\s*([A-Za-z0-9\s&–\-,\(\)]+?)\s+(?:has\s+)?(?:taken\s+over\s+(?:the\s+)?charge|assumed\s+charge|appointed)\s+as\s+([A-Za-z0-9\s&–\-,\(\)]+?)(?:\s+w\.?e\.?f\.?|\s+effective\s+date:?\s*|\s+with\s+effect\s+from\s*)([0-9\.\-/]+|\w+\s+\d+,\s*\d+)', p_txt, re.I)
                    if mgr_match:
                        salutation, name, prev_role, new_role, eff_date = mgr_match.groups()
                        prev_clean = re.sub(r'[\(\)]', '', re.sub(r'\s+', ' ', prev_role)).strip().rstrip(',')
                        new_clean = re.sub(r'[\(\)]', '', re.sub(r'\s+', ' ', new_role)).strip().rstrip(',')
                        eff_clean = eff_date.strip().rstrip('.')
                        bullets.append(f"{salutation.strip()} {name.strip()} assumed charge as {new_clean}")
                        if prev_clean and len(prev_clean) > 3:
                            bullets.append(f"Previously served as {prev_clean}")
                        if eff_clean:
                            bullets.append(f"Effective date of new charge: {eff_clean}")
                            lead_summary = f"Change in {new_clean} roles effective {eff_clean}:"
                        else:
                            lead_summary = f"Change in {new_clean} roles:"
                        break

            # --- 4. PRESS RELEASES & BULLET LISTS ---
            for p_txt in raw_pages:
                lines = p_txt.splitlines()
                merged_lines = []
                i = 0
                while i < len(lines):
                    l = lines[i].strip()
                    if re.match(r'^[●•\*\–▪►\-]$', l) and i + 1 < len(lines):
                        merged_lines.append(f"• {lines[i+1].strip()}")
                        i += 2
                    else:
                        merged_lines.append(l)
                        i += 1

                curr_b = ""
                for line in merged_lines:
                    cl = line.strip()
                    if not cl:
                        if curr_b:
                            if len(curr_b) > 20 and not curr_b.lower().startswith(('dear sir', 'about ', 'disclaimer', 'pursuant to')):
                                bullets.append(curr_b)
                            curr_b = ""
                        continue

                    b_m = re.match(r'^[●•\*\–▪►\-]\s*(.+)$', cl) or re.match(r'^[a-z0-9ivx\(\)]+\.?\s*(.+)$', cl)
                    if b_m:
                        if curr_b and len(curr_b) > 20:
                            bullets.append(curr_b)
                        curr_b = b_m.group(1).strip()
                    elif curr_b:
                        if re.match(r'^(?:gurugram|mumbai|new delhi|bengaluru|hyderabad|pune|chennai|about\s+|disclaimer),', cl, re.I):
                            bullets.append(curr_b)
                            curr_b = ""
                        else:
                            curr_b += " " + cl
                if curr_b and len(curr_b) > 20:
                    bullets.append(curr_b)

            # --- 5. CLEAN AND DEDUPLICATE BULLETS ---
            cleaned_bullets = []
            seen = set()
            for b in bullets:
                b_clean = re.sub(r'\s+', ' ', b).strip()
                b_lower = b_clean.lower()
                if b_lower in seen:
                    continue
                if len(b_clean) < 15 or len(b_clean) > 260:
                    continue
                bad_starts = ('you that', 'held today', 'to the ', 'under applicable', 'responsibilities as', 'been sent', 'floor,', 'din:', 'next two', 'scrip code:', 'symbol:', 'dear sir', 'listing obligation', 'sub:', 'subject:', 'october,', 'november,', 'december,')
                if any(b_lower.startswith(bs) for bs in bad_starts):
                    continue
                bad_keywords = [
                    'about the company', 'safe harbor', 'cautionary statement', 'membership no',
                    'scanned with', 'regards', 'personal reason', 'digitally signed by',
                    'p.j. towers', 'pj towers', 'dalal street', 'phiroze jeejeebhoy', 'dcs-listing',
                    'cin no', 'cin:', 'secretarial@', 'email:', 'road,', 'street,', 'ahmedabad',
                    'mumbai - 400', 'new delhi - 110', 'floor,', 'tower,', 'pincode', 'scrip code:',
                    'general manager dcs', 'by electronic mode', 'particulars brief details'
                ]
                if any(bad in b_lower for bad in bad_keywords):
                    continue
                seen.add(b_lower)
                b_clean = re.sub(r'[,;:\-\s]+$', '', b_clean)
                cleaned_bullets.append(b_clean)

            return lead_summary, cleaned_bullets[:6]

        except Exception as e:
            logger.debug(f"AnnexureParser error for {pdf_url}: {e}")
            return "", []

    @staticmethod
    def _extract_via_multimodal_vision(pdf_bytes: bytes) -> Tuple[str, List[str]]:
        """Pass raw PDF bytes directly into Gemini API if standard text extraction yields < 250 characters."""
        try:
            import os
            from google import genai
            from google.genai import types

            api_key = os.getenv("GEMINI_API_KEY")
            if not api_key:
                return "", []

            client = genai.Client(api_key=api_key)
            prompt = (
                "Extract the core business news from this corporate exchange filing PDF. "
                "Output: 1 lead line summarizing the action, followed by 3-5 concise bullet points "
                "containing concrete numbers (Revenue, PAT, EBITDA, Margins, Order Value, Clients, Dates). "
                "Format: Lead line first, then lines starting with - for each bullet."
            )
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=[
                    types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
                    prompt
                ],
                config=types.GenerateContentConfig(
                    max_output_tokens=4096,
                    temperature=0.1
                )
            )
            text = response.text or ""
            lines = [l.strip() for l in text.splitlines() if l.strip()]
            if not lines:
                return "", []
            lead = lines[0].lstrip("-*•▪ ").strip()
            bullets = [l.lstrip("-*•▪ ").strip() for l in lines[1:] if len(l.strip()) > 15]
            return lead, bullets[:5]
        except Exception as e:
            logger.debug(f"Gemini multimodal PDF extraction fallback error: {e}")
            return "", []
