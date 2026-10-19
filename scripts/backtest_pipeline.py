"""
Phase 5: Deterministic Backtest Engine (scripts/backtest_pipeline.py)
Validates FilterEngine and Formatter against Reference Channel ground truth:
- Evaluates Stage 1 + Stage 2 against 60 Reference posts + raw upstream filings
- Verifies HTML rendering validity (0 unescaped tags or parse errors)
- Measures Recall, Precision, and F1-Score (Target: >= 90%)
"""

import os
import re
import sys
import json
import logging
from typing import Dict, Any, List

# Ensure UTF-8 output
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if base_dir not in sys.path:
    sys.path.insert(0, base_dir)

from src.filter_engine import FilterEngine
from src.formatter import Formatter

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("BacktestEngine")

def validate_telegram_html(html_str: str) -> bool:
    """Validate that HTML tags are strictly balanced and safe for Telegram parse_mode='HTML'."""
    allowed_tags = ["b", "i", "u", "s", "code", "pre", "a"]
    stack = []
    # Tokenize tags
    tokens = re.findall(r"(</?[a-zA-Z0-9]+(?:\s+href=\"[^\"]*\")?>|[^<]+)", html_str)
    for tok in tokens:
        if tok.startswith("<") and tok.endswith(">"):
            tag_content = tok[1:-1].strip()
            if tag_content.startswith("/"):
                # Closing tag
                tag_name = tag_content[1:].strip().lower()
                if not stack or stack[-1] != tag_name:
                    return False
                stack.pop()
            else:
                # Opening tag
                parts = tag_content.split()
                tag_name = parts[0].lower()
                if tag_name not in allowed_tags:
                    return False
                stack.append(tag_name)
    return len(stack) == 0

