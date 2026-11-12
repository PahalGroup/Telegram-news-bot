"""Verbatim facts from a senior-management change table (name / role / action / date)."""
import re
from src.quality import clean

HEADER = {
    "name": re.compile(r"\b(?:name|personnel|person)\b", re.I),
    "role": re.compile(r"\b(?:designation|role|position|title)\b", re.I),
    "action": re.compile(r"\b(?:appointment|cessation|resignation|re-?appointment)\b", re.I),
    "reason": re.compile(r"\breason\b", re.I),
    "date": re.compile(r"\b(?:date|effect|occurrence|w\.?e\.?f)\b", re.I),
}


def _flat(cell):
    return re.sub(r"\s+", " ", clean(cell or "")).strip()


def management_table_evidence(rows, page):
    """Join header-aligned cells in a management-change table; do not invent wording."""
    if not rows or len(rows) < 2:
        return []
    header = [_flat(c) for c in rows[0]]
    if len(header) < 2:
        return []
    cols = {}
    for i, title in enumerate(header):
        for key, pat in HEADER.items():
            if key not in cols and pat.search(title):
                cols[key] = i
    if "name" not in cols or ("role" not in cols and "action" not in cols):
        return []
    facts = []
    for row in rows[1:]:
        cells = [_flat(c) for c in row]
        if len(cells) < 2:
            continue
        name = cells[cols["name"]] if cols["name"] < len(cells) else ""
        if not re.search(r"\b(?:Mr|Ms|Mrs|Dr|Shri|Smt|Miss)\.?\b", name, re.I) and not re.search(
                r"\b[A-Z][a-z]+\s+[A-Z][a-z]+", name):
            continue
        parts = []
        for key in ("name", "role", "action", "reason", "date"):
            if key in cols and cols[key] < len(cells) and cells[cols[key]]:
                parts.append(cells[cols[key]])
        if len(parts) < 2:
            continue
        facts.append({"text": "; ".join(parts), "page": page, "method": "management_table"})
    return facts
