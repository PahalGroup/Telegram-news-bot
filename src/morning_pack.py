"""Morning pack: 07:15 Opening Cues, 08:45 Stocks in Focus, 09:05 Nifty/GIFT pre-open."""
from __future__ import annotations

import html
from datetime import datetime, time, timedelta

from src.scheduler import IST

SLOTS = {"opening_cues": time(7, 15), "stocks_in_focus": time(8, 45), "preopen": time(9, 5)}
SLOT_GRACE = timedelta(minutes=15)


def due_slots(now: datetime, done: dict) -> list[str]:
    """Slots whose time has come today (weekdays only) and which were not posted yet."""
    now = now.astimezone(IST)
    if now.weekday() >= 5:
        return []
    today = now.date().isoformat()
    out = []
    for name, t in SLOTS.items():
        start = datetime.combine(now.date(), t, IST)
        if start <= now <= start + SLOT_GRACE and done.get(name) != today:
            out.append(name)
    return out


def arrow(pct: float) -> str:
    return "↑" if pct > 0 else "↓" if pct < 0 else "→"


def cue_line(label: str, q: dict, digits: int = 2) -> str:
    return f"• {label}: {q['last']:,.{digits}f} {arrow(q['pct'])} {abs(q['pct']):.2f}%"


def opening_cues(cues: dict, now: datetime) -> str | None:
    groups = [("US close", ["S&P 500", "Nasdaq", "Dow Jones"]), ("Asia", ["Nikkei", "Hang Seng"]),
              ("Commodities & FX", ["Brent crude", "Gold", "USD/INR"]), ("India", ["GIFT Nifty", "India VIX"])]
    lines = [f"<b>Opening Cues – {now.astimezone(IST):%d %b %Y}</b>"]
    n = 0
    for title, labels in groups:
        rows = [cue_line(l, cues[l], 4 if l == "USD/INR" else 2) for l in labels if l in cues]
        if rows:
            lines += ["", f"<i>{title}</i>", *rows]
            n += len(rows)
    if n < 4:  # not enough data to be useful
        return None
    gift = cues.get("GIFT Nifty")
    if gift:
        lines += ["", f"GIFT Nifty points to a {'positive' if gift['pct'] > 0 else 'negative' if gift['pct'] < 0 else 'flat'} start "
                      f"({gift['change']:+,.1f} pts)."]
    return "\n".join(lines)


def evening_window(now: datetime) -> tuple[datetime, datetime]:
    now = now.astimezone(IST)
    prev = now.date() - timedelta(days=3 if now.weekday() == 0 else 1)
    return datetime.combine(prev, time(15, 30), IST), now


def stocks_in_focus(records: list[dict], now: datetime, limit: int = 12) -> str | None:
    start, end = evening_window(now)
    seen, rows = set(), []
    for r in records:
        ts, company = r.get("ts"), r.get("company")
        if not ts or not company:
            continue
        t = datetime.fromisoformat(ts)
        if not (start <= t <= end) or company in seen:
            continue
        seen.add(company)
        lead = f" – {html.escape(html.unescape(r['lead']))}" if r.get("lead") else ""
        rows.append(f"• <b>{html.escape(company)}</b>: {html.escape(r.get('event', '').title())}{lead}")
    if not rows:
        return None
    return "\n".join([f"<b>Stocks in Focus – {now.astimezone(IST):%d %b %Y}</b>", "", *rows[:limit]])


def _is_todays_preopen(ind: dict, now: datetime) -> bool:
    """NSE keeps yesterday's close in this field until the 09:00 pre-open prints; never show that as pre-open."""
    try:
        t = datetime.strptime(str(ind.get("time")), "%d-%b-%Y %H:%M").replace(tzinfo=IST)
    except ValueError:
        return False
    return t.date() == now.astimezone(IST).date() and t.hour == 9 and t.minute < 15 \
        and str(ind.get("status", "")).upper() != "CLOSE"


def preopen(cues: dict, now: datetime) -> str | None:
    gift, ind = cues.get("GIFT Nifty"), cues.get("Nifty indicative")
    if not gift and not ind:
        return None
    lines = [f"<b>Pre-open – Nifty & GIFT Nifty ({now.astimezone(IST):%H:%M} IST)</b>", ""]
    if ind and _is_todays_preopen(ind, now):
        lines.append(cue_line("Nifty 50 indicative", ind))
    if gift:
        lines.append(cue_line("GIFT Nifty", gift))
    if "India VIX" in cues:
        lines.append(cue_line("India VIX", cues["India VIX"]))
    return "\n".join(lines)


def build(slot: str, now: datetime, cues: dict, records: list[dict]) -> str | None:
    if slot == "opening_cues":
        return opening_cues(cues, now)
    if slot == "stocks_in_focus":
        return stocks_in_focus(records, now)
    if slot == "preopen":
        return preopen(cues, now)
    raise ValueError(slot)
