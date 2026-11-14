"""Parse a results statement into a scorecard with computed YoY/QoQ change.

Arithmetic is done only on explicitly labelled rows whose columns carry explicit
quarter-end dates, on a page that states one scope (standalone/consolidated) and
one unit. Anything ambiguous returns None so the caller withholds the scorecard.
"""
import re
from datetime import date

UNIT_TO_CR = {"crore": 1.0, "lakh": 0.01, "million": 0.1, "thousand": 0.0001}
UNIT = re.compile(r"(?:₹|rs\.?|inr|rupees)?\s*\(?\s*(?:in|amount in|all amounts in|figures in)?\s*"
                  r"(?:₹|rs\.?|inr|rupees)?\s*\.?\s*(crores?|cr\b|lakhs?|lacs?|millions?|mn\b|thousands?|'?000)", re.I)
DATE = re.compile(r"(?P<d1>\d{1,2})[./,-](?P<m1>\d{1,2})[./,-](?P<y1>\d{4}|\d{2}(?!\d))"
                  r"|(?P<d2>\d{1,2})[-\s](?P<m2>[A-Za-z]{3})[-\s'’](?P<y2>\d{2})\b"
                  r"|(?P<d3>\d{1,2})(?:st|nd|rd|th)?[\s-]+(?P<m3>[A-Za-z]{3,9})[,\s-]+(?P<y3>\d{4})"
                  r"|(?P<m4>[A-Za-z]{3,9})\s+(?P<d4>\d{1,2}),?\s+(?P<y4>\d{4})")
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
NUM = re.compile(r"^(?:-?(?:\d{1,3}(?:,\d{2,3})+|\d+)(?:\.\d+)?|\((?:\d{1,3}(?:,\d{2,3})+|\d+)(?:\.\d+)?\)|[-–—])$")

ROWS = {
    "revenue": re.compile(r"^(?:[ivx]+[.)]?\s*|\d+[.)]?\s*|[a-z][.)]\s*)?(?:total\s+)?(?:revenue|income)\s+from\s+operations(?:\s*\(net\))?\b", re.I),
    "other_income": re.compile(r"^(?:[ivx]+[.)]?\s*|\d+[.)]?\s*|[a-z][.)]\s*)?other\s+income\b", re.I),
    "total_income": re.compile(r"^(?:[ivx]+[.)]?\s*|\d+[.)]?\s*|[a-z][.)]\s*)?total\s+income\b", re.I),
    "ebitda": re.compile(r"^(?:[ivx]+[.)]?\s*|\d+[.)]?\s*)?(?:ebitda|earnings before interest,? tax(?:es)?,? depreciation)", re.I),
    "finance": re.compile(r"^(?:[a-z\d]+[.)]\s*)?finance\s+costs?\b", re.I),
    "dep": re.compile(r"^(?:[a-z\d]+[.)]\s*)?depreciation\b", re.I),
    "pbt": re.compile(r"^(?:[ivx]+[.)]?\s*|\d+[.)]?\s*)?(?:net\s+)?profit\s*/?\s*(?:\(loss\)\s*)?before\s+(?:exceptional|share\s+of)", re.I),
    "pat": re.compile(r"^(?:[ivx]+[.)]?\s*|\d+[.)]?\s*)?(?:net\s+)?(?:profit|\(?loss\)?)\s*/?\s*(?:\(loss\)\s*)?(?:after\s+tax|for\s+the\s+(?:period|quarter|year))\b|"
                      r"^(?:[ivx]+[.)]?\s*|\d+[.)]?\s*)?net\s+profit\s*/?\s*(?:\(loss\))?\s*$", re.I),
}


UNIT_PHRASE = re.compile(r"(?:₹|`|\brs\b\.?|\binr\b|rupees|amount|figures|\bin\b|\()[^\n\d]{0,20}?\b(crores?|cr|lakhs?|lacs?|lakh|millions?|mn|thousands?)\b", re.I)


def statement_unit(text):
    units = set()
    for m in UNIT_PHRASE.finditer(text):
        u = m.group(1).lower()
        units.add("crore" if u.startswith("cr") else "lakh" if u.startswith(("lak", "lac")) else
                  "million" if u.startswith(("mil", "mn")) else "thousand")
    return units.pop() if len(units) == 1 else None


