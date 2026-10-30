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
    r'\b(?:we (?:did not review|have reviewed|have audited|have examined)|not been reviewed|not reviewed by|'
    r'interim financial information|as considered in the statement|independent auditors?|'
    r'cash flow from operating activities|key monitorable|successfully listed|'
    r'completed \d+ years|net profit cross(?:ed|ing)?|brief (?:history|profile)|'
    r'guidance|guided|expect(?:ed|s)?|forecast|target(?:ed|s)?|CAGR|order book|'
    r'adjusted PAT excludes|products that have been developed)\b', re.I)
RELATION = re.compile(r'\b(?:was|were|stood|grew|growing|growth|increas\w*|rose|risen|'
                      r'declin\w*|decreas\w*|fell|report\w*|record\w*|up|down|expanded|'
                      r'expands|improved|doubled|at|of)\b', re.I)
EXPLICIT_TABLE = re.compile(r'^(?:Consolidated|Standalone) [^:]+: INR [-\d(].*; (?:Quarter|Half year|Year) ended ', re.I)


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
        elif re.search(r'\b(?:grows|following|expansion|supported by healthy|and EBITDA|lower|higher)\s*[.:]?$|[-–]\s*$', point, re.I):
            rejected['incomplete_fragment'] += 1
        elif not EXPLICIT_TABLE.search(point) and (not RELATION.search(point) or re.search(
                r'(?<![\w.])\(?\d[\d,.]*\)?%?\s+\(?\d[\d,.]*\)?%?\s+\(?\d[\d,.]*\)?%?', point)):
            rejected['unlabelled_numeric_row'] += 1
        elif point[:1].islower():
            rejected['leading_fragment'] += 1
        else:
            points.append(point)
    return points[:5], dict(rejected)
