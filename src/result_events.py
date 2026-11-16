"""Cross-filing deduplication for explicitly comparable financial facts only."""
import hashlib
import json
import math
import re
from decimal import Decimal
from src.quality import clean
from src.result_identity import result_fact_key
from src.pipeline_observer import publication_time


def company_key(value):
    # Do not collapse Industries, Services or India into an unrelated issuer.
    name = clean(value).casefold().strip()
    return re.sub(r'\s+(?:limited|ltd\.?)$', '', name).strip()


def identity(point):
    key = result_fact_key(point)
    if key is None:
        return None
    def number(v):
        value = format(v, 'f')
        return value.rstrip('0').rstrip('.') if '.' in value else value
    fields = [number(v) if isinstance(v, Decimal) else v for v in key]
    digest = lambda obj: hashlib.sha256(json.dumps(obj, ensure_ascii=False).encode()).hexdigest()
    return digest(fields[:6]), digest(fields)


def validate_result_event(event):
    if not isinstance(event, dict) or type(event.get('version')) is not int or event.get('version') != 1:
        raise ValueError('Invalid result event version')
    if not isinstance(event.get('company'), str) or not event['company']:
        raise ValueError('Invalid result event company')
    stamp = event.get('publication_epoch')
    if (not isinstance(stamp, (int, float)) or isinstance(stamp, bool)
            or not math.isfinite(stamp) or stamp <= 0):
        raise ValueError('Invalid result event publication time')
    facts = event.get('facts')
    if (not isinstance(facts, dict) or not 1 <= len(facts) <= 5
            or any(not isinstance(k, str) or not isinstance(v, str)
                   or not re.fullmatch('[0-9a-f]{64}', k)
                   or not re.fullmatch('[0-9a-f]{64}', v) for k, v in facts.items())):
        raise ValueError('Invalid result event facts')


def select_new_result_points(points, company, item, records):
    entity = company_key(company)
    published = publication_time(item.get('published_at'), item.get('source'))
    if not entity or published is None:
        return points, None, False
    pairs = [identity(point) for point in points]
    conflicts = {slot for slot, value in (p for p in pairs if p)
                 if len({p[1] for p in pairs if p and p[0] == slot}) > 1}
    latest = {}
    for record in reversed(records[-200:]):
        event = record.get('result_event')
        if not event:
            continue
        validate_result_event(event)
        if event['company'] == entity:
            for slot, value in event['facts'].items():
                latest.setdefault(slot, (value, event['publication_epoch']))
    selected, facts, updated = [], {}, False
    for point, pair in zip(points, pairs):
        if pair is None or pair[0] in conflicts:
            # Unknown/mixed statements cannot borrow a nearby fact's identity.
            selected.append(point)
            continue
        slot, value = pair
        previous = latest.get(slot)
        if previous and (previous[0] == value or previous[1] > published.timestamp()):
            continue
        selected.append(point)
        facts[slot] = value
        updated = updated or previous is not None
    event = {'version': 1, 'company': entity, 'publication_epoch': published.timestamp(),
             'facts': facts} if facts else None
    return selected, event, updated


_HTML_TAG = re.compile(r'<[^>]+>')
_SOURCE_LINK = re.compile(r'<a\s+href=.*?</a>', re.I | re.S)
_PERIOD = re.compile(r'\b([QH][1-4])\s*FY\s*(\d{2})\b', re.I)
# Figures with a unit, a percent, a decimal, or a large integer that is not a year.
_AMOUNT = re.compile(
    r'(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?(?:\s*(crore|crs?\b|lakh|mn\b|million|bn\b|billion|%))?',
    re.I,
)


def result_amounts(text: str) -> set:
    """Numeric figures from a results post, ignoring dates, years, and the Source URL."""
    raw = _SOURCE_LINK.sub(' ', str(text or ''))
    raw = _HTML_TAG.sub(' ', raw)
    out = set()
    for m in _AMOUNT.finditer(raw):
        intpart = m.group(1).replace(',', '')
        frac = m.group(2)
        unit = m.group(3)
        try:
            whole = int(intpart)
        except ValueError:
            continue
        if not unit and frac is None and (whole <= 31 or 1900 <= whole <= 2100):
            continue
        value = float(intpart + ('.' + frac if frac else ''))
        out.add(round(value, 2))
    return out


def _period_token(text: str) -> str:
    m = _PERIOD.search(str(text or ''))
    return f"{m.group(1).upper()}FY{m.group(2)}" if m else ""


def _record_text(record: dict) -> str:
    return "\n".join(str(record.get(k) or "") for k in ("body", "lead", "headline", "event"))


def is_near_duplicate_result(pending, item, records) -> bool:
    """True when this results post restates figures already sent for the same company and period.

    NSE vs BSE near-copies often differ by one press-release line and the Source URL, so
    exact post fingerprints miss them. A genuine update that mostly adds new figures is kept.
    """
    event = (getattr(pending, "event", None) or "")
    category = (item or {}).get("category") or ""
    if "RESULT" not in event.upper() and "RESULT" not in str(category).upper():
        return False
    entity = company_key(getattr(pending, "company", None) or (item or {}).get("company") or "")
    if not entity:
        return False
    period = (_period_token(event) or _period_token(getattr(pending, "header", ""))
              or _period_token(" ".join(getattr(pending, "bullets", []) or [])))
    nums = result_amounts(pending.text() if hasattr(pending, "text") else "")
    if len(nums) < 3:
        return False
    for record in list(records or [])[-200:]:
        rec_co = company_key(record.get("company") or "")
        if rec_co != entity:
            continue
        rec_event = (record.get("event") or record.get("headline") or record.get("category") or "")
        if "RESULT" not in rec_event.upper() and "RESULT" not in str(record.get("category") or "").upper():
            continue
        rec_period = _period_token(rec_event) or _period_token(record.get("headline") or "")
        if period and rec_period and period != rec_period:
            continue
        prior = result_amounts(_record_text(record))
        if len(prior) < 3:
            continue
        # Almost every figure in the new post was already reported → near copy, not an update.
        if len(nums & prior) / len(nums) >= 0.8:
            return True
    return False
