"""Editorial rules for the channel: what to post, and how it reads.

Every decision is deterministic and explained by a reason code so the dry-run
report can count why items were dropped. Wording is our own template text; only
facts (numbers, names, dates) come from the source filing.
"""
import html
import json
import os
import re
from dataclasses import dataclass, field
from typing import List, Optional

RULES_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "filter_rules.json")


def load_rules(path=RULES_PATH):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)["editorial"]


# ---------------------------------------------------------------- junk text
JUNK_LINE = [re.compile(p, re.I) for p in (
    r"\bregd\.?\s*(?:office|off\.?)\b|\bregistered\s+office\b|\bcorporate\s+office\b",
    r"\b(?:CIN|GSTIN|ISIN|PAN)\s*[:\-]?\s*[A-Z0-9]{8,}",
    r"\b(?:scrip|security)\s*(?:code|id)\s*[:\-]?\s*\w+|\bsymbol\s*[:\-]\s*[A-Z&\-]+",
    r"^\s*(?:ref(?:erence)?|our\s+ref|letter\s+no|sub(?:ject)?)\s*[.:\-]",
    r"^\s*(?:to,?|the\s+(?:general\s+)?manager|listing\s+(?:department|compliance)|dept\.?\s+of\s+corporate)",
    r"phiroze\s+jeejeebhoy|dalal\s+street|bandra[\s-]+kurla|exchange\s+plaza",
    r"\b(?:kindly|please)\s+take\s+(?:the\s+same|this|it|note|the\s+above)",
    r"\bthanking\s+you\b|\byours\s+(?:faithfully|sincerely|truly)\b|\bfor\s+and\s+on\s+behalf\s+of\b",
    r"^\s*dear\s+(?:sir|madam|sirs)\b",
    r"\bcompany\s+secretary\b.*\b(?:membership|m\.?\s*no|acs|fcs)\b|\b(?:ACS|FCS)\s*[:\-]?\s*\d{3,}",
)]
JUNK_SPAN = [re.compile(p, re.I) for p in (
    r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+",                                    # emails
    r"https?://\S+|\bwww\.[\w.-]+\.\w+\S*",                              # urls
    r"(?:\+91[\s-]?)?(?:\(?0\d{2,4}\)?[\s-]?)?\d{3,4}[\s-]?\d{4}\b(?=[^%\d]|$)(?<!\d{5}\.\d{2})",  # phones
    r"\b(?:tel|phone|fax|mobile|ph)\.?\s*(?:no\.?)?\s*[:\-]?\s*[+\d][\d\s\-/,()]{7,}",
    r"\b(?:pursuant\s+to|in\s+(?:terms|compliance)\s+(?:of|with)|under)\s+(?:the\s+)?"
    r"(?:regulation|reg\.?|section|clause)s?\s*[\d()a-z,\s&and]+(?:of\s+(?:the\s+)?)?"
    r"(?:sebi\s*\([^)]*\)\s*regulations?,?\s*\d{4}|listing\s+regulations|companies\s+act,?\s*\d{4})?[,;]?",
    r"\bsebi\s*\(\s*listing\s+obligations?\s+and\s+disclosure\s+requirements?\s*\)\s*regulations?,?\s*\d{4}",
    r"\b[A-Z][A-Za-z]+(?:/[A-Z0-9\-]+){2,}/\d{2,4}(?:-\d{2,4})?\b",   # letter numbers like ABC/SEC/2026-27/41
    r"\bPIN\s*[:\-]?\s*\d{6}\b|\b\d{3}\s?\d{3}\b(?=\s*(?:,|\.|$|india|maharashtra|gujarat))",
)]


def strip_junk(text: str) -> str:
    """Remove letter furniture: addresses, citations, letter numbers, contacts."""
    kept = []
    for line in re.split(r"\n+", str(text or "")):
        if any(p.search(line) for p in JUNK_LINE):
            continue
        for p in JUNK_SPAN:
            line = p.sub(" ", line)
        line = re.sub(r"\s+([,.;:])", r"\1", re.sub(r"\s{2,}", " ", line)).strip(" ,;:-–")
        line = re.sub(r"^(?:that|and|further)\s+", "", line, flags=re.I)
        if len(line) >= 12 and re.search(r"[A-Za-z]{3}", line):
            kept.append(line[0].upper() + line[1:])
    return "\n".join(kept)


# ---------------------------------------------------------------- events
@dataclass
class Event:
    label: str           # header text, e.g. "ORDER WIN"
    rule: str            # always_drop | needs_number | needs_decision | tax | transcript | result
    reason: str = ""


AMOUNT = re.compile(r"(?:₹|rs\.?|inr|usd|us\$|\$|eur|€)\s*[\d,]+(?:\.\d+)?\s*(?:crores?|cr\.?|lakhs?|lacs?|mn|million|bn|billion)?"
                    r"|[\d,]+(?:\.\d+)?\s*(?:crores?|cr\b|lakhs?|lacs?|million|billion|bn\b|mn\b)", re.I)