def bare_unit(text):
    """A unit printed on its own, e.g. a column note 'Crores' beside a rupee glyph."""
    found = set()
    for line in text.splitlines()[:40]:
        m = re.fullmatch(r"\W*(?:\S\s*)?(?:in\s+)?(crores?|lakhs?|lacs?|millions?)\W*", line.strip(), re.I)
        if m:
            u = m.group(1).lower()
            found.add("crore" if u.startswith("cr") else "lakh" if u.startswith(("lak", "lac")) else "million")
    return found.pop() if len(found) == 1 else None


def statement_scope(text):
    """Scope from the statement title; a title naming both scopes is ambiguous."""
    for line in text.splitlines():
        found = {s.lower() for s in re.findall(r"\b(standalone|consolidated)\b", line, re.I)}
        if len(found) == 1 and re.search(r"statement|results|financial", line, re.I):
            return found.pop()
        if len(found) > 1:
            return None
    found = {s.lower() for s in re.findall(r"\b(standalone|consolidated)\b", text, re.I)}
    return found.pop() if len(found) == 1 else None


def _last_date(text):
    """A period heading such as '01/07/2026-30/09/2026' ends on its last date."""
    found = [d for d in (_date(m.group(0)) for m in DATE.finditer(text)) if d]
    return found[-1] if found else None


def _date(text):
    m = DATE.search(text)
    if not m:
        return None
    g = m.groupdict()
    try:
        if g["d1"]:
            y = int(g["y1"])
            return date(y + 2000 if y < 100 else y, int(g["m1"]), int(g["d1"]))
        if g["d2"]:
            return date(2000 + int(g["y2"]), MONTHS[g["m2"].lower()], int(g["d2"]))
        if g["d3"]:
            return date(int(g["y3"]), MONTHS[g["m3"][:3].lower()], int(g["d3"]))
        return date(int(g["y4"]), MONTHS[g["m4"][:3].lower()], int(g["d4"]))
    except (KeyError, ValueError):
        return None


def _value(token):
    t = token.replace(",", "")
    if t in ("-", "–", "—"):
        return 0.0
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()")
    return -float(t) if neg else float(t)


def _lines(page):
    """Group words into visual lines: (y, [(x0, x1, text)])."""
    words = sorted(page.get_text("words"), key=lambda w: (round(w[1]), w[0]))
    lines = []
    for w in words:
        mid = (w[1] + w[3]) / 2
        if lines and abs(lines[-1][0] - mid) <= 3:
            lines[-1][1].append((w[0], w[2], w[4]))
        else:
            lines.append([mid, [(w[0], w[2], w[4])]])
    for line in lines:
        line[1].sort()
        merged = []
        for w in line[1]:
            # PDF text runs can split one figure ("281 .93", "7 28"); rejoin
            # only when the pieces touch and the result is a single number.
            if merged and w[0] - merged[-1][1] < 2.5 and NUM.match(merged[-1][2] + w[2]) \
                    and re.search(r"\d", merged[-1][2]):
                merged[-1] = (merged[-1][0], w[1], merged[-1][2] + w[2])
            else:
                merged.append(w)
        line[1] = merged
    return lines


ENUM = re.compile(r"^(?:[\(\[]?(?:[a-h]|[ivx]{1,4}|\d{1,2})[\)\].]|\d{1,2}|[ivx]{1,4})\s+", re.I)


def _strip_enum(label):
    label = re.sub(r"\s+", " ", label).strip()
    for _ in range(2):
        label = ENUM.sub("", label)
    return label


def _is_serial(words, i):
    """A bare 1-2 digit integer right after the start is a serial number, not a figure."""
    return i <= 1 and re.fullmatch(r"\d{1,2}", words[i][2]) is not None and len(words) > i + 2 and \
        not NUM.match(words[i + 1][2])


def half_label(d):
    h = {9: 1, 3: 2}.get(d.month)
    if h is None:
        return None
    fy = d.year + 1 if d.month > 3 else d.year
    return f"H{h} FY{fy % 100:02d}"


