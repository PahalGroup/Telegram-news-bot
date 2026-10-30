"""Conservative event checks. Facts remain verbatim; no inferred currencies or actions."""
import re
from src.quality import clean, substantive, sentences

AMOUNT = re.compile(r'(?:₹|Rs\.?|INR|USD|US\$|EUR|GBP|\$|€|£)\s*[\d,]+(?:\.\d+)?|\b[\d,.]+\s+(?:crore|lakh|million|billion)\b', re.I)
CAPACITY = re.compile(r'\b[\d,.]+\s*(?:MW|MWh|GW|tonnes?|units|km)\b', re.I)
ORDER_ACTION = re.compile(r'\b(?:awarded|secured|received|receives|wins?|won)\b', re.I)
ROLE = re.compile(r'\b(?:director|CEO|CFO|chief .*?officer|company secretary|chairman|chairperson|managing director)\b', re.I)
PERSON = re.compile(r'\b(?:Mr|Ms|Mrs|Dr|Shri|Smt)\.?\s+[A-Z][\w-]+(?:\s+[A-Z][\w-]+)+\b')
MANAGEMENT_ACTION = re.compile(r'\b(?:appointed|appointment|resign\w*|assumed charge|(?:took|taken) over (?:the )?charge|ceased|cessation|retir\w*|superannu\w*|tenure extension)\b', re.I)
def uncertain(text, action_pattern):
    # Reference numbers are not negation. Otherwise unresolved negative claims
    # remain withheld; modality must qualify the event, not a later delivery term.
    value = re.sub(r"\b(?:Ref(?:erence)?\.?\s+)?No\.\s*", "", text, flags=re.I)
    if re.search(r"\b(?:not|no|never|neither|nor|without|denied|denies|rumou?r|unconfirmed)\b", value, re.I):
        return True
    action = re.search(action_pattern, value, re.I)
    if not action:
        return True
    prefix = value[:action.start()]
    prefix = re.sub(r"\bMay\s+\d{1,2}\b", "", prefix)
    return bool(re.search(
        r"\b(?:will|shall|may|might|would|could|expected|planned|proposed|if)\b", prefix, re.I))

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
                  and (AMOUNT.search(p) or CAPACITY.search(p)) and not uncertain(p, ORDER_ACTION.pattern)]
        label, reason = 'ORDER UPDATE', 'ORDER_REQUIRES_ACTION_AND_VALUE_OR_CAPACITY'
    elif category == 'CAPACITY_UPDATE':
        chosen = [p for p in points if re.search(r"\b(?:has (?:fully )?commissioned|commissioned|commenced commercial)\b", p, re.I)
                  and CAPACITY.search(p)
                  and not uncertain(p, r"\b(?:commissioned|commenced commercial)\b")]
        label, reason = 'CAPACITY UPDATE', 'CAPACITY_REQUIRES_COMPLETED_ACTION_AND_QUANTITY'
    elif category == 'FINANCING':
        chosen = [p for p in points if re.search(r'\b(?:loan|lease|financ\w*|credit facility)\b', p, re.I)
                  and AMOUNT.search(p) and re.search(r'\b(?:approved|availed|executed|sanctioned|secured|entered)\b', p, re.I)
                  and not uncertain(p, r"\b(?:approved|availed|executed|sanctioned|secured|entered)\b")]
        label, reason = 'FINANCING', 'FINANCING_REQUIRES_ACTION_AND_AMOUNT'
    elif category == 'MANAGEMENT_CHANGE':
        chosen = [p for p in points if ROLE.search(p) and MANAGEMENT_ACTION.search(p)
                  and PERSON.search(p) and not uncertain(p, MANAGEMENT_ACTION.pattern)]
        label, reason = 'MANAGEMENT UPDATE', 'MANAGEMENT_REQUIRES_NAMED_PERSON_ROLE_AND_ACTION'
    else:
        raise ValueError('Unsupported evidence category')
    return {
        'publish': bool(chosen), 'materiality_score': 88 if chosen else 0,
        'category': category, 'headline': f'{company} – {label}',
        'summary_points': chosen[:5], 'key_metrics': [], 'what_it_means': '',
        'rejection_reason': '' if chosen else reason,
    }
