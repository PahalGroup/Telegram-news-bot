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

MIN_BULLETS = 3
MIN_BULLETS_MACRO = 1


def _link(url) -> str:
    p = urlparse(str(url or ""))
    if p.scheme == "https" and p.hostname:
        return f'<a href="{html.escape(url, quote=True)}">Source</a>'
    return ""


def _clean(point: str) -> str:
    point = editorial.strip_junk(str(point or "")).replace("\n", " ")
    point = re.sub(r"\s+", " ", point).strip().lstrip("•-▪►* \ufe0f").rstrip(" ,;:-")
    return point


def factual(point: str) -> bool:
    return bool(editorial.NUMBER.search(point) or editorial.PARTY.search(point) or editorial.DATE_WORDS.search(point))


def filing_bullets(points, limit=6):
    out, seen = [], set()
    for raw in points:
        p = _clean(raw)
        if len(p) < 12 or p.casefold() in seen or editorial.JUNK_IN_POST.search(p) or not factual(p):
            continue
        seen.add(p.casefold())
        out.append(p)
    return out[:limit]


def compose_filing(item, eval_data, event: editorial.Event, now: datetime, published=None) -> tuple[Optional[Pending], str]:
    company = item.get("company") or eval_data.get("company") or ""
    bullets = filing_bullets(eval_data.get("summary_points") or [])
    macro = not company or item.get("source") in ("RBI", "NEWS", "LIVEBLOG", "PIB", "X")
    need = MIN_BULLETS_MACRO if macro else MIN_BULLETS
    if event.label in ("ORDER WIN", "CREDIT RATING", "MANAGEMENT CHANGE", "DIVIDEND", "BONUS / SPLIT", "BUYBACK",
                       "TAX DEMAND", "DEFAULT"):
        need = 1      # one decisive fact (amount, rating, name) is a complete post for these
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
        hdr = editorial.header(company, label)
        key_company, key_event = company, label
    return Pending(key=item["id"], company=key_company, event=key_event, header=hdr,
                   bullets=[html.escape(b) for b in bullets], discovered=now, published=published,
                   tail=_link(item.get("source_url") or item.get("pdf_url"))), "OK"


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
        lines = text.split("\n")
        hdr = "\n".join(lines[:2])                   # header + scope line
        bullets = [l[2:] for l in lines[2:]]
        return Pending(item["id"], item.get("company", ""), "RESULTS", hdr, bullets, now, published, tail), "OK"
    if t == "C":
        from src.concall_summary import format_concall
        text = format_concall(item.get("company", ""), pre["points"])
        lines = text.split("\n")
        return Pending(item["id"], item.get("company", ""), "EARNINGS CALL", lines[0],
                       [l[2:] for l in lines[1:] if l.startswith("• ")], now, published, tail), "OK"
    if t == "E":
        text = editorial.format_deal(pre["deal"])
        lines = text.split("\n")
        return Pending(item["id"], item.get("company", ""), pre["deal"]["kind"] + "|" + pre["deal"]["client"],
                       lines[0], [l[2:] for l in lines[1:]], now, published, ""), "OK"
    if t == "MACRO":
        bullets = [html.escape(_clean(b)) for b in pre.get("bullets", []) if _clean(b)]
        hdr = f"<b>{html.escape(pre['label'])} – {html.escape(pre['title'])}</b>"
        return Pending(item["id"], pre["label"], pre["title"].casefold(), hdr, bullets[:6], now, published, tail), "OK"
    if t == "RAW":       # IPO listing/allotment posts already in final HTML
        lines = pre["text"].split("\n")
        return Pending(item["id"], item.get("company", ""), pre["event"], lines[0],
                       [l[2:] for l in lines[1:] if l.startswith("• ")], now, published, tail), "OK"
    return None, "UNKNOWN_TEMPLATE"
