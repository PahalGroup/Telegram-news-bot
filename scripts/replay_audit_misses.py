#!/usr/bin/env python3
"""Offline replay of gap-audit misses and the surrounding filing window.

Uses locally cached PDFs under /workspace/tnb-audit/pdfs (md5 of pdf_url).
Never downloads, never posts, never prints secrets.
"""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import pickle
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
logging.disable(logging.WARNING)

from sources.annexure_parser import AnnexureParser
from sources.result_scorecard import parse_document
from src import composer, editorial
from src.filter_engine import FilterEngine

AUDIT = Path("/workspace/tnb-audit")
PDF_DIR = AUDIT / "pdfs"
DEFAULT_OUT = AUDIT / "replay_gate_after.json"


def load_items():
    items = {}
    for path in sorted(AUDIT.glob("replay_in*.json")):
        data = json.loads(path.read_text())
        if isinstance(data, list):
            for item in data:
                items.setdefault(item["id"], item)
    return items


def load_audit():
    rows = list(csv.DictReader((AUDIT / "gap_audit.csv").open()))
    out = pickle.load((AUDIT / "out.pkl").open("rb"))
    fid = {x["ref_id"]: x.get("_fid") or "" for x in out}
    for row in rows:
        row["_fid"] = fid.get(int(row["ref_id"]), "")
    return rows


def pdf_path(url: str) -> Path | None:
    if not url:
        return None
    path = PDF_DIR / f"{hashlib.md5(url.encode()).hexdigest()}.pdf"
    return path if path.exists() else None


def extract_local(path: Path, kind: str):
    data = path.read_bytes()
    if not data or not data.startswith(b"%PDF"):
        return "", []
    if kind == "RESULTS":
        try:
            doc = pymupdf.open(stream=data, filetype="pdf")
        except Exception:
            return "", []
        try:
            card = parse_document(doc)
        finally:
            doc.close()
        if card and "revenue" in card.get("metrics", {}) and "pat" in card.get("metrics", {}):
            return {"template": "A", "card": card}, ["scorecard"]
        evidence = AnnexureParser.extract_evidence(data)
        return "", [e["text"] for e in evidence]
    if kind == "EARNINGS CALL":
        # Keep existing concall path out of this harness; annexure prose only.
        evidence = AnnexureParser.extract_evidence(data)
        return "", [e["text"] for e in evidence]
    evidence = AnnexureParser.extract_evidence(data)
    return "", [e["text"] for e in evidence]


def render_prebuilt(item, pre, now):
    pending, why = composer.compose_prebuilt(item, now, published=item.get("_published"))
    return pending, why


def evaluate_item(engine, rules, item, cache):
    original = dict(item)
    title = item.get("title") or ""
    desc0 = item.get("description") or ""
    event = editorial.classify(item, rules)
    kind = event.label
    pre, bullets = None, []
    url = item.get("pdf_url") or ""
    local = pdf_path(url)
    cache_key = f"{item.get('id')}|{kind}|{local or ''}"
    if cache_key in cache:
        pre, bullets = cache[cache_key]
    elif local is not None:
        try:
            pre, bullets = extract_local(local, kind)
        except Exception:
            pre, bullets = "", []
        cache[cache_key] = (pre, bullets)
    item = dict(item)
    if bullets:
        original = (item.get("description") or "").strip()
        merged = list(bullets)
        if original and original not in merged:
            merged = [original] + merged
        item["description"] = "\n".join(merged)
    published = None
    raw = item.get("published_at")
    if raw:
        try:
            published = datetime.fromisoformat(raw)
        except ValueError:
            published = None
    now = published or datetime.now()

    if event.rule == "always_drop":
        return {
            "id": item.get("id"), "outcome": f"rejected:{event.reason or 'PROCEDURAL'}",
            "category": "", "headline": "", "points": [], "post": "",
        }
    if pre:
        item["_prebuilt"] = pre
        pending, why = composer.compose_prebuilt(item, now, published)
        if pending is None:
            return {"id": item.get("id"), "outcome": f"rejected:{why}", "category": "FINANCIAL_RESULTS",
                    "headline": "", "points": [], "post": ""}
        return {"id": item.get("id"), "outcome": "queued", "category": "FINANCIAL_RESULTS",
                "headline": pending.header, "points": [], "post": pending.text(),
                "scorecard": True}

    passed, reason, candidate = engine.evaluate_stage_1(item)
    if not passed:
        return {"id": item.get("id"), "outcome": f"rejected:{reason}", "category": candidate.get("category", ""),
                "headline": "", "points": [], "post": ""}
    evaluation = engine.evaluate_stage_2(candidate)
    category = candidate.get("category", "")
    if not evaluation.get("publish") or evaluation.get("materiality_score", 0) < 70:
        return {"id": item.get("id"),
                "outcome": f"rejected:{evaluation.get('rejection_reason') or 'INSUFFICIENT_MATERIAL_EVIDENCE'}",
                "category": category, "headline": evaluation.get("headline", ""),
                "points": evaluation.get("summary_points") or [], "post": ""}
    evidence = "\n".join([item.get("description") or ""] + list(evaluation.get("summary_points") or []))
    keep, why = editorial.gate(dict(item, description=evidence), event, rules)
    if not keep:
        return {"id": item.get("id"), "outcome": f"rejected:{why}", "category": category,
                "headline": evaluation.get("headline", ""),
                "points": evaluation.get("summary_points") or [], "post": ""}
    pending, why = composer.compose_filing(
        item, evaluation, event or editorial.Event("UPDATE", "macro"), now, published)
    if pending is None:
        return {"id": item.get("id"), "outcome": f"rejected:{why}", "category": category,
                "headline": evaluation.get("headline", ""),
                "points": evaluation.get("summary_points") or [], "post": ""}
    return {"id": item.get("id"), "outcome": "queued", "category": category,
            "headline": evaluation.get("headline", ""),
            "points": evaluation.get("summary_points") or [], "post": pending.text(),
            "title": title, "desc0": desc0[:200]}