def _same_kind(a, b):
    """Quarter columns must not be compared with half-year/nine-month columns."""
    def kind(ws):
        t = " ".join(ws).lower()
        if re.search(r"half|six|6\s*months|nine|9\s*months|year\s+ended|twelve|12\s*months", t):
            return "long"
        return "quarter"
    return kind(a) == kind(b)


def quarter_label(d):
    q = {6: 1, 9: 2, 12: 3, 3: 4}.get(d.month)
    if q is None:
        return None
    fy = d.year + 1 if d.month > 3 else d.year
    return f"Q{q} FY{fy % 100:02d}"


def _months_between(a, b):
    return (a.year - b.year) * 12 + a.month - b.month


LAST_FAIL = [0]


def _fail(code):
    LAST_FAIL[0] = code
    return None


def parse_page(page, doc_has_consolidated=True):
    text = page.get_text()
    head = text[:3000]
    scope = statement_scope(head)
    if scope is None and not doc_has_consolidated and not re.search(r"standalone|consolidated", text, re.I):
        # An entity without subsidiaries files only standalone results.
        scope = "standalone"
    if scope is None:
        return _fail(1)
    unit = statement_unit(head) or statement_unit(text) or bare_unit(text)
    if unit is None:
        return _fail(3)
    lines = _lines(page)
    labelled = []
    for y, words in lines:
        nums = [w for w in words if NUM.match(w[2])]
        # A row's label is the text left of its first figure.
        first = next((i for i, w in enumerate(words) if NUM.match(w[2]) and not _is_serial(words, i)), None)
        label = " ".join(w[2] for w in words[:first]) if first is not None else " ".join(w[2] for w in words)
        labelled.append((y, words, _strip_enum(label), words[first:] if first is not None else []))
    def is_revenue(i, r):
        if not ROWS["revenue"].search(r[2]) or re.search(r"other", r[2], re.I):
            return False
        # "Total income from operations" printed after an Other income row is
        # a sum including other income, not revenue.
        if re.match(r"total", r[2], re.I) and any(ROWS["other_income"].search(q[2]) for q in labelled[:i]):
            return False
        return True
    revenue_rows = {i for i, r in enumerate(labelled) if is_revenue(i, r)}
    anchor = next((r for i, r in enumerate(labelled) if i in revenue_rows and len(r[3]) >= 3), None)
    if anchor is None:
        return _fail(41)
    centres = [(w[0] + w[1]) / 2 for w in anchor[3]]
    spacing = min(b - a for a, b in zip(centres, centres[1:]))
    if spacing <= 10:
        return _fail(42)
    # Column headings: words above the first figure row, assigned by x overlap.
    header_words = [[] for _ in centres]
    for y, words, _, _ in labelled:
        if y >= anchor[0]:
            break
        for w in words:
            mid = (w[0] + w[1]) / 2
            col = min(range(len(centres)), key=lambda c: abs(centres[c] - mid))
            if abs(centres[col] - mid) <= spacing * 0.5:
                header_words[col].append(w[2])
    dates = [_last_date(" ".join(ws)) for ws in header_words]
    if not dates[0]:
        return _fail(43)
    cur = dates[0]
    idx = {"cur": 0}
    # SEBI layout: quarter columns first, then year-to-date columns. The
    # first later column ending on the current date opens the YTD block, so
    # quarter comparisons must come from columns left of it. Spanning headings
    # ("Half year ended") often land over a quarter column, so position is
    # more reliable than heading words here.
    ytd_start = next((i for i, d in enumerate(dates[1:], 1) if d == cur), None)
    for i, d in enumerate(dates[1:], 1):
        if d is None:
            continue
        if ytd_start is not None:
            if i >= ytd_start:
                break
            gap = _months_between(cur, d)
            if gap == 3 and "qoq" not in idx:
                idx["qoq"] = i
            elif gap == 12 and "yoy" not in idx:
                idx["yoy"] = i
            continue
        gap = _months_between(cur, d)
        if gap == 3 and "qoq" not in idx and _same_kind(header_words[0], header_words[i]):
            idx["qoq"] = i
        elif gap == 12 and "yoy" not in idx and _same_kind(header_words[0], header_words[i]):
            idx["yoy"] = i
    head0 = " ".join(header_words[0]).lower()
    half = not re.search(
        r"q\s?uarter|three\s+months|3\s*months", text, re.I)
    if half:
        idx.pop("qoq", None)
    period = half_label(cur) if half else quarter_label(cur)
    if "yoy" not in idx or period is None:
        return _fail(5)
    values = {}
    for n, (y, words, label, nums) in enumerate(labelled):
        if y < anchor[0] or not label:
            continue
        for key, pattern in ROWS.items():
            # Net profit is the last profit-for-the-period row: earlier ones
            # can be "from continuing operations" before discontinued items.
            if (key in values and key != "pat") or not pattern.search(label):
                continue
            if key == "revenue" and n not in revenue_rows:
                continue
            if key == "pat" and re.search(r"before|attributable|comprehensive|discontinued|share of|exceptional", label, re.I):
                continue
            if key == "revenue" and re.search(r"other", label, re.I):
                continue
            row = {}
            for x0, x1, t in nums:
                if x1 < centres[0] - spacing * 0.5:
                    continue  # formula notes such as "(5 ± 6)" sit in the label column
                if not NUM.match(t):
                    return _fail(6)  # unreadable digits (e.g. OCR text layer): withhold the page
                mid = (x0 + x1) / 2
                col = min(range(len(centres)), key=lambda c: abs(centres[c] - mid))
                if abs(centres[col] - mid) <= spacing * 0.5 and col not in row:
                    row[col] = _value(t)
            if all(i in row for i in idx.values()):
                values[key] = {name: row[i] for name, i in idx.items()}
            break
    if "revenue" not in values or "pat" not in values:
        return _fail(7)
    rev = values["revenue"]
    # Consistency guard: revenue + other income must equal total income.
    if "other_income" in values and "total_income" in values:
        for k in idx:
            expect = values["revenue"][k] + values["other_income"][k]
            if abs(expect - values["total_income"][k]) > max(1.0, abs(expect) * 0.002):
                return _fail(8)
    if any(rev[k] <= 0 for k in idx):
        return _fail(9)
    if "ebitda" not in values and all(k in values for k in ("pbt", "finance", "dep", "other_income")):
        values["ebitda"] = {k: values["pbt"][k] + values["finance"][k] + values["dep"][k] - values["other_income"][k] for k in idx}
        values["ebitda_derived"] = True
    # Cross-row sanity: operating profit cannot exceed revenue, and profit
    # cannot dwarf income. A failure means columns or rows were misread.
    for k in idx:
        if "ebitda" in values and values["ebitda"][k] > rev[k] * 1.0001:
            return _fail(10)
        income = values.get("total_income", rev)[k]
        if abs(values["pat"][k]) > max(income, rev[k]) * 3:
            return _fail(11)
    factor = UNIT_TO_CR[unit]
    out = {"scope": scope.upper(), "period": period, "period_end": cur.isoformat(),
           "unit": unit, "page": page.number + 1, "metrics": {}}
    for key in ("revenue", "ebitda", "pat"):
        if key in values:
            out["metrics"][key] = {k: round(v * factor, 4) for k, v in values[key].items()}
    out["ebitda_derived"] = bool(values.get("ebitda_derived"))
    return out