NUMBER = re.compile(AMOUNT.pattern + r"|\b\d+(?:\.\d+)?\s*%|\b\d[\d,]*(?:\.\d+)?\s*(?:mw|gw|mtpa|tpa|units|shares|acres|sq\.?\s*ft)\b"
                    # Rating grades are upper-case only; case-insensitive "a"/"b"/"d" would make any sentence look factual.
                    r"|(?-i:\b(?:AAA|AA|BBB|BB)[+-]?(?![\w])\s*(?:\(\w+\))?|\b[ABCD](?:[+-]|\s*\((?:Stable|Positive|Negative)\))|\][ABCD]\b|\b(?:CRISIL|ICRA|CARE|IND|ACUITE)\s*(?:A|AA|AAA|BBB|BB)[+-]?)", re.I)
DECISION = re.compile(r"\b(?:approved|appointed|re-?appointed|resigned|resignation|ceased?|elevated|designated|"
                      r"upgraded|downgraded|reaffirmed|assigned|revised|withdrawn|placed\s+on\s+(?:watch|credit\s+watch)|"
                      r"acquired?|acquisition|signed|entered\s+into|completed|allotted|raised|declared|recommended|"
                      r"awarded|received|bagged|secured|won|terminated|cancelled|"
                      r"approves?|appoints?|wins?|bags?|secures?|receives?|acquires?|signs?|declares?|raises?|allots?)\b", re.I)


def classify(item, rules) -> Event:
    sub = f"{item.get('subcat') or ''}"
    text = f"{item.get('title') or ''} {item.get('subject') or ''} {sub}"
    for pattern in rules["always_drop"]:
        # Check each field on its own so anchors mean "the whole sub-category".
        # An outcome/result filing's title can mention a record date or AGM in
        # passing; only its sub-category decides whether it is procedural.
        outcome = re.search(r"outcome|financial results|transcript", sub, re.I)
        fields = (sub,) if outcome else (sub, item.get("title") or "", item.get("subject") or "")
        for field_text in fields:
            if field_text and re.search(pattern, field_text.strip(), re.I):
                return Event("", "always_drop", "PROCEDURAL")
    for label, spec in rules["events"].items():
        if re.search(spec["match"], text, re.I):
            return Event(label, spec["rule"])
    body = item.get("description") or ""
    if re.search(rules["tax_demand_match"], text + " " + body[:800], re.I):
        return Event("TAX DEMAND", "tax")
    return Event("UPDATE", "needs_number_and_decision")


def amounts_cr(text):
    """Every rupee amount in text, converted to crore."""
    out = []
    for m in re.finditer(r"(?:₹|rs\.?|inr)\s*([\d,]+(?:\.\d+)?)\s*(crores?|cr\b|lakhs?|lacs?|million|mn\b|billion|bn\b)?"
                         r"|([\d,]+(?:\.\d+)?)\s*(crores?|cr\b|lakhs?|lacs?)", text, re.I):
        raw, unit = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        try:
            v = float(raw.replace(",", ""))
        except ValueError:
            continue
        u = (unit or "").lower()
        if u.startswith("cr"):
            pass
        elif u.startswith(("lak", "lac")):
            v /= 100
        elif u in ("million", "mn"):
            v /= 10
        elif u in ("billion", "bn"):
            v *= 100
        else:
            v /= 1e7  # bare rupees
        out.append(v)
    return out


def gate(item, event: Event, rules, revenue_cr: Optional[float] = None):
    """(keep, reason). Applies the number/decision requirements per event rule."""
    text = f"{item.get('title') or ''}\n{item.get('description') or ''}"
    if event.rule == "always_drop":
        return False, event.reason
    if event.rule in ("result", "transcript", "deal", "macro"):
        return True, "OK"
    has_number = bool(NUMBER.search(text))
    has_decision = bool(DECISION.search(text))
    if event.rule == "needs_number" and not has_number:
        return False, "NO_NUMBER"
    if event.rule == "needs_decision" and not has_decision:
        return False, "NO_DECISION"
    if event.rule == "needs_number_or_decision" and not (has_number or has_decision):
        return False, "NO_NUMBER_OR_DECISION"
    if event.rule == "needs_number_and_decision" and not (has_number and has_decision):
        return False, "NO_NUMBER_AND_DECISION"
    if event.rule == "tax":
        biggest = max(amounts_cr(text), default=0.0)
        floor = rules["tax_demand_min_cr"]
        pct = rules["tax_demand_min_revenue_pct"]
        if biggest < floor and not (revenue_cr and biggest >= revenue_cr * pct / 100):
            return False, "TAX_DEMAND_BELOW_THRESHOLD"
    return True, "OK"


# ---------------------------------------------------------------- global news
def global_news_decision(title, rules, global_count_today):
    """(keep, reason, counts_toward_cap) for news that is about markets abroad."""
    g = rules["global"]
    t = title or ""
    if not re.search(g["is_global"], t, re.I) or re.search(g["india_focus"], t, re.I) and not re.search(g["trade_india"], t, re.I):
        return True, "NOT_GLOBAL", False
    if re.search(g["drop_officials"], t, re.I):
        return False, "GLOBAL_NON_US_ECB_OFFICIAL", False
    for name, pattern in g["whitelist"].items():
        if re.search(pattern, t, re.I):
            if name == "crude":
                moves = [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)\s*%", t)]
                if not moves or max(moves) <= g["crude_move_pct"]:
                    continue
            return True, "GLOBAL_WHITELIST:" + name, False
    if global_count_today >= g["daily_cap"]:
        return False, "GLOBAL_DAILY_CAP", False
    return True, "GLOBAL_OTHER", True


