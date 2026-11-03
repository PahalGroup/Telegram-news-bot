"""Select explicit result values; exclude audit scope, chart labels and history.

This is a conservative prose gate, not complete period/scope reconciliation.
"""
import re
from collections import Counter
from src.quality import clean_points, sentences

METRIC = re.compile(r'\b(?:net profit|net loss|profit after tax|profit before tax|revenues?|sales|ebitda|PAT|PBT)\b', re.I)
NUMBER = r'[-+~]?(?:\(?\d[\d,]*(?:\.\d+)?\)?)'
VALUE = re.compile(
    r'(?:₹|\b(?:Rs\.?|INR|USD|EUR|GBP)\s*)\s*' + NUMBER +
    r'|' + NUMBER + r'\s*(?:%|per cent\b|percent\b|crores?\b|lakhs?\b|millions?\b|billions?\b|cr\b|mn\b)', re.I)
BACKGROUND = re.compile(
    r'\b(?:we (?:did not review|have reviewed|have audited|have examined)|not been reviewed|not reviewed by|'
    r'interim financial information|as considered in the statement|independent auditors?|'
    r'cash flow from operating activities|key monitorable|successfully listed|'
    r'completed \d+ years|net profit cross(?:ed|ing)?|brief (?:history|profile)|'
    r'guidance|guided|expect(?:ed|s)?|forecast|target(?:ed|s)?|CAGR|order book|'
    r'annuali[sz]ed|over the next|will get added|earning[s]? before interest|performance indicators on|'
    r'accompanying statement|whose (?:interim )?financial|not been audited|franchisees?|'
    r'(?:additional )?revenue potential|proposed estimated investment|geographical revenue|revenue break-up|historical financial|adjusted PAT excludes|products that have been developed)\b', re.I)
RELATION = re.compile(r'\b(?:was|were|stood|grew|growing|growth|increas\w*|rose|risen|'
                      r'declin\w*|decreas\w*|fell|report\w*|record\w*|up|down|expanded|'
                      r'expands|improved|doubled|at|of)\b', re.I)
EXPLICIT_TABLE = re.compile(r'^(?:Consolidated|Standalone) [^:]+: INR [-\d(].*; (?:Quarter|Half year|Year) ended ', re.I)


# A number elsewhere in a paragraph cannot establish the value of a metric.
# Keep only explicit bridges; unfamiliar prose is withheld, not reconstructed.
BRIDGE_WORDS = set(('from operations operating consolidated standalone consol adjusted '
    'margin margins growth for the quarter half year ended in of at to by was were is are '
    'stood grew growing increased increase increases rose risen declined decline decreased '
    'fell reported recorded up down expanded expands improved doubled reached reaching '
    'surged surging higher lower yoy qoq sequential basis sequentially').split())


def metric_has_value(point):
    if EXPLICIT_TABLE.search(point):
        return True
    for metric in METRIC.finditer(point):
        for value in VALUE.finditer(point, metric.end()):
            bridge = point[metric.end():value.start()]
            if len(bridge) > 140 or METRIC.search(bridge):
                break
            bridge = re.sub(r'\b(?:Q[1-4]|H[12])\s*(?:FY\s*\d{2,4})?\b|\bFY\s*\d{2,4}\b',
                            ' ', bridge, flags=re.I)
            bridge = re.sub(r'\b\d+(?:\.\d+)?\s*bps\b', ' ', bridge, flags=re.I)
            words = re.findall(r'[A-Za-z]+', bridge.lower())
            # Remove only recognized words and punctuation: an unrelated number,
            # label or business narrative must not bridge the metric to a value.
            residue = re.sub(r'[A-Za-z]+|[\s:;(),%.\-–—]', '', bridge)
            if not residue and all(word in BRIDGE_WORDS for word in words):
                return True
    return False



