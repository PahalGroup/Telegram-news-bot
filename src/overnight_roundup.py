"""Morning roundup of significant filings from the closed overnight window.

The individual 20-minute fresh-start cutoff is unchanged: items in this window
are marked seen and never posted one-by-one. This module only renders a compact
summary, with figures taken from a reliable scorecard or from explicit exchange
text, never invented.
"""
from __future__ import annotations

import html
import re
from datetime import datetime, time, timedelta

from src.scheduler import IST, parse_time

TELEGRAM_MAX = 4096
# Leave a little headroom for UTF-16 counting and an added (1/2) suffix.
SAFE_LEN = TELEGRAM_MAX - 32

ALWAYS_SKIP = re.compile(
    r"investor presentation|newspaper (?:publication|advertisement|clipping)|"
    r"monitoring agency report|shareholding pattern|trading window|"
    r"voting results?|scrutini[sz]er|analysts?/|institutional investor meet|"
    r"con\.? call|earnings call (?:schedule|intimation|invite)|"
    r"management commentary|podcast|alm reporting",
    re.I,
)

DIVIDEND_RATE = re.compile(
    r"(?:₹|rs\.?|inr)\s*([\d,]+(?:\.\d+)?)\s*(?:per|/)\s*(?:equity\s+)?share",
    re.I,
)
BUYBACK_AMT = re.compile(
    r"(?:₹|rs\.?|inr)\s*([\d,]+(?:\.\d+)?)\s*(crores?|cr\b|lakhs?|lacs?|million|mn\b)",
    re.I,
)

SECTION_ORDER = (
    ("RESULTS", "Results"),
    ("ORDER_WIN", "Orders"),
    ("CORP_ACTION", "Corporate actions"),
    ("DEAL", "Fundraises & M&A"),
)


def closed_window(now, cutoff=None, fresh_start=None):
    """23:30 IST the previous calendar night through the fresh-start backlog cutoff.

    Ending at the cutoff (shift start minus 20 minutes) means an item cannot
    appear both in the roundup and as its own in-window post.
    """
    now = now.astimezone(IST) if getattr(now, "tzinfo", None) else now.replace(tzinfo=IST)
    start = datetime.combine(now.date() - timedelta(days=1), time(23, 30), tzinfo=IST)
    end = cutoff or fresh_start or now
    if getattr(end, "tzinfo", None) is None:
        end = end.replace(tzinfo=IST)
    else:
        end = end.astimezone(IST)
    return start, end


def in_closed_window(published, start, end) -> bool:
    ts = parse_time(published) if not isinstance(published, datetime) else published
    if ts is None:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=IST)
    return start <= ts < end


def _telegram_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _display_company(name: str) -> str:
    from src.filter_engine import FilterEngine
    from src.editorial import company_title
    raw = FilterEngine.normalize_entity_name(name) or name
    titled = company_title(raw)
    # Keep 2-3 letter uppercase tokens from the source (CX, ITC), not Cx.
    keep = {w.casefold(): w for w in re.findall(r'\b[A-Z]{2,3}\b', raw)}
    if keep:
        titled = ' '.join(keep.get(part.casefold(), part) for part in titled.split())
    return titled


def _combined_text(item: dict) -> str:
    return " ".join(
        str(item.get(k) or "")
        for k in ("title", "subcat", "raw_subject", "description", "subject")
    )


def is_trivial_or_procedural(item: dict) -> bool:
    text = _combined_text(item)
    if ALWAYS_SKIP.search(text):
        return True
    sub = str(item.get("subcat") or "")
    if re.search(r"record date", sub, re.I):
        return True
    if re.search(r"record date for the purpose of", text, re.I) and not re.search(
            r"financial results?", text, re.I):
        return True
    if re.search(r"change in auditors?|appointment of (?:statutory|secretarial|internal|cost) auditor", text, re.I):
        if not re.search(r"financial results?|\bdividend\b|buy ?back", text, re.I):
            return True
    return False


def classify_kind(item: dict) -> str | None:
    """Map a filing onto a roundup section, or None to drop it."""
    if is_trivial_or_procedural(item):
        return None
    from src import editorial
    rules = editorial.load_rules()
    event = editorial.classify(item, rules) if item.get("source") in ("BSE", "NSE", "NEWS", "LIVEBLOG") else None
    label = (event.label if event else "") or ""
    text = _combined_text(item)
    if item.get("category") in ("FINANCIAL_RESULTS", "RESULTS") or label == "RESULTS" \
            or re.search(r"financial results?|(?:^|\b)q[1-4]\b.*\bresults?\b", text, re.I):
        # Outcome titles sometimes omit the words "financial results".
        if label == "RESULTS" or re.search(r"financial results?|(?:^|\b)q[1-4]\b.*\bresults?\b", text, re.I):
            return "RESULTS"
        if item.get("subcat") == "Outcome of Board Meeting" and re.search(
                r"results?|dividend|buy ?back", text, re.I):
            return "RESULTS"
    if label == "ORDER WIN" or re.search(
            r"award(?:ing)? of order|received a new order|secures? (?:an?\s+)?(?:order|contract)|"
            r"letter of (?:intent|award)|bags? .*(?:order|contract)", text, re.I):
        return "ORDER_WIN"
    if label in ("DIVIDEND", "BUYBACK", "BONUS / SPLIT") or re.search(
            r"\bdividend\b|\bbuy ?back\b|\bbonus\b|stock split|sub-?division", text, re.I):
        return "CORP_ACTION"
    if label in ("FUND RAISING", "ACQUISITION") or re.search(
            r"\bqip\b|preferential issue|rights issue|open offer|scheme of arrangement|"
            r"amalgamation|\bmerger\b|acquisition|wholly owned subsidiary", text, re.I):
        # Voting-result wrappers around a merger stay procedural (already skipped).
        return "DEAL"
    if item.get("subcat") == "Outcome of Board Meeting" and re.search(r"results?", text, re.I):
        return "RESULTS"
    return None


