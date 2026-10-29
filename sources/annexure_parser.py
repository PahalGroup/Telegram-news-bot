"""Conservative PDF extraction: only verbatim prose with page evidence.
Ambiguous numeric tables and image-only pages are withheld, never guessed.
"""
import logging
from urllib.parse import urlparse
import pymupdf
from curl_cffi import requests
from src.quality import clean, substantive, sentences

logger = logging.getLogger(__name__)

class AnnexureParser:
    MAX_BYTES = 20 * 1024 * 1024
    MAX_PAGES = 40

    @staticmethod
    def clean_text(text):
        return str(text or "").replace("\u200b", "").replace("\ufeff", "")

    @classmethod
    def extract_evidence(cls, pdf_bytes):
        evidence = []
        seen = set()
        with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
            for index in range(min(len(doc), cls.MAX_PAGES)):
                # Numerical extraction is enabled only for unambiguous explicit headers.
                from sources.financial_tables import table_evidence
                page = doc[index]
                table_boxes = []
                try:
                    tables = page.find_tables()
                    table_boxes = [pymupdf.Rect(t.bbox) for t in tables.tables]
                    for table in tables.tables:
                        rows = table.extract()
                        # Disclosure forms contain prose in individual cells. Keep those
                        # cells separate; never join a label to unrelated numeric columns.
                        for row in rows:
                            for cell in row:
                                for text in sentences(cell or ""):
                                    if substantive(text) and len(text.split()) >= 8 and text.casefold() not in seen:
                                        evidence.append({"text": text, "page": index + 1, "method": "table_prose"})
                                        seen.add(text.casefold())
                        for fact in table_evidence(rows, page.get_text(), index + 1):
                            if fact["text"].casefold() not in seen:
                                evidence.append(fact)
                                seen.add(fact["text"].casefold())
                except Exception:
                    logger.warning("Table extraction unavailable on page %s", index + 1)
                # Block boundaries keep columns from being merged into invented rows.
                for block in doc[index].get_text("blocks", sort=True):
                    if len(block) < 5:
                        continue
                    if any(pymupdf.Rect(block[:4]).intersects(box) for box in table_boxes):
                        continue
                    raw = cls.clean_text(block[4])
                    lines = [clean(line) for line in raw.splitlines() if clean(line)]
                    # Dense tables require a separate header/unit/period-aware parser.
                    numeric_lines = sum(bool(__import__("re").fullmatch(r"[\d,.()%+\-– ]+", line)) for line in lines)
                    if numeric_lines >= 2:
                        continue
                    for sentence in sentences(raw):
                        value = clean(sentence).lstrip("•▪►* ")
                        if substantive(value) and value.casefold() not in seen:
                            evidence.append({"text": value, "page": index + 1})
                            seen.add(value.casefold())
        return evidence[:12]

    @classmethod
    def extract_annexure_bullets(cls, pdf_url, session=None):
        parsed = urlparse(str(pdf_url or ""))
        if parsed.scheme != "https" or not parsed.hostname:
            return "", []
        try:
            client = session or requests
            response = client.get(pdf_url, timeout=12, headers={"User-Agent": "Mozilla/5.0"})
            if response.status_code != 200:
                return "", []
            data = response.content
            if len(data) > cls.MAX_BYTES or not data.lstrip().startswith(b"%PDF-"):
                return "", []
            evidence = cls.extract_evidence(data)
            if not evidence:
                logger.warning("PDF requires table-aware extraction or OCR: %s", pdf_url)
                return "", []
            logger.info("PDF evidence pages: %s", sorted({e["page"] for e in evidence}))
            return "", [e["text"] for e in evidence[:6]]
        except Exception:
            logger.exception("PDF extraction failed: %s", pdf_url)
            return "", []

