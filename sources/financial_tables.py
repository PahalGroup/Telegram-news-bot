"""Extract only explicitly labelled financial tables; never infer dates or units."""
import re
from src.quality import clean

METRIC = re.compile(r'^(?:\d+[.)]?\s*)?(revenue from operations|total income|net profit(?: / \(loss\))?|net loss|profit after tax|profit for the period|profit / \(loss\) for the period|ebitda)\s*$', re.I)
VALUE = re.compile(r'^(?:-?\d[\d,]*(?:\.\d+)?|\(\d[\d,]*(?:\.\d+)?\))$')
DATE = re.compile(r'\b\d{1,2}[./-]\d{1,2}[./-]\d{4}\b|\b\d{1,2}\s+[A-Za-z]+\s+\d{4}\b')
UNIT = re.compile(r'(?:₹|Rs\.?|INR|Rupees)\s*(?:in\s+)(lakhs?|crores?|millions?)', re.I)

def table_evidence(rows, context, page):
    """Require every numeric column to state its period and every page one scope/unit.
    Multi-level/merged headers and pages with mixed scopes are withheld in this version.
    """
    scopes = set(re.findall(r'\b(?:standalone|consolidated)\b', context, re.I))
    scopes = {s.lower() for s in scopes}
    units = {m.group(1).lower().rstrip('s') for m in UNIT.finditer(context)}
    if len(scopes) != 1 or len(units) != 1:
        return []
    unit, scope = next(iter(units)), next(iter(scopes))
    evidence = []
    header = None
    for row in rows:
        cells = [clean(c) for c in row]
        if not cells:
            continue
        if 'particular' in cells[0].lower():
            if len(cells) >= 2 and all(DATE.search(c) and re.search(r'\b(?:quarter|half year|year) ended\b', c, re.I) for c in cells[1:]):
                header = cells
            else:
                header = None
            continue
        if header is None or len(cells) != len(header):
            continue
        metric = METRIC.fullmatch(cells[0])
        if not metric or not all(VALUE.fullmatch(c) for c in cells[1:]):
            continue
        for period, value in zip(header[1:], cells[1:]):
            # Parentheses and negative signs are copied, not reinterpreted.
            text = f'{scope.capitalize()} {metric.group(1)}: INR {value} {unit}; {period}.'
            evidence.append({'text': text, 'page': page, 'method': 'explicit_table',
                             'metric': metric.group(1), 'value': value, 'unit': unit,
                             'period': period, 'scope': scope})
    return evidence
