"""Select explicit result values; exclude audit scope, chart labels and history.

This is a conservative prose gate, not complete period/scope reconciliation.
"""
import re
from collections import Counter
from src.quality import clean_points, sentences

METRIC = re.compile(r'\b(?:net profit|net loss|profit after tax|profit before tax|revenue|sales|ebitda|PAT|PBT)\b', re.I)
NUMBER = r'[-+~]?(?:\(?\d[\d,]*(?:\.\d+)?\)?)'
VALUE = re.compile(
    r'(?:₹|\b(?:Rs\.?|INR|USD|EUR|GBP)\s*)\s*' + NUMBER +
    r'|' + NUMBER + r'\s*(?:%|per cent\b|percent\b|crores?\b|lakhs?\b|millions?\b|billions?\b|cr\b|mn\b)', re.I)
BACKGROUND = re.compile(
    r'\b(?:we (?:did not review|have reviewed|have audited)|not been reviewed|not reviewed by|'
    r'interim financial information|as considered in the statement|independent auditors?|'
    r'cash flow from operating activities|key monitorable|successfully listed|'
    r'completed \d+ years|net profit cross(?:ed|ing)?|brief (?:history|profile))\b', re.I)


def select_result_points(title, description):
    candidates = []
    for line in description.splitlines() + [title]:
        # Embedded PDF bullet glyphs are boundaries, not a reason to combine metrics.
        for bullet in re.split(r'[•▪\uf0b7]', line):
            candidates.extend(sentences(bullet))
    points, rejected = [], Counter()
    for point in clean_points(candidates, limit=None):
        if BACKGROUND.search(point):
            rejected['audit_scope_or_background'] += 1
        elif not METRIC.search(point) or not VALUE.search(point):
            rejected['missing_explicit_metric_value'] += 1
        elif re.search(r'\b(?:grows|following|expansion|supported by healthy|and EBITDA)\s*[.:]?$|[-–]\s*$', point, re.I):
            rejected['incomplete_fragment'] += 1
        else:
            points.append(point)
    return points[:5], dict(rejected)