# ---------------------------------------------------------------- formatting
def company_title(name: str) -> str:
    name = re.sub(r"[\s-]*[$#*]+\s*$", "", str(name or "").strip())
    name = re.sub(r"\s+(?:limited|ltd\.?)$", "", name, flags=re.I)
    if name.isupper() or name.islower():
        name = " ".join(w if len(w) <= 3 and w.isupper() and not w.isalpha() else w.capitalize() for w in name.split())
    return name + " Ltd"


def header(company: str, event_label: str) -> str:
    """'<b>Bharat Electronics Ltd – ORDER WIN</b>'; never truncated."""
    return f"<b>{html.escape(company_title(company))} – {html.escape(event_label.upper())}</b>"


PARTY = re.compile(r"\b[A-Z][\w&.]*(?:\s+[A-Z][\w&.]*)*\s+(?:Ltd|Limited|Bank|Corporation|Ministry|Authority|"
                   r"Railways?|Board|Inc|LLC|GmbH|Pvt|Private|Government|Council|Agency|Fund|Trust)\b|"
                   r"\b(?:CRISIL|ICRA|CARE|India Ratings|Acuite|Brickwork|NHAI|NTPC|ONGC|BHEL|DRDO|ISRO|"
                   r"Indian (?:Army|Navy|Air Force|Railways))\b")
DATE_WORDS = re.compile(r"\b\d{1,2}(?:st|nd|rd|th)?\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b|"
                        r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2},?\s+\d{4}|"
                        r"\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b|\bFY\s?\d{2}\b|\bQ[1-4]\b", re.I)


def pick_bullets(text: str, limit=6) -> List[str]:
    """3–6 factual lines: sentences with figures first, source order preserved."""
    cleaned = strip_junk(text)
    sentences = []
    for line in cleaned.splitlines():
        for s in re.split(r"(?<=[.;])\s+(?=[A-Z(])", line):
            s = s.strip(" .;")
            if 15 <= len(s) <= 320 and s.casefold() not in {x.casefold() for x in sentences}:
                sentences.append(s)
    # Each bullet must carry a number, a named party or a date.
    useful = [i for i, s in enumerate(sentences) if NUMBER.search(s) or PARTY.search(s) or DATE_WORDS.search(s)]
    ranked = sorted(useful, key=lambda i: (not NUMBER.search(sentences[i]), not DECISION.search(sentences[i]), i))
    chosen = sorted(ranked[:limit])
    return [sentences[i] for i in chosen]


def format_event(company, event_label, bullets, link=None):
    lines = [header(company, event_label)] + [f"• {html.escape(b)}" for b in bullets]
    if link:
        lines.append(f'<a href="{html.escape(link, quote=True)}">Source</a>')
    return "\n".join(lines)


def format_deal(deal):
    """Template E: one block or bulk deal."""
    side = "bought" if str(deal["side"]).upper().startswith("B") else "sold"
    qty = f"{deal['qty'] / 1e5:,.2f} lakh shares" if deal["qty"] < 1e7 else f"{deal['qty'] / 1e7:,.2f} crore shares"
    from sources.result_scorecard import indian_number
    return "\n".join([
        header(deal["company"], deal["kind"]),
        f"• {html.escape(deal['client'].title())} {side} {qty}",
        f"• Avg price ₹{indian_number(deal['price'])} | Value ₹{indian_number(deal['value_cr'])} Cr",
        f"• NSE {deal['kind'].lower()} on {deal['date']}",
    ])


def format_macro(source_label, title, bullets=(), link=None):
    """RBI / PIB / FinMin / official-handle posts."""
    lines = [f"<b>{html.escape(source_label.upper())} – {html.escape(title.strip())}</b>"]
    lines += [f"• {html.escape(b)}" for b in bullets]
    if link:
        lines.append(f'<a href="{html.escape(link, quote=True)}">Source</a>')
    return "\n".join(lines)


PROCEDURAL_POST = re.compile(r"board\s+meeting\s+(?:to\s+be\s+held|intimation|scheduled)|trading\s+window|"
                             r"newspaper|postal\s+ballot|e-?voting|agm\b|egm\b|esop|reg(?:ulation)?\.?\s*74\s*\(5\)|"
                             r"investor\s+complaints?|schedule\s+of\s+(?:analyst|investor)|earnings\s+call\s+(?:on|scheduled)", re.I)
JUNK_IN_POST = re.compile(JUNK_SPAN[0].pattern + "|" + JUNK_SPAN[1].pattern +
                          r"|dear\s+sir|registered\s+office|regulation\s+30|kindly\s+take", re.I)