def _cell(c):
    return re.sub(r"\s+", " ", c or "").strip()


def _scope_spans(rows):
    """Column ranges for Standalone / Consolidated groups in a SEBI results grid."""
    spans = []
    width = max((len(r) for r in rows), default=0)
    for row in rows:
        cells = [_cell(c) for c in row]
        found = []
        for i, c in enumerate(cells):
            if re.fullmatch(r"standalone", c, re.I):
                found.append(("standalone", i))
            elif re.fullmatch(r"consolidated", c, re.I):
                found.append(("consolidated", i))
        if found:
            found.sort(key=lambda x: x[1])
            for n, (scope, start) in enumerate(found):
                end = found[n + 1][1] if n + 1 < len(found) else width
                if end - start >= 3:
                    spans.append((scope, start, end))
            if spans:
                return spans
    return []


def _date_row(rows, start, end):
    best = None
    for row in rows:
        cells = [_cell(c) for c in row]
        dates = [(_last_date(cells[i]) if i < len(cells) else None) for i in range(start, end)]
        n = sum(d is not None for d in dates)
        if n >= 3 and (best is None or n > best[0]):
            best = (n, dates)
    return best[1] if best else None


def _label_and_nums(row, start, end):
    cells = [_cell(c) for c in row]
    label = _strip_enum(" ".join(c for c in cells[:start] if c and not NUM.match(c)))
    nums = {}
    for i in range(start, min(end, len(cells))):
        t = cells[i].replace(" ", "")
        if t and NUM.match(t):
            nums[i - start] = _value(t)
    return label, nums