def _card_metrics(item: dict) -> dict | None:
    """Return period/revenue/PAT only from a reliable template-A scorecard."""
    pre = item.get("_prebuilt")
    card = None
    if isinstance(pre, dict) and pre.get("template") == "A":
        card = pre.get("card")
    if not card and isinstance(item.get("card"), dict):
        card = item["card"]
    if not isinstance(card, dict):
        return None
    metrics = card.get("metrics") or {}
    if "revenue" not in metrics or "pat" not in metrics:
        return None
    try:
        rev = float(metrics["revenue"]["cur"])
        pat = float(metrics["pat"]["cur"])
    except (KeyError, TypeError, ValueError):
        return None
    return {
        "period": card.get("period") or "",
        "scope": card.get("scope") or "",
        "revenue_cr": rev,
        "pat_cr": pat,
    }


def _fmt_cr(value: float) -> str:
    text = f"{value:,.2f}".rstrip("0").rstrip(".")
    return f"₹{text} Cr"


def _dividend_clause(text: str) -> str:
    m = DIVIDEND_RATE.search(text)
    if not m:
        return ""
    return f"final dividend ₹{m.group(1)} / share"


def _amount_clause(text: str) -> str:
    """An explicit rupee (or USD etc.) amount already in the exchange text."""
    m = BUYBACK_AMT.search(text)
    if not m:
        return ""
    raw, unit = m.group(1), (m.group(2) or "").lower()
    try:
        value = float(raw.replace(",", ""))
    except ValueError:
        return ""
    if unit.startswith("lakh") or unit.startswith("lac"):
        value /= 100
        unit_label = "Cr"
    elif unit.startswith("million") or unit == "mn":
        value /= 10
        unit_label = "Cr"
    else:
        unit_label = "Cr"
    return f"{_fmt_cr(value)}" if unit_label == "Cr" else f"₹{value} {unit}"


def summarise_item(item: dict) -> dict | None:
    kind = classify_kind(item)
    if not kind:
        return None
    company = (item.get("company") or "").strip()
    if not company:
        return None
    text = _combined_text(item)
    published = parse_time(item.get("published_at") or item.get("_backlog_at"))
    card = _card_metrics(item) if kind == "RESULTS" else None
    period = (card or {}).get("period") or ""
    if kind == "RESULTS" and not period:
        from src.composer import _period_label
        period = _period_label(text, "", when=published) or ""
        period = period.replace(" RESULTS", "").strip()

    bits = []
    score = 50
    if kind == "RESULTS":
        label = f"{period} results" if period else "results"
        bits.append(label)
        if card:
            bits.append(f"revenue {_fmt_cr(card['revenue_cr'])}, PAT {_fmt_cr(card['pat_cr'])}")
            score = 100
        else:
            score = 70
        div = _dividend_clause(text)
        if div:
            bits.append(div)
            score = max(score, 90)
        buy = re.search(r"buy ?back", text, re.I)
        if buy:
            amt = _amount_clause(text)
            bits.append(f"buyback{f' {amt}' if amt else ''}".strip())
            score = max(score, 90)
    elif kind == "ORDER_WIN":
        amt = _amount_clause(text)
        # QAR/USD converted figures written as ₹ / INR in the same sentence are used;
        # a foreign amount with no rupee (or USD) figure is named without a number.
        bits.append(f"order{f' of {amt}' if amt else ''}".strip())
        if amt:
            score = 80
        else:
            # An order with no value is easy to over-claim; keep only if clearly an award.
            if not re.search(r"order|contract|LOI|LOA", text, re.I):
                return None
            score = 55
        m = re.search(r"\bfrom\s+([A-Z][\w&.]*(?:\s+[A-Z][\w&.]*){0,6})", text)
        if m and not re.search(r"from the exchange|from official", m.group(0), re.I):
            party = m.group(1).strip(" ,.")
            if party.lower() not in ("the", "its", "a", "an"):
                bits[-1] += f" from {party}"
    elif kind == "CORP_ACTION":
        if re.search(r"buy ?back", text, re.I):
            amt = _amount_clause(text)
            bits.append(f"buyback{f' {amt}' if amt else ''}".strip())
            score = 85 if amt else 60
        elif re.search(r"dividend", text, re.I):
            div = _dividend_clause(text)
            bits.append(div or "dividend")
            score = 85 if div else 60
        elif re.search(r"bonus", text, re.I):
            bits.append("bonus")
            score = 75
        else:
            bits.append("corporate action")
            score = 50
    else:  # DEAL
        if re.search(r"wholly owned subsidiary", text, re.I):
            bits.append("to incorporate a wholly owned subsidiary")
            score = 65
        elif re.search(r"acquisition|acquire", text, re.I):
            amt = _amount_clause(text)
            bits.append(f"acquisition{f' {amt}' if amt else ''}".strip())
            score = 90 if amt else 70
        elif re.search(r"amalgamation|\bmerger\b", text, re.I):
            bits.append("amalgamation")
            score = 80
        else:
            amt = _amount_clause(text)
            bits.append(f"fundraise{f' {amt}' if amt else ''}".strip())
            score = 88 if amt else 60

    body = "; ".join(b for b in bits if b)
    if not body:
        return None
    return {
        "id": item.get("id") or "",
        "company": company,
        "display": _display_company(company),
        "kind": kind,
        "score": score,
        "published": published.isoformat() if published else "",
        "line": f"• <b>{html.escape(_display_company(company))}</b> – {html.escape(body)}",
        "plain": f"{_display_company(company)} – {body}",
    }


