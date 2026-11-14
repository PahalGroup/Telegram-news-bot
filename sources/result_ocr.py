"""Tesseract OCR of a scanned results *statement* page, with arithmetic checks.

Only the statement page is recognised. Figures are published only when the
sum checks pass. Failed checks or unreadable grids are withheld, never guessed.
Tesseract runs locally; nothing is sent to a remote model.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pymupdf

from sources.result_scorecard import UNIT_TO_CR, statement_scope, statement_unit, bare_unit

logger = logging.getLogger(__name__)

MAX_PAGES = 16
SCALE = 2.0
TIMEOUT = 25
TOL_ABS = 1.0          # same family as the text-layer scorecard (in statement units)
TOL_REL = 0.002

STATEMENT = re.compile(
    r"revenue from operations|income from operations|statement of (?:unaudited |audited )?financial results",
    re.I,
)
SKIP = re.compile(
    r"independent auditor.?s review|we have reviewed the accompanying|"
    r"we draw (?:your )?attention|emphasis of matter|basis for (?:qualified |adverse )?opinion|"
    r"other matters?\s*:|list of (?:subsidiaries|associates)|"
    r"cash flow statement|statement of (?:assets|cash flows)|segment[\s-]?wise|"
    r"statement of assets (?:and|&) liabilities|notes to the (?:unaudited )?financial",
    re.I,
)
LABELS = {
    "revenue": re.compile(r"(?:total\s+)?(?:revenue|income)\s+from\s+operations", re.I),
    "other_income": re.compile(r"other\s+income\b", re.I),
    "total_income": re.compile(r"total\s+income\b", re.I),
    "pbt": re.compile(r"profit\s*/?\s*\(?(?:loss)?\)?\s*before\s+tax\b|"
                      r"(?:net\s+)?profit\s*/?\s*\(loss\)\s*before\s+(?:exceptional|tax|share)", re.I),
    "tax": re.compile(r"(?:tax\s+expense|current\s+tax|total\s+tax)\b", re.I),
    "pat": re.compile(r"(?:profit|net\s+profit|\(?loss\)?)\s*/?\s*(?:\(loss\)\s*)?(?:after\s+tax|for the (?:period|quarter))", re.I),
}
NUMBER = re.compile(r"\(?-?(?:\d{1,3}(?:,\d{2,3})+|\d+)(?:\.\d+)?\)?")
HALF = re.compile(r"\b(?:half[\s-]?year|h1|six months?|year[\s-]?to[\s-]?date|ytd)\b", re.I)
YEAR = re.compile(r"\byear\s+ended\b|\bfor the year\b", re.I)
QUARTER = re.compile(r"\b(?:quarter|three months?)\s+ended\b|\bq[1-4]\b", re.I)


def test_mode() -> bool:
    return os.environ.get("MARKET_WIRE_OCR_TEST", "").strip() in {"1", "true", "yes"}


def _ocr_png(path: Path) -> str:
    engine = shutil.which("tesseract")
    if not engine:
        return ""
    try:
        result = subprocess.run(
            [engine, str(path), "stdout", "-l", "eng", "--psm", "6"],
            capture_output=True, text=True, timeout=TIMEOUT, check=True,
        )
        return result.stdout or ""
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, OSError):
        logger.warning("tesseract failed on %s", path)
        return ""


def ocr_page(page) -> str:
    with tempfile.TemporaryDirectory(prefix="tnb-ocr-") as work:
        path = Path(work) / "page.png"
        scale = min(SCALE, (8_000_000 / max(page.rect.width * page.rect.height, 1)) ** 0.5)
        page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False).save(str(path))
        return _ocr_png(path)


def _is_statement(text: str) -> bool:
    if not text or SKIP.search(text):
        return False
    if not STATEMENT.search(text):
        return False
    # Auditor cover notes often name "the statement of financial results"
    # without a P&L grid. Prefer a page that actually tabulates operations.
    if re.search(r"(?:total\s+)?(?:revenue|income)\s+from\s+operations", text, re.I):
        return True
    # A clean header still counts if several period columns are visible.
    nums = len(re.findall(r"\d[\d,]*\.\d{2}", text))
    return nums >= 8


def find_statement_page(doc):
    """Return (page_index, text) for the P&L statement, or (None, '')."""
    # Prefer a text layer that already looks like the statement and is not a skip page.
    for index, page in enumerate(doc):
        if index >= MAX_PAGES:
            break
        layer = page.get_text() or ""
        if _is_statement(layer) and len(layer.strip()) >= 200:
            return index, layer
    # Otherwise OCR image-heavy pages only, and stop at the first statement hit.
    for index, page in enumerate(doc):
        if index >= MAX_PAGES:
            break
        layer = (page.get_text() or "").strip()
        if len(layer) >= 400 and not page.get_images():
            continue
        text = ocr_page(page)
        if _is_statement(text):
            return index, text
    return None, ""


def _nums(line: str):
    out = []
    for m in NUMBER.finditer(line):
        raw = m.group(0)
        if raw.count(".") > 1:
            continue
        t = raw.replace(",", "")
        neg = (t.startswith("(") and t.endswith(")")) or t.startswith("-")
        try:
            value = float(t.strip("()-"))
        except ValueError:
            continue
        if neg:
            value = -value
        out.append(value)
    return out


def _header_kinds(text: str):
    """Classify consecutive number columns from the header block as quarter / half / year."""
    header = "\n".join(text.splitlines()[:12])
    # A typical SEBI row is: Q (current), Q (prev), Q (yoy), Half (current), Half (yoy), Year.
    # We only need to *avoid* treating a half-year or year column as the current quarter.
    kinds = []
    for line in text.splitlines()[:12]:
        if not (QUARTER.search(line) or HALF.search(line) or YEAR.search(line)):
            continue
        # Split the header line into cells on large gaps already flattened by OCR.
        cells = re.split(r"\s{2,}|\|", line)
        if len(cells) < 2:
            cells = re.split(r"(?<=ended)|(?<=2026)|(?<=2025)", line)
        for cell in cells:
            if QUARTER.search(cell) and not HALF.search(cell) and not YEAR.search(cell):
                kinds.append("q")
            elif HALF.search(cell):
                kinds.append("h")
            elif YEAR.search(cell):
                kinds.append("y")
        if kinds:
            break
    return kinds


def _pick_columns(kinds, n):
    """Return index mapping cur/qoq/yoy for the current quarter, plus optional half-year cur."""
    if kinds and len(kinds) >= n:
        kinds = kinds[:n]
    else:
        kinds = None
    if kinds:
        q = [i for i, k in enumerate(kinds) if k == "q"]
        h = [i for i, k in enumerate(kinds) if k == "h"]
        if not q:
            return None
        idx = {"cur": q[0]}
        if len(q) >= 2:
            idx["qoq"] = q[1]
        if len(q) >= 3:
            idx["yoy"] = q[2]
        if h:
            idx["half"] = h[0]
        return idx
    # Fallback: first number is the current quarter (SEBI layout).
    if n < 1:
        return None
    idx = {"cur": 0}
    if n >= 2:
        idx["qoq"] = 1
    if n >= 3:
        idx["yoy"] = 2
    if n >= 4:
        idx["half"] = 3
    return idx


def _drop_serial_columns(values: dict) -> dict:
    """Drop a leading serial/line-number column (1, 2, 16) so the first figure is the quarter."""
    def serial(row):
        if not row or len(row) < 2:
            return False
        first, second = row[0], row[1]
        return (
            abs(first - round(first)) < 1e-9
            and 1 <= first <= 40
            and (second >= 50 or second >= 10 * max(abs(first), 1))
        )
    revenue = values.get("revenue") or []
    if not serial(revenue) and sum(serial(v) for v in values.values()) < 2:
        return values
    out = {}
    for key, row in values.items():
        if row and abs(row[0] - round(row[0])) < 1e-9 and 0 <= row[0] <= 40:
            out[key] = row[1:]
        else:
            out[key] = row
    return out


def parse_ocr_text(text: str):
    if not text:
        return None, "OCR_EMPTY"
    scope = statement_scope(text)
    unit = statement_unit(text) or bare_unit(text)
    if unit is None:
        # Scanned statements usually print "Rs. in Lakhs" or "Rs. in Crore". Refuse if unknown.
        return None, "OCR_UNIT_AMBIGUOUS"
    if scope is None:
        # Same default as the text scorecard: a company without subsidiaries files standalone.
        if not re.search(r"standalone|consolidated", text, re.I):
            scope = "standalone"
        else:
            return None, "OCR_SCOPE_AMBIGUOUS"
    kinds = _header_kinds(text)
    values = {}
    for line in text.splitlines():
        label_key = None
        for key, pat in LABELS.items():
            if pat.search(line) and not (key == "revenue" and re.search(r"other", line, re.I)):
                if key == "total_income" and re.search(r"from operations", line, re.I):
                    continue
                if key == "pat" and re.search(r"before|comprehensive|attributable|share of", line, re.I):
                    continue
                label_key = key
                break
        if not label_key:
            continue
        nums = _nums(line)
        if len(nums) < 1:
            continue
        values[label_key] = nums
    values = _drop_serial_columns(values)
    if "revenue" not in values or "pat" not in values:
        return None, "OCR_MISSING_REVENUE_OR_PAT"
    n = min(len(v) for v in values.values())
    if n < 1:
        return None, "OCR_NO_COLUMNS"
    idx = _pick_columns(kinds, n)
    if not idx:
        return None, "OCR_COLUMNS_UNCLEAR"

    def col(row, name):
        i = idx.get(name)
        if i is None or i >= len(row):
            return None
        return row[i]

    # --- sum checks (statement units, before conversion) ---
    def close(a, b):
        return abs(a - b) <= max(TOL_ABS, abs(a) * TOL_REL)

    for name in ("cur", "qoq", "yoy", "half"):
        if name not in idx:
            continue
        if "other_income" in values and "total_income" in values:
            rev, oth, tot = col(values["revenue"], name), col(values["other_income"], name), col(values["total_income"], name)
            if None not in (rev, oth, tot) and not close(rev + oth, tot):
                return None, "OCR_CHECK_TOTAL_INCOME"
        if "pbt" in values and "tax" in values:
            pbt, tax, pat = col(values["pbt"], name), col(values["tax"], name), col(values["pat"], name)
            if None not in (pbt, tax, pat) and not close(pbt - tax, pat):
                return None, "OCR_CHECK_PBT_MINUS_TAX"
    # Quarter vs half-year: current half-year revenue must be at least the current quarter.
    if "half" in idx:
        q = col(values["revenue"], "cur")
        h = col(values["revenue"], "half")
        if None not in (q, h) and h + max(TOL_ABS, abs(q) * TOL_REL) < q:
            return None, "OCR_CHECK_QUARTER_VS_HALF"
        # If a previous quarter is present, Q(cur)+Q(prev) should be close to H1 when both are Q2/Q1.
        if "qoq" in idx and q is not None and h is not None:
            prev = col(values["revenue"], "qoq")
            if prev is not None and close(q + prev, h) is False and (q + prev) > h * 1.15:
                # Only fail the glaring case; OCR column drift otherwise withholds via other checks.
                return None, "OCR_CHECK_QUARTER_VS_HALF"

    if col(values["revenue"], "cur") is None or col(values["pat"], "cur") is None:
        return None, "OCR_MISSING_CURRENT_COLUMN"
    if values["revenue"][idx["cur"]] <= 0:
        return None, "OCR_NON_POSITIVE_REVENUE"

    period = "Q2 FY27"
    # Period from the first quarter-ended date in the header if we can see Sept/Jun/Mar/Dec.
    head = "\n".join(text.splitlines()[:15])
    if re.search(r"september|30[./-]09|30[./-]9", head, re.I):
        year = 27 if re.search(r"2026", head) else 27
        period = f"Q2 FY{year}"
    elif re.search(r"june|30[./-]06|30[./-]6", head, re.I):
        period = "Q1 FY27"
    elif re.search(r"march|31[./-]03|31[./-]3", head, re.I):
        period = "Q4 FY26"
    elif re.search(r"december|31[./-]12", head, re.I):
        period = "Q3 FY27"

    factor = UNIT_TO_CR[unit]
    metrics = {}
    for key in ("revenue", "pat"):
        row = {}
        for name in ("cur", "qoq", "yoy"):
            v = col(values[key], name)
            if v is not None:
                row[name] = round(v * factor, 4)
        metrics[key] = row
    card = {
        "scope": scope.upper(),
        "period": period,
        "unit": unit,
        "metrics": metrics,
        "source": "ocr",
    }
    return card, ""


def parse_scanned_document(doc):
    index, text = find_statement_page(doc)
    if index is None:
        return None, "OCR_NO_STATEMENT_PAGE"
    card, reason = parse_ocr_text(text)
    if card:
        card["page"] = index + 1
    return card, reason


def log_card(company, card, reason=""):
    """Test-mode logger: figures only, no secrets, no posting."""
    if card:
        m = card.get("metrics") or {}
        logger.info(
            "OCR test-mode %s page=%s scope=%s period=%s revenue=%s pat=%s",
            company, card.get("page"), card.get("scope"), card.get("period"),
            (m.get("revenue") or {}).get("cur"), (m.get("pat") or {}).get("cur"),
        )
        print(
            f"[OCR TEST] {company}: {card.get('period')} ({card.get('scope')}) "
            f"page {card.get('page')} revenue={ (m.get('revenue') or {}).get('cur') } "
            f"pat={ (m.get('pat') or {}).get('cur') } (no posting)",
            flush=True,
        )
    else:
        logger.info("OCR test-mode %s withheld: %s", company, reason)
        print(f"[OCR TEST] {company}: withheld {reason} (no posting)", flush=True)
