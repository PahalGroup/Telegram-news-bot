"""Conservative PDF extraction: only verbatim prose with page evidence.
Ambiguous numeric tables and image-only pages are withheld, never guessed.
"""
import logging
import re
from urllib.parse import urlparse
import pymupdf
from src.quality import clean, substantive, sentences, paragraph_sentences

logger = logging.getLogger(__name__)

class PDFExtractionError(RuntimeError):
    """A PDF could not be downloaded or parsed; distinct from unsupported evidence."""


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
            opening = '\n'.join(doc[i].get_text() for i in range(min(4, len(doc))))
            # A portfolio presentation filed by several listed companies cannot
            # inherit the filing company's identity after page context is flattened.
            entities = set(re.findall(r'^\s*([A-Z][^\n]{1,90}\b(?:Limited|Ltd\.?))\s*$',
                                      opening, re.M))
            if re.search(r'group\s+financial\s+scorecard', opening, re.I) and len(entities) >= 3:
                logger.warning('Multi-entity group presentation requires page/entity association')
                return []
            for index in range(min(len(doc), cls.MAX_PAGES)):
                # Numerical extraction is enabled only for unambiguous explicit headers.
                from sources.financial_tables import table_evidence, resolve_merged_headers
                page = doc[index]
                table_boxes = []
                try:
                    tables = page.find_tables()
                    table_boxes = [pymupdf.Rect(t.bbox) for t in tables.tables]
                    for table in tables.tables:
                        rows = table.extract()
                        from sources.disclosure_tables import disclosure_table_evidence
                        from sources.management_tables import management_table_evidence
                        evidence.extend(disclosure_table_evidence(rows, index + 1))
                        evidence.extend(management_table_evidence(rows, index + 1))
                        # Disclosure forms contain prose in individual cells. Keep those
                        # cells separate; never join a label to unrelated numeric columns.
                        for row in rows:
                            for cell in row:
                                for text in sentences(cell or ""):
                                    if substantive(text) and len(text.split()) >= 8 and text.casefold() not in seen:
                                        evidence.append({"text": text, "page": index + 1, "method": "table_prose"})
                                        seen.add(text.casefold())
                        resolved_rows = resolve_merged_headers(rows, [row.cells for row in table.rows])
                        for fact in table_evidence(resolved_rows, page.get_text(), index + 1):
                            if fact["text"].casefold() not in seen:
                                evidence.append(fact)
                                seen.add(fact["text"].casefold())
                except Exception:
                    logger.warning("Table extraction unavailable on page %s", index + 1)
                # Join full-width wrapped lines using geometry; retain column/table boundaries.
                from sources.pdf_layout import join_wrapped_blocks, join_bullet_continuations
                raw_blocks = doc[index].get_text("blocks", sort=True)
                from src.ipo_evidence import recover_ipo_sale_paragraphs
                for point in recover_ipo_sale_paragraphs(raw_blocks, page.rect.width):
                    if point.casefold() not in seen:
                        evidence.append({"text": point, "page": index + 1,
                                         "method": "explicit_ipo_sale_paragraph"})
                        seen.add(point.casefold())
                prose_blocks = []
                for block in raw_blocks:
                    if any(pymupdf.Rect(block[:4]).intersects(box) for box in table_boxes):
                        prose_blocks.append([*block[:4], ""])  # explicit boundary
                    else:
                        prose_blocks.append(block)
                for block in join_wrapped_blocks(join_bullet_continuations(prose_blocks, page.rect.width), page.rect.width):
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
                    for sentence in paragraph_sentences(raw):
                        value = clean(sentence).lstrip("•▪►* ")
                        if substantive(value) and value.casefold() not in seen:
                            evidence.append({"text": value, "page": index + 1})
                            seen.add(value.casefold())
        return evidence

    @classmethod
    def extract_annexure_bullets(cls, pdf_url, session=None):
        parsed = urlparse(str(pdf_url or ""))
        if parsed.scheme != "https" or not parsed.hostname:
            return "", []
        try:
            from sources.pdf_download import download_pdf
            data = download_pdf(pdf_url, client=session, max_bytes=cls.MAX_BYTES)
            evidence = cls.extract_evidence(data)
            if not evidence:
                logger.warning("PDF requires table-aware extraction or OCR: %s", pdf_url)
                return "", []
            logger.info("PDF evidence pages: %s", sorted({e["page"] for e in evidence}))
            return "", [e["text"] for e in evidence]
        except Exception as exc:
            from sources.pdf_download import PDFDownloadError
            failure = PDFExtractionError(f"PDF extraction failed: {pdf_url}: {exc}")
            # Exception attributes survive process-pool serialization. Permanent
            # download rejections need review, not repeated automatic downloads.
            failure.reason = getattr(exc, "reason", "PDF_PARSE_ERROR")
            failure.retryable = isinstance(exc, PDFDownloadError) and exc.retryable
            raise failure from exc



