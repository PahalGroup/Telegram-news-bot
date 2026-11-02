"""Select event facts before limiting; retain pending-transfer qualifications."""
import re
from src.quality import clean, sentences
from src.event_evidence import candidates, AMOUNT

def response(category, company, label, points, reason):
    return {'publish': bool(points), 'materiality_score': 88 if points else 0,
            'category': category, 'headline': f'{company} – {label}',
            'summary_points': points, 'key_metrics': [], 'what_it_means': '',
            'rejection_reason': '' if points else reason}

def acquisition(company, title, description):
    points = candidates(title, description)
    # A subject line may carry the only complete target name. Retain just the
    # contiguous event phrase, without the introductory regulatory citation.
    identities = []
    for p in points:
        m = re.search(r'\b(?:Agreement to acquire|Acquisition of)\b.*', p, re.I)
        if m and re.search(r'\bstake in\s+[A-Z]', m.group(), re.I):
            identities.append(m.group().removesuffix('– Reg.').strip())
        elif re.match(r'Name of the target entity\s*:', p, re.I):
            identities.append(p)
    core = [p for p in points if re.search(r'\b(?:has acquired|have acquired|acquires)\b', p, re.I)
            and re.search(r'\d+(?:\.\d+)?\s*%', p)
            and not re.search(r'\b(?:not|never|if|may|might)\b', p, re.I)]
    core.sort(key=lambda p: (not bool(AMOUNT.search(p)), len(p)))
    pending = [p for p in points if re.search(
        r'\b(?:transfer of (?:the )?shares.*(?:expected|pending)|shares (?:will|are to) be transferred)\b', p, re.I)]
    if not identities or not core:
        return response('CORP_RESTRUCTURE', company, 'ACQUISITION UPDATE', [],
                        'ACQUISITION_REQUIRES_TARGET_AND_STAKE_ACTION')
    label = 'ACQUISITION AGREEMENT' if pending or re.search(r'agreement to acquire', title, re.I) else 'ACQUISITION UPDATE'
    # A late qualification must not disappear behind historical target-company facts.
    chosen = [identities[0], core[0], *pending[:1]]
    return response('CORP_RESTRUCTURE', company, label, list(dict.fromkeys(chosen)), '')

def regulatory(company, title, description):
    points = candidates(title, description)
    context = [p for p in points if re.search(r'\b(?:US\s*FDA|United States Food.*Drug Administration)\b', p, re.I)
               and re.search(r'\b(?:conducted|completed|concluded)\b.*\binspection\b|\binspection\b.*\b(?:completed|concluded)\b', p, re.I)]
    outcome = [p for p in points if re.search(r'\bForm\s*483\b', p, re.I)
               and re.search(r'\bobservations?\b', p, re.I)
               and re.search(r'\b(?:issued|received|containing|with)\b', p, re.I)]
    chosen = context[:1] + outcome[:1] if context and outcome else []
    return response('REGULATORY_UPDATE', company, 'FDA INSPECTION UPDATE', chosen,
                    'INSPECTION_REQUIRES_CONTEXT_AND_OUTCOME')

def rating(company, title, description):
    from sources.disclosure_tables import LABEL
    facts = []
    for line in description.splitlines():
        label, sep, value = line.partition(':')
        if sep and LABEL.fullmatch(clean(label)) and clean(value):
            facts.append(clean(line))
    if not any(re.search(r'(?:Long|Short|Shirt) Term Rating:', p, re.I) for p in facts):
        return None  # Existing prose path handles non-tabular rating notices.
    return response('CREDIT_RATING', company, 'CREDIT RATING UPDATE', facts[:3], '')


def prose_rating(company, title, description):
    agency = re.compile(r'\b(?:ICRA|CRISIL|CARE|India Ratings|IND|Brickwork)\b', re.I)
    grade = re.compile(r'(?<![A-Za-z])(?:AAA|AA[+-]?|A[123][+-]?|A[+-]?|BBB[+-]?|BB[+-]?|B[+-]?|C|D)(?![A-Za-z0-9])')
    action = re.compile(r'\b(?:assigned|reaffirmed|upgraded|downgraded|revised|withdrawn|placed|retained)\b', re.I)
    points = []
    for line in description.splitlines() + [title]:
        for p in sentences(line):
            if (agency.search(p) and grade.search(p) and action.search(p)
                    and not re.search(r'\b(?:may|might|if|not|whether)\b', p, re.I)
                    and 25 <= len(p) <= 700
                    and p.casefold() not in {v.casefold() for v in points}):
                points.append(p)
    return response('CREDIT_RATING', company, 'CREDIT RATING UPDATE', points[:3],
                    'RATING_REQUIRES_AGENCY_GRADE_AND_ACTION')
