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


def select_result_points(title, description):
    candidates = []
    for line in description.splitlines() + [title]:
        # Embedded PDF bullet glyphs are boundaries, not a reason to combine metrics.
        for bullet in re.split(r'[•▪●✓\uf0b7]|\s+(?=Premiums on our infrastructure\b)', line):
            candidates.extend(sentences(bullet))
    points, rejected = [], Counter()
    for point in clean_points(candidates, limit=None):
        if point.count('(') != point.count(')'):
            rejected['incomplete_parenthesis'] += 1
        elif BACKGROUND.search(point):
            rejected['audit_scope_or_background'] += 1
        elif not METRIC.search(point) or not VALUE.search(point) or not metric_has_value(point):
            rejected['missing_explicit_metric_value'] += 1
        elif re.search(r'\b(?:grows|following|expansion|supported by healthy|and EBITDA|lower|higher)\s*[.:]?$|[-–]\s*$', point, re.I):
            rejected['incomplete_fragment'] += 1
        elif not EXPLICIT_TABLE.search(point) and re.search(
                r'(?<![\w.])\(?\d[\d,.]*\)?%?\s+\(?\d[\d,.]*\)?%?\s+\(?\d[\d,.]*\)?%?', point):
            rejected['unlabelled_numeric_row'] += 1
        elif point[:1].islower():
            rejected['leading_fragment'] += 1
        else:
            points.append(point)
    return points[:5], dict(rejected)

