"""Conservative event checks. Facts remain verbatim; no inferred currencies or actions."""
import re
from src.quality import clean, substantive, sentences

AMOUNT = re.compile(r'(?:₹|Rs\.?|INR|USD|US\$|EUR|GBP|\$|€|£)\s*[\d,]+(?:\.\d+)?|\b[\d,.]+\s+(?:crore|lakh|million|billion)\b', re.I)
CAPACITY = re.compile(r'\b[\d,.]+\s*(?:MW|MWh|GW|tonnes?|units|km)\b', re.I)
ORDER_ACTION = re.compile(r'\b(?:awarded|secured|received|receives|wins?|won)\b', re.I)
ROLE = re.compile(r'\b(?:director|CEO|CFO|chief .*?officer|company secretary|chairman|chairperson|managing director)\b', re.I)
PERSON = re.compile(r'\b(?:Mr|Ms|Mrs|Dr|Shri|Smt)\.?\s+[A-Z][\w-]+(?:\s+[A-Z][\w-]+)+\b')
MANAGEMENT_ACTION = re.compile(r'\b(?:appointed|appointment|resign\w*|assumed charge|ceased|cessation|retir\w*|superannu\w*|tenure extension)\b', re.I)
NEGATED = re.compile(r'\b(?:not|no|never)\b.{0,35}\b(?:awarded|received|secured|approved|appointed|resigned)\b', re.I)

def candidates(title, description):
    result, seen = [], set()
    # Select before limiting: useful facts can be at the end of a long filing.
    for line in [*description.splitlines(), title]:
        for text in sentences(line):
            text = clean(text).lstrip('•-▪►* \ufe0f')
            key = text.casefold().rstrip('.')
            if substantive(text) and key not in seen:
                result.append(text)
                seen.add(key)
    return result

def evaluate_event(category, company, title, description):
    points = candidates(title, description)
    chosen, reason = [], ''
    label = category.replace('_', ' ')
    if category == 'ORDER_WIN':
        chosen = [p for p in points if ORDER_ACTION.search(p)
                  and re.search(r'\b(?:order|contract|letter of (?:intent|award)|LOI|LOA)\b', p, re.I)
                  and (AMOUNT.search(p) or CAPACITY.search(p)) and not NEGATED.search(p)]
        label, reason = 'ORDER UPDATE', 'ORDER_REQUIRES_ACTION_AND_VALUE_OR_CAPACITY'
    elif category == 'FINANCING':
        chosen = [p for p in points if re.search(r'\b(?:loan|lease|financ\w*|credit facility)\b', p, re.I)
                  and AMOUNT.search(p) and re.search(r'\b(?:approved|availed|executed|sanctioned|secured|entered)\b', p, re.I)
                  and not NEGATED.search(p)]
        label, reason = 'FINANCING', 'FINANCING_REQUIRES_ACTION_AND_AMOUNT'
    elif category == 'MANAGEMENT_CHANGE':
        chosen = [p for p in points if ROLE.search(p) and MANAGEMENT_ACTION.search(p)
                  and PERSON.search(p) and not NEGATED.search(p)]
        label, reason = 'MANAGEMENT UPDATE', 'MANAGEMENT_REQUIRES_NAMED_PERSON_ROLE_AND_ACTION'
    else:
        raise ValueError('Unsupported evidence category')
    return {
        'publish': bool(chosen), 'materiality_score': 88 if chosen else 0,
        'category': category, 'headline': f'{company} – {label}',
        'summary_points': chosen[:5], 'key_metrics': [], 'what_it_means': '',
        'rejection_reason': '' if chosen else reason,
    }
