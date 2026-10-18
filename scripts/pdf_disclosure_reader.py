import re
import logging
from typing import Optional, Dict, Any, List
import pymupdf as fitz
from curl_cffi import requests

logger = logging.getLogger("PDFReader")

class PDFDisclosureReader:
    """
    High-Speed PDF Disclosure Text & Fact Extractor for Indian Stock Exchanges (BSE / NSE).
    Reads the actual PDF filing uploaded by listed companies to extract:
    - Real clean Subject line (un-truncated, replacing generic LODR boilerplate)
    - Key financial figures (₹ Crore, Lakh, %, MW, share counts)
    - Operative transaction bullet points (Agreements, Orders, Acquisitions, Turnovers)
    """
    def __init__(self, timeout: int = 8):
        self.timeout = timeout
        self.session = requests.Session(impersonate="chrome120")

    def extract_from_url(self, pdf_url: str) -> Optional[Dict[str, Any]]:
        if not pdf_url or not pdf_url.lower().endswith(".pdf"):
            return None

        try:
            r = self.session.get(pdf_url, timeout=self.timeout)
            if r.status_code != 200 or len(r.content) < 500:
                return None

            doc = fitz.open(stream=r.content, filetype="pdf")
            max_pages = min(3, len(doc))
            pages_text = [doc[i].get_text() for i in range(max_pages)]
            doc.close()

            full_text = "\n".join(pages_text)
            page1 = pages_text[0] if pages_text else ""

            # 1. Extract Real Subject
            subject = self._extract_subject(page1, full_text)

            # 2. Extract Operative Fact Bullets
            bullets = self._extract_operative_bullets(page1, full_text)

            # 3. Extract Key Financials
            numbers = self._extract_numbers(full_text)

            return {
                "url": pdf_url,
                "clean_subject": subject,
                "bullets": bullets,
                "numbers": numbers,
                "raw_snippet": full_text[:1500]
            }
        except Exception as e:
            logger.debug(f"PDF extraction skipped for {pdf_url}: {e}")
            return None

    def _extract_subject(self, page1: str, full_text: str) -> str:
        """Extracts the clean un-truncated Subject line from corporate letterhead."""
        patterns = [
            r'(?:Sub|Subject)\s*[:\-]\s*(.*?)(?=\n\s*(?:Ref|Dear|Pursuant|In terms|Madam|Sir|\n\n))',
            r'(?:Sub|Subject)\s*[:\-]\s*(.*?)(?=\n[A-Z][a-z]+:)',
            r'(?:Sub|Subject)\s*[:\-]\s*(.*?)(?=\.\s*\n)',
        ]
        for pat in patterns:
            m = re.search(pat, page1, re.DOTALL | re.IGNORECASE)
            if m:
                clean = " ".join(m.group(1).split()).strip(" .:-")
                # Remove common formal prefixes
                clean = re.sub(r'^(?:Intimation\s+(?:under|regarding|of)\s+|Disclosure\s+under\s+Regulation\s*\d+\s*(?:\([0-9a-zA-Z]+\))?\s*-?\s*)', '', clean, flags=re.I).strip()
                if len(clean) > 15:
                    return clean

        return ""

    def _extract_operative_bullets(self, page1: str, full_text: str) -> List[str]:
        """Extracts 2 to 4 high-value substantive transaction bullets from the filing."""
        candidates = []
        
        # Clean text by normalizing whitespace
        lines = [l.strip() for l in page1.splitlines() if l.strip()]
        text_block = " ".join(lines)
        
        # Clean out letterhead addresses and headers
        header_cutoff = ["Dear Sir", "Dear Madam", "Dear Sir/Madam", "Dear Sir/Ma’am", "Dear Sir/Ma'am"]
        for hc in header_cutoff:
            if hc in text_block:
                text_block = text_block.split(hc, 1)[-1]
                break

        # Cut off signature block
        sig_cutoff = ["Thanking You", "Yours faithfully", "Yours truly", "For ", "FOR "]
        for sc in sig_cutoff:
            if sc in text_block:
                # Only cutoff if it appears in bottom half
                idx = text_block.rfind(sc)
                if idx > len(text_block) * 0.4:
                    text_block = text_block[:idx]
                    break

        triggers = [
            "entered into", "agreement", "acquire", "acquisition", "facility agreement",
            "term loan", "awarded", "order worth", "contract", "bagged", "wholly owned subsidiary",
            "amalgamation", "merger", "demerger", "appointed", "resigned", "resignation",
            "patent", "usfda", "turnover", "crore", "cr", "equity stake", "share swap", "solar",
            "power project", "mou", "memorandum of understanding"
        ]

        sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z0-9])', text_block)
        
        for s in sentences:
            s_clean = " ".join(s.split()).strip()
            # Clean leading salutation remnants
            s_clean = re.sub(r'^(?:/?(?:Ma’am|Madam|Sir)[,\s]*)+', '', s_clean, flags=re.I).strip()
            s_clean = re.sub(r'^(?:Sub(?:ject)?\s*[:\-]\s*.*?(?:Ref\s*[:\-]\s*.*?)?)\s*(?:Pursuant to.*?,?\s*)?', '', s_clean, flags=re.I).strip()
            
            # Ignore address/filing header lines
            if any(ign in s_clean for ign in ["ISIN :", "Scrip Code", "Department of Corporate", "Phiroze Jeejeebhoy", "Bandra Kurla", "Exchange Plaza"]):
                continue
            if any(ign in s_clean for sc in ["Digitally signed", "M. No.", "Company Secretary", "Compliance Officer"] for ign in [sc]):
                continue
            if len(s_clean) > 40 and any(t in s_clean.lower() for t in triggers):
                # Clean LODR legalistic boilerplate
                s_clean = re.sub(r'^(?:Pursuant to Regulation \d+.*?we (?:wish to |hereby )?inform (?:you that|the exchanges? that)?,?\s*)', '', s_clean, flags=re.I).strip()
                s_clean = re.sub(r'^(?:In terms of Regulation \d+.*?,\s*)', '', s_clean, flags=re.I).strip()
                s_clean = re.sub(r'^(?:Ref: Disclosure pursuant to Regulation \d+.*?,\s*)', '', s_clean, flags=re.I).strip()
                s_clean = s_clean.strip(" ,.:-")
                if s_clean and len(s_clean) > 30:
                    candidates.append(s_clean)

        # De-duplicate while preserving order
        unique_bullets = []
        seen = set()
        for c in candidates:
            key = c[:50].lower()
            if key not in seen:
                seen.add(key)
                unique_bullets.append(c)

        return unique_bullets[:3]

    @staticmethod
    def _extract_numbers(text: str) -> List[str]:
        return re.findall(r'(?:₹|rs\.?\s*)?[\d,]+(?:\.\d+)?\s*(?:cr(?:ore)?|lakh|%|mw|gw)?', text, re.IGNORECASE)
