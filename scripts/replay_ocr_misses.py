#!/usr/bin/env python3
"""Offline OCR replay of the 303 scanned audit misses. Never posts. Never prints secrets."""
from __future__ import annotations

import csv
import hashlib
import json
import os
import pickle
import sys
from collections import Counter
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["MARKET_WIRE_OCR_TEST"] = "1"

from sources.result_ocr import parse_scanned_document, log_card

AUDIT = Path("/workspace/tnb-audit")
PDF_DIR = AUDIT / "pdfs"


def items_by_id():
    items = {}
    for path in AUDIT.glob("replay_in*.json"):
        data = json.loads(path.read_text())
        if isinstance(data, list):
            for item in data:
                items.setdefault(item["id"], item)
    return items


def process(row, item):
    rec = {"ref_id": row["ref_id"], "company": row["company"], "headline": row["ref_headline"],
           "layer": row["pdf_layer"], "ok": False, "reason": "no_pdf"}
    if item and item.get("pdf_url"):
        path = PDF_DIR / f"{hashlib.md5(item['pdf_url'].encode()).hexdigest()}.pdf"
        if path.exists():
            try:
                with pymupdf.open(path) as doc:
                    card, reason = parse_scanned_document(doc)
                rec["ok"] = bool(card)
                rec["reason"] = reason or "ok"
                if card:
                    rec["page"] = card.get("page")
                    rec["scope"] = card.get("scope")
                    rec["period"] = card.get("period")
                    rec["revenue"] = (card["metrics"].get("revenue") or {}).get("cur")
                    rec["pat"] = (card["metrics"].get("pat") or {}).get("cur")
            except Exception as exc:
                rec["reason"] = f"exception:{type(exc).__name__}"
    return rec


def main():
    from concurrent.futures import ProcessPoolExecutor, as_completed
    audit = list(csv.DictReader((AUDIT / "gap_audit.csv").open()))
    rows = [r for r in audit if r["reason_class"] == "our gap (no reliable OCR)"]
    fid = {x["ref_id"]: x.get("_fid") or "" for x in pickle.load((AUDIT / "out.pkl").open("rb"))}
    items = items_by_id()
    out_path = Path(sys.argv[1]) if len(sys.argv) > 1 else AUDIT / "replay_ocr_after.json"
    jobs = [(row, items.get(fid.get(int(row["ref_id"])) or "")) for row in rows]
    results = []
    with ProcessPoolExecutor(max_workers=3) as pool:
        futs = [pool.submit(process, row, item) for row, item in jobs]
        for fut in as_completed(futs):
            rec = fut.result()
            results.append(rec)
            print(f"{str(rec['company'])[:40]:40} ok={rec['ok']} {rec['reason']}", flush=True)
    results.sort(key=lambda r: int(r["ref_id"]))
    out_path.write_text(json.dumps({"n": len(results), "passed": sum(r["ok"] for r in results),
                                    "reasons": dict(Counter(r["reason"] for r in results)),
                                    "rows": results}, indent=2, ensure_ascii=False))
    passed = sum(r["ok"] for r in results)
    print(f"=== {passed}/{len(results)} passed checks; wrote {out_path}")


if __name__ == "__main__":
    main()
