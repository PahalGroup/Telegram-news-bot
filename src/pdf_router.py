"""Process-pool worker: one PDF download, then the extractor that fits the filing.

RESULTS        -> template A scorecard (sources.result_scorecard)
EARNINGS CALL  -> template C concall summary (src.concall_summary)
anything else  -> Regulation-30 annexure evidence (sources.annexure_parser)

Returns (prebuilt, bullets). `prebuilt` is a dict for templates A/C and "" otherwise,
matching the (lead, bullets) contract of AnnexureParser.extract_annexure_bullets.
"""
from urllib.parse import urlparse


def extract(pdf_url, kind="", session=None):
    from sources.annexure_parser import AnnexureParser, PDFExtractionError
    if kind not in ("RESULTS", "EARNINGS CALL"):
        return AnnexureParser.extract_annexure_bullets(pdf_url, session)
    parsed = urlparse(str(pdf_url or ""))
    if parsed.scheme != "https" or not parsed.hostname:
        return "", []
    import fitz
    from sources.pdf_download import download_pdf, PDFDownloadError
    try:
        data = download_pdf(pdf_url, client=session, max_bytes=AnnexureParser.MAX_BYTES)
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:
        failure = PDFExtractionError(f"PDF extraction failed: {pdf_url}: {exc}")
        failure.reason = getattr(exc, "reason", "PDF_PARSE_ERROR")
        failure.retryable = isinstance(exc, PDFDownloadError) and exc.retryable
        raise failure from exc
    if kind == "RESULTS":
        from sources.result_scorecard import parse_document
        card = parse_document(doc)
        if not card or "revenue" not in card["metrics"] or "pat" not in card["metrics"]:
            return "", []      # scans and unusual layouts are withheld, never guessed
        return {"template": "A", "card": card}, ["scorecard"]
    from src.concall_summary import summarise_pdf
    points = summarise_pdf(doc)
    if not points:
        return "", []
    return {"template": "C", "points": points}, points
