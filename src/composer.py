"""Turns evaluated items into Outbox entries using the editorial templates.

Every post is `<b>{Company} Ltd – {EVENT}</b>` (or `<b>{SOURCE} – {title}</b>` for
macro sources) followed by 3–6 bullets that each carry a number, a named party or
a date. Nothing here truncates a headline.
"""
from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

from src import editorial
from src.scheduler import Pending

MIN_BULLETS = 2
MIN_BULLETS_MACRO = 1


def _link(url) -> str:
    from src.post_style import link
    return link(url)


BOILERPLATE = re.compile(r"limited review report|taken on record|annexure\s*[-–]?\s*[a-z]\b|"
                         r"\bfinancial highlights\b(?=\s+\w)", re.I)
# Glyph damage from PDF fonts. The rupee sign often extracts as "X", "`", "%" or a stray
# "z"/"t" glued to the amount; "ti" ligatures as "^" or "Ɵ"; "Q2" as "02" before FY.
_AMOUNT = r"(?=\s?\d[\d,]*(?:\.\d+)?\s*(?:crore|crs?\b|lakh|mn\b|million|bn\b|billion))"
RUPEE_GLYPH = re.compile(r"(?<![A-Za-z0-9])[X`%]" + _AMOUNT + r"|(?<=\bto)t\s?" + _AMOUNT[3:-1].join(("(?=", ")"))
                         + r"|(?<=\s)z\s?" + _AMOUNT[3:-1].join(("(?=", ")")))
GLYPHS = ((re.compile(r"(?<=[a-z])[\^Ɵ](?=[a-z])"), "ti"), (re.compile(r"\b0([1-4])(?=\s?FY\s?\d)"), r"Q\1"),
          (re.compile(r"[\u0336\u0335]"), ""), (re.compile(r"^(?:&quot;|\")\s*"), ""))


SPACED_CAPS = re.compile(r"\b(?:[A-Z] ){2,}[A-Z]\b")
RATING_ACTION = re.compile(r"(\bRating (?:Assigned|Reaffirmed|Upgraded|Downgraded|Withdrawn|Revised))\b[^.;]*$", re.I)
FOREIGN_AMOUNT = re.compile(r"(?:\b(?:EUR|USD|GBP|JPY)\s?-?\d|[$€£]\s?-?\d)")
INR_AMOUNT = re.compile(r"(?:₹|\bINR|\bRs\.?)\s?-?\d|\d\s?(?:crore|cr\b|lakh)", re.I)


def _clean(point: str) -> str:
    point = editorial.strip_junk(str(point or "")).replace("\n", " ")
    # Letter-spaced headings ("P R E S S  R E L E A S E") extract one glyph per word.
    point = SPACED_CAPS.sub(lambda m: m.group(0).replace(" ", ""), point)
    point = re.sub(r"^\s*PRESS\s?RELEASE\b[\s:–-]*", "", point, flags=re.I)
    # Rating tables bleed the next cell ("Rating Assigned - $", "Rating Assigned RBI").
    point = RATING_ACTION.sub(r"\1", point)
    point = RUPEE_GLYPH.sub(lambda m: " ₹" if m.group(0)[0] == "t" else "₹", point)
    for pattern, repl in GLYPHS:
        point = pattern.sub(repl, point)
    point = re.sub(r"\s*\((?:DIN|PAN)\s*:?\s*\)", "", point)
    point = re.sub(r"\s+", " ", point).strip().lstrip("•-▪►* \ufe0f").rstrip(" ,;:-")
    return point


def _norm(p):
    return re.sub(r"[^a-z0-9%.]+", " ", p.casefold()).strip()


def factual(point: str) -> bool:
    return bool(editorial.NUMBER.search(point) or editorial.PARTY.search(point) or editorial.DATE_WORDS.search(point))


def filing_bullets(points, limit=6, management=False):
    from src.post_style import shorten_management, dedupe_bullets
    out = []
    for raw in points:
        p = _clean(raw)
        if management:
            p = shorten_management(p)
        if len(p) < 12 or editorial.JUNK_IN_POST.search(p) or BOILERPLATE.search(p) or not factual(p):
            continue
        out.append(p)
    return dedupe_bullets(out)[:limit]


def compose_filing(item, eval_data, event: editorial.Event, now: datetime, published=None) -> tuple[Optional[Pending], str]:
    company = item.get("company") or eval_data.get("company") or ""
    bullets = filing_bullets(eval_data.get("summary_points") or [],
                             management=event.label in ("MANAGEMENT CHANGE", "MANAGEMENT UPDATE"))
    if event.label in ("FINANCIAL RESULTS", "RESULTS") and item.get("source") not in ("RBI", "PIB", "X"):
        # A results post for an Indian listed company reports rupee figures; EUR/USD-only
        # bullets come from a foreign subsidiary's release attached to the filing.
        bullets = [b for b in bullets if not (FOREIGN_AMOUNT.search(b) and not INR_AMOUNT.search(b))]
    macro = not company or item.get("source") in ("RBI", "NEWS", "LIVEBLOG", "PIB", "X")
    need = MIN_BULLETS_MACRO if macro else MIN_BULLETS
    if event.label in ("ORDER WIN", "CREDIT RATING", "MANAGEMENT CHANGE", "DIVIDEND", "BONUS / SPLIT", "BUYBACK",
                       "TAX DEMAND", "DEFAULT", "ACQUISITION", "AGREEMENT", "MOU", "CORPORATE ACTION",
                       "PREFERENTIAL ISSUE", "IPO UPDATE"):
        need = 1      # one decisive fact (amount, rating, name, action) is a complete post for these
    if len(bullets) < need:
        return None, "TOO_FEW_FACTUAL_BULLETS"
    m = re.match(r"^(.+?)\s+–\s+(.+)$", eval_data.get("headline") or "")
    if macro and company and m:
        hdr = editorial.header(company, m.group(2))
        key_company, key_event = company, m.group(2).upper()
    elif macro:
        title = re.sub(r"\s+", " ", eval_data.get("headline") or item.get("title") or "").strip()
        label = {"RBI": "RBI", "PIB": "PIB"}.get(item.get("source"), "")
        hdr = f"<b>{html.escape(label + ' – ' if label else '')}{html.escape(title)}</b>"
        key_company, key_event = company or item.get("source", "MACRO"), title.casefold()
    else:
        label = event.label if event.label != "UPDATE" else _event_from_headline(eval_data.get("headline", ""))
        if label in ("FINANCIAL RESULTS", "RESULTS"):
            label = _period_label(" ".join(bullets + [str(eval_data.get("headline") or "")]), label,
                                  published or now)
        hdr = editorial.header(company, label)
        key_company, key_event = company, label
    return Pending(key=item["id"], company=key_company, event=key_event, header=hdr,
                   bullets=[html.escape(b) for b in bullets], discovered=now, published=published,
                   tail=_link(item.get("source_url") or item.get("pdf_url"))), "OK"