def merge_summaries(rows: list[dict]) -> list[dict]:
    """One line per company: keep the strongest kind, append extra clauses."""
    by: dict[str, dict] = {}
    for row in sorted(rows, key=lambda r: (-r["score"], r.get("published") or "")):
        key = row["display"].casefold()
        if key not in by:
            by[key] = dict(row)
            continue
        cur = by[key]
        # Add a clause the first line does not already contain.
        extra = row["plain"].split("–", 1)[-1].strip()
        cur_body = cur["plain"].split("–", 1)[-1].strip()
        if extra and extra.casefold() not in cur_body.casefold():
            if row["kind"] == "RESULTS" and cur["kind"] != "RESULTS":
                body = extra + "; " + cur_body
                cur["kind"] = "RESULTS"
            else:
                body = cur_body + "; " + extra
            cur["plain"] = f"{cur['display']} – {body}"
            cur["line"] = f"• <b>{html.escape(cur['display'])}</b> – {html.escape(body)}"
        cur["score"] = max(cur["score"], row["score"])
    return sorted(by.values(), key=lambda r: (-r["score"], r["display"].casefold()))


def _render(rows: list[dict], now: datetime, part: int | None = None, total: int | None = None) -> str:
    stamp = now.astimezone(IST).strftime("%d %b %Y")
    title = f"Overnight Filings – {stamp}"
    if part and total and total > 1:
        title = f"{title} ({part}/{total})"
    lines = [f"<b>{html.escape(title)}</b>"]
    grouped: dict[str, list[dict]] = {k: [] for k, _ in SECTION_ORDER}
    for row in rows:
        grouped.setdefault(row["kind"], []).append(row)
    for kind, heading in SECTION_ORDER:
        chunk = grouped.get(kind) or []
        if not chunk:
            continue
        lines += ["", f"<i>{heading}</i>", *[r["line"] for r in chunk]]
    return "\n".join(lines)


def build_posts(items: list[dict], now: datetime, limit: int = 2) -> list[str]:
    """Return 0–2 HTML posts, ranked by significance, within Telegram's limit."""
    summaries = []
    seen_ids = set()
    for item in items:
        key = item.get("id")
        if key and key in seen_ids:
            continue
        if key:
            seen_ids.add(key)
        row = summarise_item(item)
        if row:
            summaries.append(row)
    rows = merge_summaries(summaries)
    if not rows:
        return []

    def pack(chosen, part=None, total=None):
        return _render(chosen, now, part, total)

    full = pack(rows)
    if _telegram_len(full) <= SAFE_LEN:
        return [full]

    # Fill post 1 with the highest-scoring rows that still fit, then post 2.
    part1, part2 = [], []
    for row in rows:
        trial = pack(part1 + [row], 1, 2)
        if _telegram_len(trial) <= SAFE_LEN:
            part1.append(row)
        else:
            part2.append(row)
    if not part1:
        # A single oversized line should not ship; drop it and try the rest.
        part2 = rows[1:]
        part1 = rows[:1]
        if _telegram_len(pack(part1, 1, 2)) > SAFE_LEN:
            part1 = []
    # Trim part 2 from the bottom (lowest significance already last) until it fits.
    while part2 and _telegram_len(pack(part2, 2, 2)) > SAFE_LEN:
        part2.pop()
    posts = []
    total = 2 if (part1 and part2) else 1
    if part1:
        posts.append(pack(part1, 1 if total == 2 else None, total if total == 2 else None))
    if part2 and limit >= 2:
        posts.append(pack(part2, 2, 2))
    return posts[:limit]