def main():
    engine = FilterEngine()
    rules = editorial.load_rules()
    items = load_items()
    rows = load_audit()
    cache = {}
    results = {}

    # Replay every filing we have an item for (FP check + misses).
    for item_id, item in items.items():
        results[item_id] = evaluate_item(engine, rules, item, cache)

    missed = [r for r in rows if r["reason_class"] in (
        "our bug (gate too strict)", "our bug (PDF parser)")]
    by_class = defaultdict(list)
    for row in missed:
        by_class[row["reason_class"]].append(row)

    def after(row):
        return results.get(row["_fid"], {})

    report = {
        "n_items": len(items),
        "n_results": len(results),
        "classes": {},
        "recovered": [],
        "still_missed": [],
        "new_vs_reference": [],
    }
    for cls, group in by_class.items():
        before_ok = 0  # all these were not queued on main (that's why they're misses)
        after_ok = sum(1 for r in group if after(r).get("outcome") == "queued")
        reasons_after = Counter(after(r).get("outcome") or "no_item" for r in group)
        no_item = sum(1 for r in group if not r["_fid"] or r["_fid"] not in items)
        report["classes"][cls] = {
            "n": len(group),
            "before_queued": 0,
            "after_queued": after_ok,
            "recovered": after_ok,
            "no_replay_item": no_item,
            "after_reasons": dict(reasons_after),
        }
        for row in group:
            rec = {
                "ref_id": row["ref_id"], "company": row["company"],
                "ref_headline": row["ref_headline"], "fid": row["_fid"],
                "before": row.get("replay_now"), "after": after(row).get("outcome"),
                "category": after(row).get("category"),
                "headline": after(row).get("headline"),
                "post": after(row).get("post", ""),
            }
            if after(row).get("outcome") == "queued":
                report["recovered"].append(rec)
            else:
                report["still_missed"].append(rec)

    # Reference *posted* this filing if the audit row is matched or missed.
    # A "correct skip" that we would now post is a loosening regression.
    posted_fids = {r["_fid"] for r in rows
                   if r.get("_fid") and r.get("status") in ("matched", "missed")
                   and r.get("reason_class") in (
                       "matched", "our bug (gate too strict)", "our bug (PDF parser)",
                       "our gap (no reliable OCR)", "our gap (no OCR)",
                       "fixed in current main (would post now)",
                       "our bug (not ingested)", "our rule (PDF size cap 20 MB)",
                   )}
    # Broader: any missed/matched row, regardless of reason class.
    posted_fids |= {r["_fid"] for r in rows
                    if r.get("_fid") and r.get("status") in ("matched", "missed")}
    skip_now = []
    for r in rows:
        if r.get("reason_class") in ("correct skip by our rules", "correct skip") or (
            r.get("status") == "missed" and "correct skip" in (r.get("reason_class") or "")
        ):
            rec = results.get(r.get("_fid") or "")
            if rec and rec.get("outcome") == "queued":
                skip_now.append({
                    "ref_id": r["ref_id"], "company": r.get("company"),
                    "ref_headline": r.get("ref_headline"), "fid": r.get("_fid"),
                    "reason_class": r.get("reason_class"),
                    "headline": rec.get("headline"), "post": rec.get("post", ""),
                })
    report["correct_skip_now_queued"] = skip_now
    for item_id, rec in results.items():
        if rec.get("outcome") != "queued":
            continue
        if item_id in posted_fids:
            continue
        item = items[item_id]
        report["new_vs_reference"].append({
            "id": item_id,
            "company": item.get("company"),
            "title": item.get("title"),
            "headline": rec.get("headline"),
            "post": rec.get("post", ""),
        })

    out = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUT
    # Keep the JSON lean for recovered/new posts; drop huge failed bodies.
    slim = {
        "classes": report["classes"],
        "n_items": report["n_items"],
        "recovered": report["recovered"],
        "still_missed_counts": Counter(
            (r["after"] or "no_item") + "|" + (r.get("category") or "")
            for r in report["still_missed"]
        ),
        "still_missed": [
            {k: r[k] for k in ("ref_id", "company", "ref_headline", "fid", "before", "after", "category", "headline")}
            for r in report["still_missed"]
        ],
        "new_vs_reference": report["new_vs_reference"],
        "correct_skip_now_queued": report.get("correct_skip_now_queued") or [],
    }
    # Counter is not JSON-serializable
    slim["still_missed_counts"] = dict(slim["still_missed_counts"])
    out.write_text(json.dumps(slim, ensure_ascii=False, indent=2))

    print("=== Replay summary ===")
    for cls, stats in report["classes"].items():
        print(f"{cls}: {stats['n']} misses; recovered {stats['recovered']}; reasons={stats['after_reasons']}")
    print(f"new vs reference: {len(report['new_vs_reference'])}")
    print(f"correct skip now queued: {len(report.get('correct_skip_now_queued') or [])}")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
