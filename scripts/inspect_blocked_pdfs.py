"""Read-only diagnosis of the four observed blocked public filings.

The larger byte limit is confined to this inspector; production limits stay intact.
No Telegram imports, credentials, or delivery state are used.
"""
import hashlib
import json
import re
import sys
from pathlib import Path
import pymupdf
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sources.pdf_download import download_pdf

CASES = {
    "raymond_lifestyle": "https://nsearchives.nseindia.com/corporate/RLL_02112026235951_RLLGroupintimationsigned.pdf",
    "raymond_realty": "https://nsearchives.nseindia.com/corporate/RRL_02112026234508_SE_Intimation.pdf",
    "raymond": "https://nsearchives.nseindia.com/corporate/RAYMOND_02112026234256_RLSEintimationsigned.pdf",
    "oversized_results": "https://nsearchives.nseindia.com/corporate/suhask_02112026235027_Reg30OutcomewithUDIN02112026.pdf",
}

def main():
    rows = []
    for name, url in CASES.items():
        row = {"id": name, "url": url}
        try:
            data = download_pdf(url, max_bytes=64*1024*1024)
            row.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
            with pymupdf.open(stream=data, filetype="pdf") as doc:
                row["pages"] = len(doc)
                row["inspected_pages"] = min(80, len(doc))
                row["page_profiles"] = []
                for i in range(row["inspected_pages"]):
                    page = doc[i]
                    text = page.get_text()
                    lines = [s.strip() for s in text.splitlines() if s.strip()]
                    headings = [s for s in lines if re.search(
                        r"Limited.*(?:Update|FY)|(?:standalone|consolidated|quarter|half.year|lakhs|crores)", s, re.I)]
                    row["page_profiles"].append({"page": i+1, "text_characters": len(text.strip()),
                        "image_count": len(page.get_images()),
                        "headings": headings[:12],
                        "sample": text[:1400] if i < 3 else ""})
            if name == "oversized_results":
                from sources.annexure_parser import AnnexureParser
                from src.filter_engine import FilterEngine
                from src.formatter import Formatter
                class ReviewParser(AnnexureParser):
                    MAX_PAGES = 60
                evidence = ReviewParser.extract_evidence(data)
                item = {"id": name, "source": "NSE", "company": "HAPPIEST MINDS TECHNOLOGIES LIMITED",
                        "title": "Financial results for Q2 FY27", "pdf_url": url,
                        "description": "\n".join(e["text"] for e in evidence)}
                engine = FilterEngine()
                passed, reason, candidate = engine.evaluate_stage_1(item)
                evaluation = engine.evaluate_stage_2(candidate) if passed else {}
                row["review_candidate"] = {
                    "evidence_count": len(evidence),
                    "post": Formatter.format_post(evaluation, item) if evaluation.get("publish") else "",
                    "reason": reason or evaluation.get("rejection_reason"),
                    "evidence": [e for e in evidence if e["page"] >= 45]}
            row["status"] = "inspected"
        except Exception as exc:
            row.update(status="error", error=str(exc))
        rows.append(row)
        print("BLOCKED_PDF_REVIEW " + json.dumps(row, ensure_ascii=False))
    Path("blocked-pdf-review.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2))
    return int(any(r["status"] != "inspected" for r in rows))

if __name__ == "__main__":
    sys.exit(main())
