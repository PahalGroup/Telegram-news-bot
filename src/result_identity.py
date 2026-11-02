"""Conservative equivalence for complete, single-metric result statements.

Only recognized grammar is compared. Missing periods or extra commentary are
left alone; this does not reconcile separate filings or infer entity scope.
"""
import re
from decimal import Decimal

PERIOD = re.compile(r'\b(?:in\s+)?(?P<period>[QH][1-4])\s*FY\s*(?P<year>\d{2}(?:\d{2})?)\b', re.I)
FACT = re.compile(
    r'^(?:(?P<scope>consolidated|standalone)\s+)?'
    r'(?P<metric>revenue from operations|profit after tax|EBITDA)\s+'
    r'(?:(?:of|was|stood at|reached)\s+)?'
    r'(?P<currency>₹|INR|Rs\.?|USD|EUR|GBP)\s*'
    r'(?P<amount>-?\d[\d,]*(?:\.\d+)?)\s*'
    r'(?P<unit>Mn|million|crores?|Cr|lakhs?|billion)\s*'
    r'(?:,?\s*\(?\s*(?P<direction>[+-])(?P<growth>\d+(?:\.\d+)?)%\s*'
    r'(?P<basis>YoY|QoQ)\s*\)?)?$', re.I)


def result_fact_key(point):
    periods = list(PERIOD.finditer(point))
    if len(periods) != 1:
        return None
    period = periods[0]
    if period['period'].upper().startswith('H') and period['period'][1] not in '12':
        return None
    text = (point[:period.start()] + point[period.end():]).strip().rstrip('.')
    text = re.sub(r'\byear on year\b', 'YoY', text, flags=re.I)
    text = re.sub(r'\bquarter on quarter\b', 'QoQ', text, flags=re.I)
    text = re.sub(r'\bup\s+', '+', text, flags=re.I)
    text = re.sub(r'\bdown\s+', '-', text, flags=re.I)
    text = re.sub(r'\s+', ' ', text).strip()
    match = FACT.fullmatch(text)
    if not match:
        return None
    fields = match.groupdict()
    currency = fields['currency'].upper().rstrip('.')
    currency = 'INR' if currency in ('₹', 'RS') else currency
    unit = fields['unit'].lower()
    unit = {'mn': 'million', 'cr': 'crore', 'crores': 'crore', 'lakhs': 'lakh'}.get(unit, unit)
    year = period['year']
    year = '20' + year if len(year) == 2 else year
    return (
        (fields['scope'] or '').lower(), fields['metric'].lower(),
        period['period'].upper(), year, currency, unit,
        Decimal(fields['amount'].replace(',', '')),
        fields['direction'], Decimal(fields['growth']) if fields['growth'] else None,
        (fields['basis'] or '').lower(),
    )


def unique_result_facts(points):
    result, seen = [], set()
    for point in points:
        key = result_fact_key(point)
        if key is not None and key in seen:
            continue
        result.append(point)
        if key is not None:
            seen.add(key)
    return result
