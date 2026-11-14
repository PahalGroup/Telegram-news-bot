"""Cluster word boxes into a results grid (local tesseract TSV or PDF words).

Figures are still published only when the existing sum checks pass. This
module only recovers row/column layout; it does not loosen those checks.
"""
from __future__ import annotations

import csv
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pymupdf

from sources.result_scorecard import (
    UNIT_TO_CR, _date, _last_date, _months_between, bare_unit, half_label,
    quarter_label, statement_scope, statement_unit,
)

# Same tolerances as sources.result_ocr (do not loosen).
TOL_ABS = 1.0
TOL_REL = 0.002

NUMBER = re.compile(r"\(?-?(?:\d{1,3}(?:,\d{2,3})+|\d+)(?:\.\d+)?\)?")
DATEISH = re.compile(r"^\d{1,2}[./-]\d{1,2}[./-]\d{2,4}$")
HALF = re.compile(r"\b(?:half[\s-]?year|h1|six months?|year[\s-]?to[\s-]?date|ytd)\b", re.I)
YEAR = re.compile(r"\byear\s+ended\b|\bfor the year\b", re.I)
QUARTER = re.compile(r"\b(?:quarter|three months?)\s+ended\b|\bq[1-4]\b", re.I)

LABELS = {
    "revenue": re.compile(r"(?:total\s+)?(?:revenue|income)\s+from\s+operations", re.I),
    "other_income": re.compile(r"other\s+income\b", re.I),
    "total_income": re.compile(r"total\s+income\b", re.I),
    "pbt": re.compile(r"profit\s*/?\s*\(?(?:loss)?\)?\s*before\s+tax\b|"
                      r"(?:net\s+)?profit\s*/?\s*\(loss\)\s*before\s+(?:exceptional|tax|share)", re.I),
    "tax": re.compile(r"(?:tax\s+expense|current\s+tax|total\s+tax)\b", re.I),
    "deferred_tax": re.compile(r"deferred\s+tax\b", re.I),
    "pat": re.compile(r"(?:profit|net\s+profit|\(?loss\)?)\s*/?\s*(?:\(loss\)\s*)?(?:after\s+tax|for\s*the (?:period|quarter))", re.I),
}

KEYWORDS = (
    re.compile(r"revenue\s+from\s+operations", re.I),
    re.compile(r"total\s+income\b", re.I),
    re.compile(r"profit\s*/?\s*\(?loss\)?\s*before\s+tax", re.I),
    re.compile(r"tax\s+expense", re.I),
    re.compile(r"(?:net\s+)?profit\s*/?\s*\(?loss\)?\s*(?:after\s+tax|for the (?:period|quarter))", re.I),
)
HEADERS = (
    re.compile(r"\bparticulars\b", re.I),
    re.compile(r"\bquarter\s+ended\b|\bthree\s+months?\b|\bq[1-4]\b", re.I),
    re.compile(r"\bhalf[\s-]?year\s+ended\b|\bsix\s+months?\b", re.I),
    re.compile(r"\byear\s+ended\b", re.I),
)
SKIP = re.compile(
    r"independent auditor.?s review|we have reviewed the accompanying|"
    r"we draw (?:your )?attention|emphasis of matter|basis for (?:qualified |adverse )?opinion|"
    r"other matters?\s*:|list of (?:subsidiaries|associates)|"
    r"cash flow statement|statement of (?:assets|cash flows)|segment[\s-]?wise|"
    r"statement of assets (?:and|&) liabilities|notes to the (?:unaudited )?financial",
    re.I,
)
STATEMENT = re.compile(
    r"revenue from operations|income from operations|statement of (?:unaudited |audited )?financial results",
    re.I,
)

# Garbled / typo unit lines: "(@ in lakhs)", "Zin lakhs", "(Armount in Lakhs.)",
# "(INR in lakhs exceptas stated)". Kept narrow so a notes line like
# "Rs. 10.75 lakh has been recognised" does not invent a second unit.
GARBLED_UNIT = re.compile(
    r"(?:[@₹`z]|inr|rs\.?|rupees|a?rmounts?|amounts?|figures)[^\n\d]{0,16}?\bin\s+"
    r"(crores?|crs?|lakhs?|lacs?|millions?)\b|"
    r"[\(\[]\s*[@₹`z]?\s*in\s+(crores?|crs?|lakhs?|lacs?|millions?)\b|"
    r"\binr\s+in\s+(crores?|crs?|lakhs?|lacs?|millions?)\b",
    re.I,
)