def parse_statement_table(page):
    """Read a results grid, including a side-by-side standalone+consolidated statement.

    Prefers the consolidated column group when both scopes are on the page.
    """
    text = page.get_text()
    unit = statement_unit(text[:3000]) or statement_unit(text) or bare_unit(text)
    if unit is None:
        return _fail(3)
    try:
        found = page.find_tables()
        tables = found.tables if found else []
    except Exception:
        return None
    for table in tables:
        card = _card_from_grid(table.extract(), unit, page.number + 1, text)
        if card:
            return card
    return None


def _card_from_grid(rows, unit, page_no, text):
    if not rows or len(rows) < 6:
        return None
    spans = _scope_spans(rows)
    if not spans:
        return None
    spans.sort(key=lambda s: s[0] != "consolidated")
    scope, start, end = spans[0]
    dates = _date_row(rows, start, end)
    if not dates or not dates[0]:
        return _fail(43)
    cur = dates[0]
    idx = {"cur": 0}
    ytd_start = next((i for i, d in enumerate(dates[1:], 1) if d == cur), None)
    for i, d in enumerate(dates[1:], 1):
        if d is None:
            continue
        if ytd_start is not None and i >= ytd_start:
            break
        gap = _months_between(cur, d)
        if gap == 3 and "qoq" not in idx:
            idx["qoq"] = i
        elif gap == 12 and "yoy" not in idx:
            idx["yoy"] = i
    half = not re.search(r"q\s?uarter|three\s+months|3\s*months", text, re.I)
    if half:
        idx.pop("qoq", None)
    period = half_label(cur) if half else quarter_label(cur)
    if "yoy" not in idx or period is None:
        return _fail(5)
    values = {}
    for row in rows:
        label, nums = _label_and_nums(row, start, end)
        if not label or not nums:
            continue
        for key, pattern in ROWS.items():
            if (key in values and key != "pat") or not pattern.search(label):
                continue
            if key == "pat" and re.search(r"before|attributable|comprehensive|discontinued|share of|exceptional", label, re.I):
                continue
            if key == "revenue" and re.search(r"other", label, re.I):
                continue
            if key == "revenue" and re.match(r"total", label, re.I):
                continue
            if all(i in nums for i in idx.values()):
                values[key] = {name: nums[i] for name, i in idx.items()}
            break
    if "revenue" not in values or "pat" not in values:
        return _fail(7)
    rev = values["revenue"]
    if "other_income" in values and "total_income" in values:
        for k in idx:
            expect = values["revenue"][k] + values["other_income"][k]
            if abs(expect - values["total_income"][k]) > max(1.0, abs(expect) * 0.002):
                return _fail(8)
    if any(rev[k] <= 0 for k in idx):
        return _fail(9)
    if "ebitda" not in values and all(k in values for k in ("pbt", "finance", "dep", "other_income")):
        values["ebitda"] = {k: values["pbt"][k] + values["finance"][k] + values["dep"][k] - values["other_income"][k] for k in idx}
        values["ebitda_derived"] = True
    for k in idx:
        if "ebitda" in values and values["ebitda"][k] > rev[k] * 1.0001:
            return _fail(10)
        income = values.get("total_income", rev)[k]
        if abs(values["pat"][k]) > max(income, rev[k]) * 3:
            return _fail(11)
    factor = UNIT_TO_CR[unit]
    out = {"scope": scope.upper(), "period": period, "period_end": cur.isoformat(),
           "unit": unit, "page": page_no, "metrics": {}}
    for key in ("revenue", "ebitda", "pat"):
        if key in values:
            out["metrics"][key] = {k: round(v * factor, 4) for k, v in values[key].items()}
    out["ebitda_derived"] = bool(values.get("ebitda_derived"))
    return out