def unsafe_result_context(point):
    if re.search(r'\bunbilled revenue\b|\brevenue recogni[sz]ed under Ind AS', point, re.I):
        return 'accounting_balance_note'
    if re.search(r'\b(?:subscription-based|lifecycle-led)\s*[.,;:]?$', point, re.I):
        return 'incomplete_fragment'
    if re.search(r'\bmargin\s+(?:expanded|increased|improved)\b.*\(-\d+(?:\.\d+)?\s*bps', point, re.I):
        return 'contradictory_margin_direction'
    if re.search(r'\b(?:a|an|increasing|decreasing)\s*[.,;:]?$', point, re.I):
        return 'incomplete_fragment'
    # Recognition/refund notes explicitly assigned to a previous period are not
    # the current results. Keep ordinary current-versus-prior comparisons.
    if (re.search(r'\b(?:recognised|recognized|refunded)\b', point, re.I)
            and re.search(r'\b(?:previous|prior|preceding)\s+(?:quarter|year|period)\b', point, re.I)
            and not re.search(r'\bcurrent\s+(?:quarter|year|period)\b', point, re.I)):
        return 'prior_period_recognition_note'
    # Historical multi-year slides are not the current period's result.
    years = [int(y) + (2000 if len(y) == 2 else 0)
             for y in re.findall(r'\bFY\s*(\d{2,4})\b', point, re.I)]
    year_range = re.search(r'\bFY\s*(\d{4})\s*[-–]\s*(\d{4})\b', point, re.I)
    if year_range:
        years.extend([int(year_range[1]), int(year_range[2])])
    span = re.search(r'\bFY\s*(\d{2})\s+to\s+(?:FY\s*)?(\d{2})\b', point, re.I)
    if span:
        years.extend([2000 + int(span[1]), 2000 + int(span[2])])
    if (years and max(years) - min(years) > 1
            and not re.search(r'\b(?:Q[1-4]|H[12])\s*FY', point, re.I)):
        return 'historical_multi_year_comparison'
    if re.search(r'\b(?:at|of|to|and)\s*,|\byou can see\b', point, re.I):
        return 'incomplete_transcript_fragment'
    # A bare scorecard row can belong to a subsidiary on another PDF panel.
    # Do not attach it to the parent-company headline without explicit scope/period.
    if re.match(r'^(?:total |operating )?(?:revenue(?:s)?(?: from operations)?|'
                r'profit after tax(?: \(PAT\))?|net profit|adjusted EBITDA|EBITDA)\s*(?::|₹|Rs\b|INR\b)',
                point, re.I) and not re.search(
                    r'\b(?:consolidated|standalone|Q[1-4]|H[12]|FY\s*\d{2,4}|'
                    r'quarter ended|year ended|half year ended)\b', point, re.I):
        return 'scorecard_missing_scope_or_period'
    tail = re.search(r'%\s+([a-z]{1,3})\s+([a-z]{1,3})$', point)
    if tail and tail.groups() not in (('yoy', 'cc'), ('qoq', 'cc'), ('vs', 'ly')):
        return 'garbled_chart_tail'
    return ''


def select_result_points(title, description):
    # Mixed group/parent disclosures may also contain unlabeled subsidiary panels.
    # Never inherit the parent headline's scope merely from a nearby number.
    scope_metric = r'\b{}\s+(?:profit after tax|net profit|revenue|EBITDA)\b'
    mixed_scope = all(re.search(scope_metric.format(scope), description, re.I)
                      for scope in ('consolidated', 'standalone'))
    candidates = []
    for line in description.splitlines() + [title]:
        # Embedded PDF bullet glyphs are boundaries, not a reason to combine metrics.
        for bullet in re.split(r'[•▪●✓\uf0b7\uf0a7]|\s+(?=Premiums on our infrastructure\b)', line):
            candidates.extend(sentences(bullet))
    points, rejected = [], Counter()
    for point in clean_points(candidates, limit=None):
        context_error = unsafe_result_context(point)
        if mixed_scope and not re.search(r'\b(?:consolidated|standalone)\b', point, re.I):
            rejected['ambiguous_scope_in_mixed_filing'] += 1
        elif context_error:
            rejected[context_error] += 1
        elif point.count('(') != point.count(')'):
            rejected['incomplete_parenthesis'] += 1
        elif BACKGROUND.search(point):
            rejected['audit_scope_or_background'] += 1
        elif not METRIC.search(point) or not VALUE.search(point) or not metric_has_value(point):
            rejected['missing_explicit_metric_value'] += 1
        elif re.search(r'\b(?:grows|following|expansion|supported by healthy|and EBITDA|lower|higher)\s*[.:]?$|[-–]\s*$|\b(?:in|for|from|to)\s+Q[1-4]\s*$', point, re.I):
            rejected['incomplete_fragment'] += 1
        elif not EXPLICIT_TABLE.search(point) and re.search(
                r'(?<![\w.])[+-]?\(?\d[\d,.]*\)?%?(?:\s+bps)?\s+[+-]?\(?\d[\d,.]*\)?%?', point):
            rejected['unlabelled_numeric_row'] += 1
        elif point[:1].islower():
            rejected['leading_fragment'] += 1
        else:
            points.append(point)
    from src.result_identity import unique_result_facts
    # Same explicit period and scope, different margin values: missing panel
    # context must not be guessed. Other periods and labelled scopes stay distinct.
    margin_pattern = re.compile(r'^(?:(consolidated|standalone)\s+)?Achieved an EBITDA margin of\s*'
                                r'(\d+(?:\.\d+)?)\s*%\s+in\s+([QH][1-4])\s*FY\s*(\d{2,4})\.?$', re.I)
    matches = [margin_pattern.fullmatch(p) for p in points]
    values = {}
    for m in matches:
        if m:
            key = ((m[1] or '').lower(), m[3].upper(), m[4])
            values.setdefault(key, set()).add(float(m[2]))
    kept = []
    for p, m in zip(points, matches):
        if m and len(values[((m[1] or '').lower(), m[3].upper(), m[4])]) > 1:
            rejected['conflicting_unresolved_margin'] += 1
        else:
            kept.append(p)
    points = kept
    unique = unique_result_facts(points)
    if len(unique) < len(points):
        rejected['repeated_explicit_result_fact'] += len(points) - len(unique)
    return unique[:5], dict(rejected)


