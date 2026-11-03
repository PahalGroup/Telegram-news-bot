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
