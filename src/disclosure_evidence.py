"""Select event facts before limiting; retain pending-transfer qualifications."""
import re
from src.quality import clean, sentences
from src.event_evidence import candidates, AMOUNT

def response(category, company, label, points, reason):
    return {'publish': bool(points), 'materiality_score': 88 if points else 0,
            'category': category, 'headline': f'{company} – {label}',
            'summary_points': points, 'key_metrics': [], 'what_it_means': '',
            'rejection_reason': '' if points else reason}

_TARGET = re.compile(
    r"(?:Private Limited|Pvt\.?\s*Ltd|Limited|Ltd\b|Hospital|Centre|Center|LLC|GmbH|Inc\b)",
    re.I,
)
_TITLE_TARGET = re.compile(
    r"\b(?:acquisition|acquire[ds]?|acquiring|completion of acquisition)\s+"
    r"(?:of\s+)?(?:(?:an?|the)\s+)?(?:(?:entire\s+)?(?:business operations and assets of\s+)?)?"
    r"(?:equity shares? of\s+)?([A-Z][A-Za-z0-9&.,'’\- ]{2,80}?" + _TARGET.pattern + r")",
    re.I,
)
_STAKE = re.compile(r"\d+(?:\.\d+)?\s*%")
_ACQ_ACTION = re.compile(
    r"\b(?:has acquired|have acquired|acquires|acquisition of|agreement to acquire|"
    r"completed the acquisition|completion of acquisition)\b", re.I)


def _named_target(title, description):
    """Target named in the title or the official first announcement line."""
    lead = ""
    for line in (description or "").splitlines()[:3]:
        if len(line.strip()) >= 20:
            lead = line.strip()
            break
    for text in (title or "", lead):
        m = _TITLE_TARGET.search(text)
        if m:
            return m.group(1).strip()
    return ""


def acquisition(company, title, description):
    points = candidates(title, description)
    # A subject line may carry the only complete target name. Retain just the
    # contiguous event phrase, without the introductory regulatory citation.
    identities = []
    for p in points:
        m = re.search(r'\b(?:Agreement to acquire|Acquisition of)\b.*', p, re.I)
        if m and re.search(r'\b(?:stake|shareholding|shares|equity)\s+(?:in|of)\s+[A-Z]', m.group(), re.I):
            identities.append(m.group().removesuffix('– Reg.').strip())
        elif re.match(r'Name of the target entity\s*:', p, re.I):
            identities.append(p)
    title_target = _named_target(title, description)
    if title_target and not identities:
        identities.append(f"Acquisition of {title_target}")
    # Stake % may sit on the following line of a table or the next sentence.
    paired = []
    for i, p in enumerate(points):
        nxt = points[i + 1] if i + 1 < len(points) else ""
        paired.append(p if _STAKE.search(p) else (p + " " + nxt).strip())
    core = [p for p, joined in zip(points, paired) if _ACQ_ACTION.search(p)
            and _STAKE.search(joined)
            and not re.search(r'\b(?:not|never|if|may|might)\b', p, re.I)]
    if not core and title_target:
        # Title already names the target; an action sentence is enough (business
        # transfers often have no stake %).
        token = re.escape(title_target.split()[0])
        core = [p for p in points if _ACQ_ACTION.search(p)
                and re.search(token, p, re.I)
                and not re.search(r'\b(?:not|never|if|may|might)\b', p, re.I)]
        if not core:
            core = [p for p in points
                    if _ACQ_ACTION.search(p)
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
    action = re.compile(
        r'\b(?:assigned|assignment|assigns?|reaffirmed|affirmation|affirms?|affirmed|'
        r'upgraded|upgrade|downgraded|downgrade|revised|revision|'
        r'withdrawn|withdrawal|placed|retained)\b', re.I)
    raw_lines = [ln.strip() for ln in description.splitlines() if ln.strip()] + [title]
    # Grade often sits on the line after the agency/action heading.
    windows = []
    for i, line in enumerate(raw_lines):
        nxt = raw_lines[i + 1] if i + 1 < len(raw_lines) else ""
        windows.append((line, (line + " " + nxt).strip()))
    points = []
    for original, joined in windows:
        for p, src in ((original, original), (joined, joined)):
            if (agency.search(src) and grade.search(src) and action.search(src)
                    and not re.search(r'\b(?:may|might|if|not|whether)\b', src, re.I)
                    and 25 <= len(src) <= 900
                    and src.casefold() not in {v.casefold() for v in points}):
                # Prefer the joined window when the grade was on the next line.
                points.append(src)
                break
    return response('CREDIT_RATING', company, 'CREDIT RATING UPDATE', points[:3],
                    'RATING_REQUIRES_AGENCY_GRADE_AND_ACTION')