def parse_document(doc, max_pages=12):
    """Prefer a consolidated statement; fall back to standalone."""
    found = []
    has_cons = any(re.search(r"consolidat", doc[i].get_text(), re.I) for i in range(min(len(doc), max_pages)))
    for i in range(min(len(doc), max_pages)):
        card = parse_page(doc[i], has_cons)
        if not card:
            card = parse_statement_table(doc[i])
        if card:
            found.append(card)
    if not found:
        return None
    found.sort(key=lambda c: c["scope"] != "CONSOLIDATED")
    return found[0]


# ---------------------------------------------------------------- formatting

def indian_number(value, decimals=2):
    neg = value < 0
    s = f"{abs(value):.{decimals}f}"
    whole, _, frac = s.partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        head = re.sub(r"(\d)(?=(\d{2})+$)", r"\1,", head)
        whole = head + "," + tail
    return ("-" if neg else "") + whole + ("." + frac if frac else "")


def money(value):
    return ("-₹" if value < 0 else "₹") + indian_number(abs(value)) + " Cr"


def change(cur, prev, profit=False):
    """Change versus a prior period. % only when both values are positive."""
    if prev == 0:
        return None
    if cur > 0 and prev > 0:
        pct = (cur - prev) / prev * 100
        arrow = "↑" if pct > 0 else ("↓" if pct < 0 else "")
        return f"{arrow} {abs(pct):.1f}%"
    if profit:
        return ("vs loss " if prev < 0 else "vs profit ") + f"₹{indian_number(abs(prev))} Cr"
    return "vs " + money(prev)


def metric_line(label, m):
    cur = m["cur"]
    profit = label == "Net Profit"
    name = "Net Loss" if profit and cur < 0 else label
    parts = []
    for key, tag in (("yoy", "YoY"), ("qoq", "QoQ")):
        if key in m:
            text = change(cur, m[key], profit)
            if text:
                parts.append(f"{text} {tag}" if text.startswith("vs ") else f"{tag} {text}")
    amount = f"₹{indian_number(abs(cur))} Cr" if profit else money(cur)
    return f"- {name}: {amount}" + (" | " + " | ".join(parts) if parts else "")


def company_name(raw):
    """'Zydus Wellness Ltd-$' -> 'ZYDUS WELLNESS' (BSE appends group markers)."""
    name = re.sub(r"[\s-]*[$#*]+\s*$", "", raw.strip())
    return re.sub(r"\s+(?:limited|ltd\.?)$", "", name, flags=re.I).upper()


def format_scorecard(company, card):
    """Template A. Returns HTML text, or '' when the card lacks revenue and profit."""
    import html as _h
    m = card["metrics"]
    if "revenue" not in m or "pat" not in m:
        return ""
    from src.editorial import header
    scope = (card.get("scope") or "").strip().upper()
    label = f"{card['period']} ({scope})" if scope else f"{card['period']} RESULTS"
    lines = [header(company, label)]
    lines.append(metric_line("Net Profit", m["pat"]))
    lines.append(metric_line("Revenue", m["revenue"]))
    if "ebitda" in m:
        lines.append(metric_line("EBITDA", m["ebitda"]))
        rev = m["revenue"]
        cur = m["ebitda"]["cur"] / rev["cur"] * 100
        py = m["ebitda"]["yoy"] / rev["yoy"] * 100
        lines.append(f"- EBITDA Margin: {cur:.1f}% vs {py:.1f}% YoY")
    elif m["pat"]["cur"] > 0 and "yoy" in m["pat"] and "yoy" in m["revenue"]:
        rev = m["revenue"]
        lines.append(f"- Net Margin: {m['pat']['cur'] / rev['cur'] * 100:.1f}% vs "
                     f"{m['pat']['yoy'] / rev['yoy'] * 100:.1f}% YoY")
    return "\n".join(lines)