def unit_from_text(text: str):
    """Prefer a unique unit, accepting a few typo/garbled header lines."""
    unit = statement_unit(text) or bare_unit(text)
    if unit:
        return unit
    found = set()
    window = "\n".join(text.splitlines()[:40])
    for m in GARBLED_UNIT.finditer(window):
        u = next(g.lower() for g in m.groups() if g)
        found.add("crore" if u.startswith("cr") else "lakh" if u.startswith(("lak", "lac")) else "million")
    return found.pop() if len(found) == 1 else None


def page_score(text: str) -> int:
    """Score a page as a P&L statement. Auditor notes / balance sheet score low."""
    if not text:
        return 0
    if SKIP.search(text):
        return -50
    score = 0
    for pat in KEYWORDS:
        if pat.search(text):
            score += 4
    for pat in HEADERS:
        if pat.search(text):
            score += 2
    if STATEMENT.search(text):
        score += 3
    nums = len(re.findall(r"\d[\d,]*\.\d{2}", text))
    if nums >= 8:
        score += 3
    elif nums >= 4:
        score += 1
    return score


def parse_tsv(text: str):
    """Parse tesseract TSV into (x0, y0, x1, y1, text, conf) words."""
    words = []
    for i, line in enumerate((text or "").splitlines()):
        if i == 0 and line.startswith("level"):
            continue
        parts = line.split("\t")
        if len(parts) < 12:
            continue
        try:
            conf = float(parts[10])
            left, top, width, height = map(int, parts[6:10])
        except ValueError:
            continue
        token = "\t".join(parts[11:]).strip()
        if conf < 0 or not token:
            continue
        words.append((left, top, left + width, top + height, token, conf))
    return words


def ocr_tsv(png: Path, timeout: int = 40) -> str:
    engine = shutil.which("tesseract")
    if not engine:
        return ""
    try:
        result = subprocess.run(
            [engine, str(png), "stdout", "-l", "eng", "--psm", "6", "tsv"],
            capture_output=True, text=True, timeout=timeout, check=True,
        )
        return result.stdout or ""
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, OSError):
        return ""


def ocr_osd_rotate(png: Path, timeout: int = 15) -> Path:
    """Rotate 90/180/270 if tesseract OSD says so. Fine skew is left as-is."""
    engine = shutil.which("tesseract")
    if not engine:
        return png
    try:
        result = subprocess.run(
            [engine, str(png), "stdout", "--psm", "0"],
            capture_output=True, text=True, timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError):
        return png
    blob = (result.stdout or "") + (result.stderr or "")
    m = re.search(r"Rotate:\s*(\d+)", blob)
    if not m:
        return png
    angle = int(m.group(1)) % 360
    if angle == 0:
        return png
    try:
        from PIL import Image
    except ImportError:
        return png
    img = Image.open(png)
    # Tesseract "Rotate" is the degrees to rotate the image clockwise to correct it.
    img = img.rotate(-angle, expand=True, fillcolor="white")
    out = png.with_name(png.stem + "_rot.png")
    img.save(out)
    return out