def run_backtest():
    print("=" * 70)
    print(" PHASE 5: DETERMINISTIC PIPELINE BACKTEST & VERIFICATION ")
    print("=" * 70)

    # 1. Load ground truth
    audit_path = os.path.join(base_dir, "state", "reverse_engineering_audit.json")
    if not os.path.exists(audit_path):
        print(f"[!] Warning: {audit_path} not found. Running reverse engineer first...")
        import subprocess
        subprocess.run([sys.executable, os.path.join(base_dir, "scripts", "reverse_engineer_reference.py")], check=True)

    with open(audit_path, "r", encoding="utf-8") as f:
        audit_data = json.load(f)

    filter_engine = FilterEngine()

    # Build test dataset of ground-truth reference posts (positives) and noise samples (negatives)
    positive_items = []
    
    # 1. Load from reference_posts.json if available
    ref_json_path = os.path.join(base_dir, "reference_posts.json")
    if os.path.exists(ref_json_path):
        try:
            with open(ref_json_path, "r", encoding="utf-8") as f:
                ref_json = json.load(f)
                for rp in ref_json:
                    txt = rp.get("text", "").strip()
                    if len(txt) > 20:
                        lines = [l.strip() for l in txt.splitlines() if l.strip()]
                        title = lines[0] if lines else txt[:60]
                        if len(title) < 15 and len(lines) > 1:
                            title = f"{lines[0]} {lines[1]}"
                        desc = " ".join(lines[1:]) if len(lines) > 1 else ""
                        
                        is_promo = any(w in txt.upper() for w in ["PAID PROMOTION", "SPONSORED", "DISCOUNT CODE", "VIP GROUP"])
                        if is_promo:
                            negative_items.append({
                                "id": f"REF_PROMO_{rp.get('post_id')}",
                                "title": title,
                                "description": desc,
                                "expected_publish": False
                            })
                        else:
                            positive_items.append({
                                "id": f"REF_POST_{rp.get('post_id')}",
                                "title": title,
                                "description": desc,
                                "company": title.split(":")[0].strip() if ":" in title else "",
                                "source": "REF_CHANNEL",
                                "expected_publish": True
                            })
        except Exception as e:
            logger.debug(f"Error reading {ref_json_path}: {e}")

    # 2. Load from matched_events
    for m in audit_data.get("matched_events", []):
        header = m.get("ref_header") or m.get("upstream_headline") or ""
        if not header or len(header.strip()) < 5:
            continue
        entity = m.get("ref_entity") or m.get("upstream_company") or ""
        positive_items.append({
            "id": f"REF_MATCH_{m.get('ref_id')}",
            "title": str(header),
            "description": f"{entity} corporate action disclosure as verified on exchanges.",
            "company": str(entity),
            "source": m.get("upstream_source", "REF"),
            "expected_publish": True
        })

    # Noise items from standard negative pool
    negative_items = [
        {"id": "NOISE_1", "title": "Intimation of Closure of Trading Window", "description": "Trading window for all designated persons shall remain closed.", "expected_publish": False},
        {"id": "NOISE_2", "title": "Compliance Certificate under Reg 74(5)", "description": "Certificate under Reg 74(5) of SEBI DP Regulations for Q2.", "expected_publish": False},
        {"id": "NOISE_3", "title": "Loss of Share Certificate", "description": "Intimation of loss of share certificates as per Reg 39(3).", "expected_publish": False},
        {"id": "NOISE_4", "title": "Submission of Newspaper Advertisement", "description": "Newspaper clippings published in Financial Express and Navshakti.", "expected_publish": False},
        {"id": "NOISE_5", "title": "Duplicate Share Certificate", "description": "Issue of duplicate share certificate in lieu of lost original.", "expected_publish": False},
        {"id": "NOISE_6", "title": "Intimation of Analyst Call", "description": "Conference call with analysts on Oct 24 without presentation.", "expected_publish": False},
        {"id": "NOISE_7", "title": "Reconciliation of Share Capital Audit", "description": "Report for the quarter ended September 30, 2026.", "expected_publish": False},
        {"id": "NOISE_8", "title": "Disclosure Attached", "description": "Submission of disclosure under Reg 30 - disclosure attached.", "expected_publish": False},
        {"id": "NOISE_9", "title": "Statement of Investor Grievance", "description": "Grievance redressal mechanism report under Regulation 13(3).", "expected_publish": False},
        {"id": "NOISE_10", "title": "Stocks to buy today for 40% gain", "description": "Join our VIP Telegram group for multibagger calls.", "expected_publish": False},
    ]

    total_test_items = positive_items + negative_items
    print(f"[*] Testing pipeline across {len(total_test_items)} items ({len(positive_items)} ground-truth positives, {len(negative_items)} ground-truth noise negatives)...")

    true_positives = 0
    false_positives = 0
    true_negatives = 0
    false_negatives = 0
    html_errors = 0

    for item in total_test_items:
        filter_engine.recent_fingerprints.clear()
        passed_s1, reason_s1, candidate = filter_engine.evaluate_stage_1(item)
        if passed_s1:
            eval_data = filter_engine.evaluate_stage_2(candidate)
            published = eval_data.get("publish", False) and eval_data.get("materiality_score", 0) >= 70
        else:
            published = False
            eval_data = {"headline": "", "key_metrics": [], "summary_points": [], "what_it_means": ""}

        # Test HTML formatting if published
        if published:
            formatted_html = Formatter.format_post(eval_data, item)
            is_valid_html = validate_telegram_html(formatted_html)
            if not is_valid_html:
                html_errors += 1
                print(f"[!] HTML Error in item {item['id']}:\n{formatted_html}")

        # Metrics accounting
        expected = item["expected_publish"]
        if published and expected:
            true_positives += 1
        elif published and not expected:
            false_positives += 1
            print(f"[!] False Positive: {item['title']}")
        elif not published and not expected:
            true_negatives += 1
        elif not published and expected:
            false_negatives += 1
            print(f"[!] False Negative: {item['title']}")

    precision = (true_positives / (true_positives + false_positives) * 100) if (true_positives + false_positives) > 0 else 0.0
    recall = (true_positives / (true_positives + false_negatives) * 100) if (true_positives + false_negatives) > 0 else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0

    print("\n" + "=" * 70)
    print(" BACKTEST SCOREBOARD & METRICS ")
    print("=" * 70)
    print(f" Total Evaluated:       {len(total_test_items)}")
    print(f" True Positives (TP):   {true_positives}")
    print(f" True Negatives (TN):   {true_negatives}")
    print(f" False Positives (FP):  {false_positives}")
    print(f" False Negatives (FN):  {false_negatives}")
    print(f" Precision:             {precision:.2f}% (Target: >= 90%)")
    print(f" Recall:                {recall:.2f}% (Target: >= 90%)")
    print(f" F1-Score:              {f1:.2f}%")
    print(f" HTML Parse Errors:     {html_errors} (Target: 0)")
    print("=" * 70)

    # Verification assertions
    assert html_errors == 0, f"HTML parsing errors found: {html_errors}"
    assert precision >= 90.0, f"Precision below 90%: {precision:.2f}%"
    assert recall >= 90.0, f"Recall below 90%: {recall:.2f}%"

    print("[SUCCESS] All Verification Gates Satisfied! Pipeline meets >=90% Recall, >=90% Precision, and 0 HTML errors.\n")
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "html_errors": html_errors
    }

if __name__ == "__main__":
    run_backtest()
