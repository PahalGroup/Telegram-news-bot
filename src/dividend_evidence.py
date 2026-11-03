"""A dividend policy or face value alone is not a dividend declaration."""
import re
from src.quality import clean, sentences

DECLARATION = re.compile(r'\b(?:declared|approved|recommended|recommends|declares|approves)\b', re.I)
AMOUNT = re.compile(r'\bdividend\s*(?:of|at)?\s*(?:₹|Rs\.?|INR)\s*[\d,]+(?:\.\d+)?'
                    r'\s*(?:/-)?\s*(?:\(\s*\d+(?:\.\d+)?\s*%\s*\))?\s*'
                    r'(?:per|for each|/)\s*(?:equity\s+)?share\b', re.I)


def evaluate_dividend(company, title, description, category):
    points = []
    for line in description.splitlines() + [title]:
        for point in sentences(line):
            if (DECLARATION.search(point) and AMOUNT.search(point)
                    and not re.search(r'\b(?:not declared|not recommended|not approved|'
                                      r'has not|have not|may|might|could|subject to board)\b', point, re.I)):
                value = clean(point)
                if value not in points:
                    points.append(value)
    kind = ''
    if points:
        declaration = ' '.join(points)
        match = re.search(r'\b(interim|final|special)\s+dividend\b', declaration, re.I)
        if not match:
            match = re.search(r'\b(interim|final|special)\s+dividend\b', title, re.I)
        kind = (match[1].upper() + ' ') if match else ''
    return {'publish': bool(points), 'materiality_score': 88 if points else 0,
            'category': category, 'headline': f'{company} – {kind}DIVIDEND',
            'summary_points': points[:3], 'key_metrics': [], 'what_it_means': '',
            'rejection_reason': '' if points else 'DIVIDEND_REQUIRES_DECLARATION_AND_PER_SHARE_AMOUNT'}