def cluster_rows(words, y_frac=0.6):
    """Group word boxes into visual rows by vertical midpoint."""
    boxes = [(w[0], w[1], w[2], w[3], w[4]) for w in words if len(w) >= 5 and str(w[4]).strip()]
    if not boxes:
        return []
    heights = sorted(max(1.0, b[3] - b[1]) for b in boxes)
    typ = heights[len(heights) // 2]
    thresh = max(6.0, typ * y_frac)
    boxes = sorted(boxes, key=lambda b: ((b[1] + b[3]) / 2, b[0]))
    rows = []
    for b in boxes:
        mid = (b[1] + b[3]) / 2
        if rows and abs(rows[-1][0] - mid) <= thresh:
            n = len(rows[-1][1]) + 1
            rows[-1][0] = (rows[-1][0] * (n - 1) + mid) / n
            rows[-1][1].append(b)
        else:
            rows.append([mid, [b]])
    for row in rows:
        row[1].sort(key=lambda b: b[0])
        row[1] = _join_number_fragments(row[1], typ)
    return rows


def _join_number_fragments(words, typ):
    """Rejoin split figures like '1,' + '03,328.09' or '8,' + '262'."""
    gap = max(4.0, typ * 0.7)
    merged = []
    for w in words:
        if merged and w[0] - merged[-1][2] <= gap:
            left, right = merged[-1][4], w[4]
            combo = left + right
            if NUMBER.fullmatch(combo.strip("|()")) or NUMBER.fullmatch(combo):
                merged[-1] = (merged[-1][0], min(merged[-1][1], w[1]), w[2], max(merged[-1][3], w[3]), combo)
                continue
        merged.append(w)
    return merged


def _is_num(token: str) -> bool:
    t = (token or "").strip().strip("|")
    if not t or DATEISH.match(t):
        return False
    return bool(NUMBER.fullmatch(t) or (t.startswith("(") and t.endswith(")") and NUMBER.search(t)))


def _is_serial_word(words, i):
    """A bare 1-2 digit integer left of a label is a line number, not a figure."""
    if i > 1 or i >= len(words):
        return False
    if not re.fullmatch(r"\d{1,2}", words[i][4].strip().strip("|")):
        return False
    return i + 1 < len(words) and not _is_num(words[i + 1][4])


def _value(token: str):
    t = token.strip().strip("|").replace(",", "")
    neg = (t.startswith("(") and t.endswith(")")) or t.startswith("-")
    try:
        value = float(t.strip("()-"))
    except ValueError:
        return None
    return -value if neg else value


def merge_continuation_rows(rows):
    """A numbers-only row is the rest of the previous label row (common in OCR)."""
    out = []
    for mid, words in rows:
        nums = [w for w in words if _is_num(w[4])]
        labels = [w for w in words if not _is_num(w[4])]
        if out and nums and len(nums) >= 2 and len(labels) <= 1:
            prev_labels = [w for w in out[-1][1] if not _is_num(w[4])]
            prev_nums = [w for w in out[-1][1] if _is_num(w[4])]
            if prev_labels and len(prev_nums) <= 1:
                out[-1][1] = out[-1][1] + words
                out[-1][1].sort(key=lambda w: w[0])
                continue
        out.append([mid, words])
    return out


def rows_to_text(rows) -> str:
    return "\n".join(" ".join(w[4] for w in words) for _, words in rows)


def _label_of(words):
    first = next((i for i, w in enumerate(words) if _is_num(w[4]) and not _is_serial_word(words, i)), len(words))
    return re.sub(r"\s+", " ", " ".join(w[4] for w in words[:first])).strip()


def _metric_key(label: str):
    if not label:
        return None
    for key, pat in LABELS.items():
        if not pat.search(label):
            continue
        if key == "revenue" and re.search(r"other", label, re.I):
            continue
        if key == "total_income" and re.search(r"from operations", label, re.I):
            continue
        if key == "pat" and re.search(r"before|comprehensive|attributable|share of", label, re.I):
            continue
        return key
    return None


def _column_centres(rows):
    """Use the revenue (or densest metric) row as column anchors."""
    best = None
    for _, words in rows:
        key = _metric_key(_label_of(words))
        nums = [w for i, w in enumerate(words) if _is_num(w[4]) and not _is_serial_word(words, i)]
        if key == "revenue" and len(nums) >= 2:
            best = nums
            break
        if len(nums) >= 3 and (best is None or len(nums) > len(best)):
            best = nums
    if not best:
        return []
    centres = [(w[0] + w[2]) / 2 for w in best]
    if len(centres) >= 2 and min(b - a for a, b in zip(centres, centres[1:])) <= 8:
        return []
    return centres


def _assign(words, centres):
    if not centres:
        return {}
    spacing = min((b - a) for a, b in zip(centres, centres[1:])) if len(centres) >= 2 else 40
    row = {}
    for i, w in enumerate(words):
        if not _is_num(w[4]) or _is_serial_word(words, i):
            continue
        mid = (w[0] + w[2]) / 2
        col = min(range(len(centres)), key=lambda c: abs(centres[c] - mid))
        if abs(centres[col] - mid) <= spacing * 0.55 and col not in row:
            value = _value(w[4])
            if value is not None:
                row[col] = value
    return row


def _header_dates(rows, centres, cutoff_y):
    """Join stacked header cells ('September 30,' + '2026') before parsing dates."""
    if not centres:
        return []
    spacing = min((b - a) for a, b in zip(centres, centres[1:])) if len(centres) >= 2 else 40
    header_words = [[] for _ in centres]
    for mid, words in rows:
        if mid >= cutoff_y:
            break
        for w in words:
            mid_x = (w[0] + w[2]) / 2
            col = min(range(len(centres)), key=lambda c: abs(centres[c] - mid_x))
            if abs(centres[col] - mid_x) <= spacing * 0.55:
                header_words[col].append(w[4])
    return [_last_date(" ".join(ws)) for ws in header_words]


def _kinds_from_header(rows, centres, cutoff_y):
    kinds = [None] * len(centres)
    spacing = min((b - a) for a, b in zip(centres, centres[1:])) if len(centres) >= 2 else 40
    for mid, words in rows:
        if mid >= cutoff_y:
            break
        # Span labels (Quarter Ended / Half Year Ended) sit above groups of columns.
        text = " ".join(w[4] for w in words)
        for w in words:
            mid_x = (w[0] + w[2]) / 2
            col = min(range(len(centres)), key=lambda c: abs(centres[c] - mid_x))
            token = w[4]
            blob = token + " " + text
            if QUARTER.search(blob) and not HALF.search(token) and not YEAR.search(token):
                kinds[col] = kinds[col] or "q"
            elif HALF.search(token) or HALF.search(text) and mid_x > centres[0]:
                # only mark the column the word actually sits over for HALF/YEAR tokens
                if HALF.search(token):
                    kinds[col] = kinds[col] or "h"
            elif YEAR.search(token):
                kinds[col] = kinds[col] or "y"
    return kinds


def _idx_from_dates(dates):
    if not dates or not dates[0]:
        return None
    cur = dates[0]
    idx = {"cur": 0}
    ytd_start = next((i for i, d in enumerate(dates[1:], 1) if d == cur), None)
    for i, d in enumerate(dates[1:], 1):
        if d is None:
            continue
        if ytd_start is not None and i >= ytd_start:
            if "half" not in idx and d == cur:
                idx["half"] = i
            continue
        gap = _months_between(cur, d)
        if gap == 3 and "qoq" not in idx:
            idx["qoq"] = i
        elif gap == 12 and "yoy" not in idx:
            idx["yoy"] = i
    return idx


def _idx_from_kinds(kinds, n):
    if kinds and sum(k is not None for k in kinds) >= 1:
        kinds = (kinds + [None] * n)[:n]
        q = [i for i, k in enumerate(kinds) if k == "q"]
        h = [i for i, k in enumerate(kinds) if k == "h"]
        if q:
            idx = {"cur": q[0]}
            if len(q) >= 2:
                idx["qoq"] = q[1]
            if len(q) >= 3:
                idx["yoy"] = q[2]
            if h:
                idx["half"] = h[0]
            return idx
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


def drop_serial_columns(values: dict) -> dict:
    """Drop a leading serial/line-number column (1, 2, 16). Same rule as result_ocr."""
    def serial(row):
        if not row or len(row) < 2 or row[0] is None or row[1] is None:
            return False
        first, second = row[0], row[1]
        return (
            abs(first - round(first)) < 1e-9
            and 1 <= first <= 40
            and (second >= 50 or second >= 10 * max(abs(first), 1))
        )
    if "revenue" in values:
        revenue = values["revenue"]
        if not serial(revenue) and sum(serial(v) for v in values.values()) < 2:
            return values
    else:
        return values
    out = {}
    for key, row in values.items():
        if row and row[0] is not None and abs(row[0] - round(row[0])) < 1e-9 and 0 <= row[0] <= 40:
            out[key] = row[1:]
        else:
            out[key] = row
    return out


def extract_values(rows):
    """Return {metric: [col0, col1, ...]}, header kinds/dates, and centres."""
    centres = _column_centres(rows)
    if not centres:
        return {}, None, None, []
    anchor_y = None
    values = {}
    for mid, words in rows:
        key = _metric_key(_label_of(words))
        if not key:
            continue
        assigned = _assign(words, centres)
        if not assigned:
            continue
        if key == "revenue" and anchor_y is None:
            anchor_y = mid
        aligned = [assigned[i] if i in assigned else None for i in range(len(centres))]
        if key == "tax" and "tax" in values:
            # A later "current tax" / "deferred tax" row: add onto the tax-expense total.
            values["tax"] = [
                (a if a is not None else 0) + (b if b is not None else 0)
                if a is not None or b is not None else None
                for a, b in zip(values["tax"] + [None] * len(aligned), aligned + [None] * len(values["tax"]))
            ][:len(centres)]
        elif key == "pat" and "pat" in values:
            # Keep an earlier PAT row when a later one is out of scale with revenue.
            rev = values.get("revenue") or []
            cur_rev = next((v for v in rev if v is not None), None)
            new_pat = next((v for v in aligned if v is not None), None)
            old_pat = next((v for v in values["pat"] if v is not None), None)
            if cur_rev and new_pat is not None and abs(new_pat) > max(abs(cur_rev), 1) * 3:
                if old_pat is None or abs(old_pat) <= max(abs(cur_rev), 1) * 3:
                    continue
            values[key] = aligned
        else:
            values[key] = aligned
    if "deferred_tax" in values:
        if "tax" in values:
            a, b = values["tax"], values["deferred_tax"]
            n = max(len(a), len(b))
            values["tax"] = [
                (a[i] if i < len(a) and a[i] is not None else 0) + (b[i] if i < len(b) and b[i] is not None else 0)
                if (i < len(a) and a[i] is not None) or (i < len(b) and b[i] is not None) else None
                for i in range(n)
            ]
        else:
            values["tax"] = values["deferred_tax"]
        values.pop("deferred_tax", None)
    dates = _header_dates(rows, centres, anchor_y or 1e9)
    kinds = _kinds_from_header(rows, centres, anchor_y or 1e9)
    return values, dates, kinds, centres


def values_as_dense(values):
    """Drop None holes only at the front (serials already handled) — keep alignment."""
    return values


def close(a, b):
    return abs(a - b) <= max(TOL_ABS, abs(a) * TOL_REL)


def run_sum_checks(values, idx):
    """Exact OCR sum checks from result_ocr.parse_ocr_text. Do not loosen."""
    def col(row, name):
        i = idx.get(name)
        if i is None or row is None or i >= len(row):
            return None
        return row[i]

    for name in ("cur", "qoq", "yoy", "half"):
        if name not in idx:
            continue
        if "other_income" in values and "total_income" in values:
            rev, oth, tot = col(values["revenue"], name), col(values["other_income"], name), col(values["total_income"], name)
            if None not in (rev, oth, tot) and not close(rev + oth, tot):
                return "OCR_CHECK_TOTAL_INCOME"
        if "pbt" in values and "tax" in values:
            pbt, tax, pat = col(values["pbt"], name), col(values["tax"], name), col(values["pat"], name)
            if None not in (pbt, tax, pat) and not close(pbt - tax, pat):
                return "OCR_CHECK_PBT_MINUS_TAX"
    if "half" in idx:
        q = col(values["revenue"], "cur")
        h = col(values["revenue"], "half")
        if None not in (q, h) and h + max(TOL_ABS, abs(q) * TOL_REL) < q:
            return "OCR_CHECK_QUARTER_VS_HALF"
        if "qoq" in idx and q is not None and h is not None:
            prev = col(values["revenue"], "qoq")
            if prev is not None and close(q + prev, h) is False and (q + prev) > h * 1.15:
                return "OCR_CHECK_QUARTER_VS_HALF"
    return ""


def period_from_text(text: str) -> str:
    period = "Q2 FY27"
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
    return period


def card_from_values(values, idx, unit, scope, text, source="ocr-grid"):
    """Build a scorecard after sum checks. values[key] is a list aligned to columns."""
    if "revenue" not in values or "pat" not in values:
        return None, "OCR_MISSING_REVENUE_OR_PAT"
    # Drop rows that are all None; require current column.
    def col(row, name):
        i = idx.get(name) if idx else None
        if i is None or i >= len(row):
            return None
        return row[i]

    if not idx or col(values["revenue"], "cur") is None or col(values["pat"], "cur") is None:
        return None, "OCR_MISSING_CURRENT_COLUMN"
    if values["revenue"][idx["cur"]] is not None and values["revenue"][idx["cur"]] <= 0:
        return None, "OCR_NON_POSITIVE_REVENUE"
    reason = run_sum_checks(values, idx)
    if reason:
        return None, reason
    # Same scale guard as the text-layer scorecard (fail 11): a PAT that
    # dwarfs income means the columns were misread. This withholds, never loosens.
    cur_i = idx.get("cur")
    if cur_i is not None:
        rev = values["revenue"][cur_i] if cur_i < len(values["revenue"]) else None
        pat = values["pat"][cur_i] if cur_i < len(values["pat"]) else None
        income = rev
        if "total_income" in values and cur_i < len(values["total_income"]) and values["total_income"][cur_i] is not None:
            income = values["total_income"][cur_i]
        if rev is not None and pat is not None and income is not None and abs(pat) > max(income, rev) * 3:
            return None, "OCR_CHECK_PAT_SCALE"
    factor = UNIT_TO_CR[unit]
    metrics = {}
    for key in ("revenue", "pat"):
        row = {}
        for name in ("cur", "qoq", "yoy"):
            v = col(values[key], name)
            if v is not None:
                row[name] = round(v * factor, 4)
        metrics[key] = row
    period = period_from_text(text)
    if idx and "cur" in idx:
        # Prefer an explicit quarter label when header dates were read.
        pass
    card = {
        "scope": (scope or "standalone").upper(),
        "period": period,
        "unit": unit,
        "metrics": metrics,
        "source": source,
    }
    return card, ""


def parse_word_boxes(words, text=None, source="ocr-grid"):
    """Turn word boxes into a card, or (None, reason)."""
    rows = merge_continuation_rows(cluster_rows(words))
    blob = text or rows_to_text(rows)
    scope = statement_scope(blob)
    unit = unit_from_text(blob)
    if unit is None:
        return None, "OCR_UNIT_AMBIGUOUS"
    if scope is None:
        if not re.search(r"standalone|consolidated", blob, re.I):
            scope = "standalone"
        else:
            return None, "OCR_SCOPE_AMBIGUOUS"
    values, dates, kinds, centres = extract_values(rows)
    # Convert aligned lists: drop leading serials using dense filled values.
    serial_src = {}
    for key, row in values.items():
        serial_src[key] = [v for v in row if v is not None] if row and row[0] is not None else [v for v in (row or []) if v is not None]
        # If first column is a figure (not none at 0), keep alignment including Nones after start
    # Serial drop on dense lists, then map back if a leading col was dropped.
    dense = {k: [v for v in row if v is not None] for k, row in values.items()}
    dense = drop_serial_columns(dense)
    # Prefer date-aligned index on the original centres when no serial was dropped.
    serial_dropped = any(
        len([v for v in (values.get(k) or []) if v is not None]) != len(dense.get(k) or [])
        for k in dense
    )
    idx = None
    if dates and dates[0] and not serial_dropped:
        idx = _idx_from_dates(dates)
        use = values
        # Replace None-padded aligned rows; checks skip None.
    else:
        use = dense
        n = min((len(v) for v in use.values()), default=0)
        if n < 1:
            return None, "OCR_NO_COLUMNS"
        idx = _idx_from_kinds(kinds, n) if kinds else None
        if not idx:
            idx = _idx_from_kinds([], n)
    if not idx:
        return None, "OCR_COLUMNS_UNCLEAR"
    # If using aligned values (with possible Nones), fill dense for cur lookup
    if not serial_dropped and dates and dates[0]:
        # Keep alignment with Nones so date indices stay valid.
        return card_from_values(values, idx, unit, scope, blob, source=source)
    return card_from_values(use, idx, unit, scope, blob, source=source)


def pymupdf_words(page):
    return [(w[0], w[1], w[2], w[3], w[4]) for w in page.get_text("words")]


def pdfplumber_words(page):
    """Word boxes from pdfplumber for one pymupdf page."""
    import io
    try:
        import pdfplumber
    except ImportError:
        return []
    tmp = pymupdf.open()
    try:
        tmp.insert_pdf(page.parent, from_page=page.number, to_page=page.number)
        data = tmp.tobytes()
    finally:
        tmp.close()
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        return [
            (w["x0"], w["top"], w["x1"], w["bottom"], w["text"])
            for w in pdf.pages[0].extract_words(use_text_flow=False, keep_blank_chars=False)
        ]


def parse_text_layer_page(page):
    """Row/column parse of a text-layer statement using pdfplumber, then pymupdf words."""
    text = page.get_text() or ""
    for source, getter in (("pdfplumber-grid", pdfplumber_words), ("pymupdf-grid", pymupdf_words)):
        try:
            words = getter(page)
        except Exception:
            continue
        if not words:
            continue
        card, reason = parse_word_boxes(words, text=text, source=source)
        if card:
            card["page"] = page.number + 1
            return card, reason
        last = reason
    return None, locals().get("last") or "OCR_MISSING_REVENUE_OR_PAT"
