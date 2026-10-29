"""Replay public original filings. No Telegram import, credentials or state mutation."""
import hashlib
import json
import re
import sys
from pathlib import Path
import pymupdf
from curl_cffi import requests
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sources.annexure_parser import AnnexureParser
from src.filter_engine import FilterEngine
from src.formatter import Formatter

def normalized(value):
    return re.sub(r"\s+", "", value).casefold()

def main():
    cases = json.loads(Path("tests/fixtures/public_filings.json").read_text())
    results = []
    for case in cases:
        result = {"id": case["id"], "url": case["url"]}
        try:
            response = requests.get(case["url"], timeout=20)
            if response.status_code != 200 or not response.content.startswith(b"%PDF"):
                raise ValueError(f"PDF unavailable: HTTP {response.status_code}")
            data = response.content
            if len(data) > AnnexureParser.MAX_BYTES:
                raise ValueError("PDF too large")
            result["sha256"] = hashlib.sha256(data).hexdigest()
            with pymupdf.open(stream=data, filetype="pdf") as doc:
                source = " ".join(page.get_text() for page in doc)
                result["pages"] = len(doc)
            evidence = AnnexureParser.extract_evidence(data)
            item = dict(case, source="NSE", pdf_url=case["url"],
                        description="\n".join(e["text"] for e in evidence[:6]))
            engine = FilterEngine()
            passed, reason, candidate = engine.evaluate_stage_1(item)
            evaluation = engine.evaluate_stage_2(candidate) if passed else {}
            post = Formatter.format_post(evaluation, item) if evaluation.get("publish") else ""
            result.update(stage1=passed, reason=reason or evaluation.get("rejection_reason"),
                          category=candidate.get("category"), post=post, evidence=evidence)
            result["checks"] = {fact: {"source": normalized(fact) in normalized(source),
                                       "output": normalized(fact) in normalized(post)}
                                for fact in case["facts"]}
            result["passed"] = (bool(post) and candidate.get("category")==case["category"]
                                and all(v["source"] and v["output"] for v in result["checks"].values()))
            # Bounded original text for source review, not a generated summary.
            result["source_review"] = source[:14000]
        except Exception as exc:
            result.update(passed=False, error=str(exc))
        results.append(result)
        print(json.dumps({k:v for k,v in result.items() if k!="source_review"}, ensure_ascii=False))
    Path("filing-replay.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print("SOURCE_REVIEW_BEGIN")
    for result in results:
        print(json.dumps({"id":result["id"], "source_review":result.get("source_review","")}, ensure_ascii=False))
    return 0 if all(r["passed"] for r in results) else 1

if __name__ == "__main__":
    sys.exit(main())