def _period_label(text, default, when=None):
    """'Q2 FY27 RESULTS' / 'H1 FY27 RESULTS' from the period the bullets name; otherwise the
    latest quarter that had ended before the filing (results are filed within ~60 days)."""
    found = set()
    for m in re.finditer(r"\b(?:([QH][1-4])|([1-4])([QH]))\s?['’]?\s?FY\s?'?((?:20)?\d{2})(?:\s?[-–]\s?(\d{2}))?\b", text, re.I):
        per = (m.group(1) or (m.group(3) + m.group(2))).upper()
        yr = m.group(5) or m.group(4)[-2:]
        found.add((per, yr))
    # A prior-year quarter used only as a comparison (e.g. "in line with Q2 FY '26")
    # must not beat a current-year period (e.g. "H1 FY '27") for the title year.
    pick = None
    if found:
        latest = max(int(f[1]) for f in found)
        at_year = [f for f in found if int(f[1]) == latest]
        quarters = sorted((f for f in at_year if f[0].startswith("Q")), key=lambda f: f[0])
        pick = quarters[-1] if quarters else at_year[0]
    if not pick and when is not None:
        y, mth = when.year, when.month
        qe_month = ((mth - 1) // 3) * 3 or 12          # last calendar quarter end before this month
        qe_year = y if qe_month != 12 else y - 1
        fq = {6: 1, 9: 2, 12: 3, 3: 4}[qe_month]
        fy = qe_year + 1 if qe_month in (6, 9, 12) else qe_year
        pick = (f"Q{fq}", f"{fy % 100:02d}")
    return f"{pick[0]} FY{pick[1]} RESULTS" if pick else default


def _event_from_headline(headline: str) -> str:
    parts = re.split(r"\s+[–-]\s+", headline or "", maxsplit=1)
    return (parts[1] if len(parts) == 2 else "UPDATE").strip().upper()


def compose_prebuilt(item, now: datetime, published=None) -> tuple[Optional[Pending], str]:
    pre = item["_prebuilt"]
    tail = _link(item.get("source_url") or item.get("pdf_url"))
    t = pre.get("template")
    if t == "A":
        from sources.result_scorecard import format_scorecard
        text = format_scorecard(item.get("company", ""), pre["card"])
        if not text:
            return None, "SCORECARD_INCOMPLETE"
        from src.post_style import strip_marker
        lines = [l for l in text.split("\n") if l.strip()]
        hdr = lines[0]
        bullets = [strip_marker(l) for l in lines[1:]]
        return Pending(item["id"], item.get("company", ""), "RESULTS", hdr, bullets, now, published, tail), "OK"
    if t == "C":
        from src.concall_summary import format_concall
        text = format_concall(item.get("company", ""), pre["points"])
        lines = text.split("\n")
        return Pending(item["id"], item.get("company", ""), "EARNINGS CALL", lines[0],
                       [l[2:] if l.startswith(("• ", "- ")) else l for l in lines[1:] if l.startswith(("• ", "- "))], now, published, tail), "OK"
    if t == "E":
        text = editorial.format_deal(pre["deal"])
        lines = text.split("\n")
        return Pending(item["id"], item.get("company", ""), pre["deal"]["kind"] + "|" + pre["deal"]["client"],
                       lines[0], [l[2:] for l in lines[1:]], now, published, ""), "OK"
    if t == "MACRO":
        bullets = [html.escape(b) for b in filing_bullets(pre.get("bullets", []))]
        title = re.sub(r"[\s\-–]+$", "", re.sub(r"\s+", " ", pre["title"])).strip()
        if not bullets and not editorial.NUMBER.search(title):
            return None, "NO_FACTUAL_MACRO_BULLET"   # speeches and notices without a figure or decision
        hdr = f"<b>{html.escape(pre['label'])} – {html.escape(title)}</b>"
        return Pending(item["id"], pre["label"], pre["title"].casefold(), hdr, bullets[:6], now, published, tail), "OK"
    if t == "RAW":       # IPO listing/allotment posts already in final HTML
        lines = pre["text"].split("\n")
        return Pending(item["id"], item.get("company", ""), pre["event"], lines[0],
                       [l[2:] if l.startswith(("• ", "- ")) else l for l in lines[1:] if l.startswith(("• ", "- "))], now, published, tail), "OK"
    return None, "UNKNOWN_TEMPLATE"
