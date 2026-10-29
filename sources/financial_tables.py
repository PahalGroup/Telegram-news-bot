"""Extract only explicitly labelled financial tables; never infer dates or units."""
import re
from src.quality import clean

METRIC = re.compile(r'^(?:\d+[.)]?\s*)?(revenue from operations|total income|net profit(?: / \(loss\))?|net loss|profit after tax|profit for the period|profit / \(loss\) for the period|ebitda)\s*$', re.I)
VALUE = re.compile(r'^(?:-?\d[\d,]*(?:\.\d+)?|\(\d[\d,]*(?:\.\d+)?\))$')
DATE = re.compile(r'\b\d{1,2}[./-]\d{1,2}[./-]\d{4}\b|\b\d{1,2}\s+[A-Za-z]+\s+\d{4}\b')
UNIT = re.compile(r'(?:₹|Rs\.?|INR|Rupees)\s*(?:in\s+)(lakhs?|crores?|millions?)', re.I)

def table_evidence(rows, context, page):
    """Require every numeric column to state its period and every page one scope/unit.
    Merged headers require geometry resolution first; mixed scopes are withheld.
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


def resolve_merged_headers(rows, cell_rows):
    """Resolve header spans from PDF rectangles, never by filling blank text cells.

    A header cell contributes only to columns fully covered by its rectangle.
    Each resulting numeric column must have exactly one period kind and date.
    Invalid/ambiguous geometry returns the original rows for conservative handling.
    """
    first_metric = next((i for i, row in enumerate(rows)
                         if row and METRIC.fullmatch(clean(row[0]))), None)
    if first_metric is None or first_metric == 0 or len(cell_rows) != len(rows):
        return rows
    columns = cell_rows[first_metric]
    width = len(rows[first_metric])
    if len(columns) != width or width < 2 or any(c is None for c in columns):
        return rows
    pieces = [[] for _ in columns]
    for index in range(first_metric):
        if len(cell_rows[index]) != len(rows[index]):
            return rows
        for text, rect in zip(rows[index], cell_rows[index]):
            text = clean(text)
            if not text or rect is None:
                continue
            for col in range(1, width):
                left, _, right, _ = columns[col]
                # One point tolerance accommodates PDF rounding, not partial overlap.
                if rect[0] <= left + 1 and rect[2] >= right - 1:
                    if text not in pieces[col]:
                        pieces[col].append(text)
    headers = ['Particulars']
    for parts in pieces[1:]:
        joined = ' '.join(parts)
        dates = DATE.findall(joined)
        kinds = re.findall(r'\b(?:half[ -]year|quarter|year) ended\b', joined, re.I)
        if len(dates) != 1 or len(kinds) != 1:
            return rows
        headers.append(joined)
    return [headers] + rows[first_metric:]
