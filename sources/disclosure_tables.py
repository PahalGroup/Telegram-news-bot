"""Explicit two-cell disclosure facts; no neighbouring-column inference."""
import re
from src.quality import clean

LABEL = re.compile(r'^(?:Total Bank Loan Facilities Rated|Long Term Rating|Short Term Rating|Shirt Term Rating)$', re.I)
RATING = re.compile(r'^(?:CRISIL|ICRA|CARE|IND(?:IA)?(?: Ratings)?)\s+[A-Z0-9+\-/ ()]+$', re.I)

def disclosure_table_evidence(rows, page):
    facts = []
    for row in rows:
        cells = [clean(c) for c in row]
        if len(cells) != 2 or not LABEL.fullmatch(cells[0]):
            continue
        label, value = cells
        if not value or '\n' in value:
            continue
        if 'facilities' in label.lower():
            valid = re.fullmatch(r'(?:Rs\.?|INR|₹)\s*[\d,.]+\s*(?:Crores?|Lakhs?|Million)', value, re.I)
        else:
            valid = RATING.fullmatch(value)
        if valid:
            facts.append({'text': f'{label}: {value}', 'page': page, 'method': 'explicit_disclosure_table'})
    return facts
