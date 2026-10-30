"""Inspect a fixed public filing corpus; no Telegram imports or credentials."""
import hashlib
import json
import sys
from pathlib import Path
import pymupdf
from curl_cffi import requests
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sources.annexure_parser import AnnexureParser
from src.filter_engine import FilterEngine
from src.formatter import Formatter

def main():
    cases = json.loads(Path("tests/fixtures/public_failure_review.json").read_text())
    results = []
    for case in cases:
        row = dict(case)
        try:
            response = requests.get(case["url"], timeout=30)
            data = response.content
            if response.status_code != 200 or not data.startswith(b"%PDF") or len(data) > AnnexureParser.MAX_BYTES:
                raise ValueError(f"Invalid PDF response: HTTP {response.status_code}")
            row["sha256"] = hashlib.sha256(data).hexdigest()
            if row["sha256"] != case["sha256"]:
                raise ValueError("Original document changed; review required")
            with pymupdf.open(stream=data, filetype="pdf") as doc:
                row["pages"] = len(doc)
                row["page_review"] = []
                for index, page in enumerate(doc):
                    if index >= 40:
                        break
                    text = page.get_text()
                    relevant = case["id"] != "dcw" or any(term in text for term in ("6,629", "475", "Geographical"))
                    if relevant:
                        row["page_review"].append({"page": index+1, "width": page.rect.width,
                            "text": text[:16000], "blocks": [list(b[:5]) for b in page.get_text("blocks", sort=True)][:100]})
            row["evidence"] = AnnexureParser.extract_evidence(data)
            item = dict(case, source="NSE", pdf_url=case["url"],
                        description="\n".join(e["text"] for e in row["evidence"]))
            engine = FilterEngine()
            passed, reason, candidate = engine.evaluate_stage_1(item)
            evaluation = engine.evaluate_stage_2(candidate) if passed else {}
            row["post"] = Formatter.format_post(evaluation, item) if evaluation.get("publish") else ""
            row["reason"] = reason or evaluation.get("rejection_reason")
            row["category"] = candidate.get("category")
            import re
            norm = lambda s: re.sub(r"\s+", "", s).casefold()
            row["checks"] = {fact: norm(fact) in norm(row["post"]) for fact in case["facts"]}
            row["absent_checks"] = {fact: norm(fact) not in norm(row["post"]) for fact in case["absent"]}
            row["passed"] = (bool(row["post"]) == case["publish"] and all(row["checks"].values())
                             and all(row["absent_checks"].values())
                             and (not case.get("category") or row["category"] == case["category"]))
        except Exception as exc:
            row["error"] = str(exc)
        results.append(row)
        print("PUBLIC_DOCUMENT_REVIEW " + json.dumps(row, ensure_ascii=False))
    Path("public-failure-review.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
    return int(any(not row.get("passed", False) for row in results))

if __name__ == "__main__":
    sys.exit(main())
