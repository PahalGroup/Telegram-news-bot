"""Evidence gates for option grants and share splits; no boilerplate inference."""
import re
from src.quality import clean, sentences
from src.disclosure_evidence import response

OPTIONS = re.compile(r'\b(?:stock options?|ESOP|ESOS|employee.{0,30}stock option)\b', re.I)
SPLIT = re.compile(r'\b(?:stock split|split of (?:equity )?shares|sub[- ]?division)\b', re.I)
CONDITIONAL = re.compile(r'\b(?:if|in case|whether|may|might|not|never)\b', re.I)
GRANT = re.compile(
    r'\b(?:approved\s+(?:the\s+)?grant of|granted)\s+'
    r'[\d,]+\s+(?:employee\s+)?stock options?\b', re.I)
SPLIT_ACTION = re.compile(
    r'\bapproved\b.{0,80}\b(?:stock split|split of (?:equity )?shares|sub[- ]?division)\b', re.I)
FACE_VALUE = re.compile(r'(?:Rs\.?|INR|₹)\s*[\d,]+(?:\.\d+)?', re.I)


def evaluate_capital_action(company, title, description):
    points = []
    for line in description.splitlines():
        for raw in sentences(line):
            point = clean(raw).lstrip('•▪- ')
            if 25 <= len(point) <= 700 and point not in points:
                points.append(point)
    grants, splits = [], []
    for point in points:
        if CONDITIONAL.search(point):
            continue
        if GRANT.search(point):
            grants.append(point)
        if (SPLIT_ACTION.search(point) and re.search(r'\bequity shares?\b', point, re.I)
                and (len(FACE_VALUE.findall(point)) >= 2
                     or re.search(r'\bratio\s+\d+\s*:\s*\d+\b', point, re.I))):
            splits.append(point)
    if grants and splits:
        return response('CORP_RESTRUCTURE', company, 'CAPITAL ACTIONS',
                        list(dict.fromkeys(grants + splits))[:5], '')
    if grants:
        return response('CORP_RESTRUCTURE', company, 'STOCK OPTION GRANT', grants[:5], '')
    if splits:
        return response('CORP_RESTRUCTURE', company, 'STOCK SPLIT', splits[:5], '')
    return response('CORP_RESTRUCTURE', company, 'CAPITAL ACTION', [],
                    'CAPITAL_ACTION_REQUIRES_APPROVAL_AND_QUANTITY_OR_TERMS')
