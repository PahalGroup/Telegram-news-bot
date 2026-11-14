"""Shared post chrome: dash bullets, a blank line after the headline, Filing link.

Layout is inspired by the reference channel's look. Wording stays our own;
judgment tags (GOOD/WEAK) and their sentences are not used.
"""
import html
import re
from urllib.parse import urlparse

BULLET = "- "
LINK_LABEL = "Filing"


def link(url) -> str:
    parsed = urlparse(str(url or ""))
    if parsed.scheme == "https" and parsed.hostname:
        return f'<a href="{html.escape(url, quote=True)}">{LINK_LABEL}</a>'
    return ""


def strip_marker(line: str) -> str:
    return re.sub(r"^[-•▪]\s+", "", (line or "").strip())


def render(header: str, bullets, tail: str = "") -> str:
    lines = [header, ""]
    for raw in bullets:
        text = strip_marker(str(raw or ""))
        if text:
            lines.append(f"{BULLET}{text}")
    if tail:
        lines.append(tail)
    return "\n".join(lines)


# --- Management bullets: who / role / effective date, in our own words ---
_HONORIFIC = r"(?:Mr|Ms|Mrs|Dr|Shri|Smt|Sri|Prof|Kum)\.?"
_NAME_TOKEN = r"[A-Z](?:\.|[A-Za-z][\w'-]*)"
_NAME = _NAME_TOKEN + r"(?:\s+" + _NAME_TOKEN + r")+"
_WHO = re.compile(
    rf"({_HONORIFIC}\s+{_NAME})|(?:(?P<bare>{_NAME})\s+(?i:as)\s+)",
)
_ROLE = re.compile(
    r"(Company Secretary(?: and Compliance Officer)?|Compliance Officer|"
    r"Chief Financial Officer|Chief Executive Officer|Chief Operating Officer|"
    r"Chief [A-Z][A-Za-z]+ Officer|"
    r"(?:Managing |Executive |Whole[- ]Time |Independent |Additional |"
    r"Non-Executive |Non-Independent )*Director|"
    r"CFO|CEO|COO)\b",
    re.I,
)
_EFFECTIVE = re.compile(
    r"(?:with effect from|w\.?e\.?f\.?|effective(?: from)?)\s+"
    r"(\d{1,2}(?:st|nd|rd|th)?\s+(?:January|February|March|April|May|June|"
    r"July|August|September|October|November|December)[a-z]*\.?\s*,?\s*\d{4}"
    r"|(?:January|February|March|April|May|June|July|August|September|"
    r"October|November|December)[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}"
    r"|\d{1,2}[./-]\d{1,2}[./-]\d{2,4})",
    re.I,
)
_ACTION = (
    (re.compile(r"\b(?:re-?appoint(?:ed|ment)|re-?designat)", re.I), "re-appointed as"),
    (re.compile(r"\b(?:appoint(?:ed|ment))", re.I), "appointed as"),
    (re.compile(r"\b(?:resign(?:ed|ation)|resigns)\b", re.I), "resigned as"),
    (re.compile(r"\b(?:ceased|cessation)\b", re.I), "ceased to be"),
)


def _norm_words(text: str) -> str:
    return re.sub(r"[^a-z0-9%.]+", " ", (text or "").casefold()).strip()


def near_duplicate(a: str, b: str) -> bool:
    """True when one bullet restates the other (substring or nearly contained)."""
    na, nb = _norm_words(a), _norm_words(b)
    if not na or not nb:
        return False
    if na == nb or na in nb or nb in na:
        return True
    ta, tb = set(na.split()), set(nb.split())
    shorter, longer = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    if len(shorter) >= 6 and len(shorter & longer) / len(shorter) >= 0.8:
        return True
    return False


def shorten_management(text: str) -> str:
    """Rewrite a management sentence to 'who / role / effective date' in our words.

    Facts are taken only from the source sentence. If who, role and action
    cannot be read, the original sentence is kept unchanged.
    """
    raw = (text or "").strip()
    if not raw:
        return raw
    who = _WHO.search(raw)
    role = _ROLE.search(raw)
    verb = next((label for pat, label in _ACTION if pat.search(raw)), "")
    if not (who and role and verb):
        return raw
    name = re.sub(r"\s+", " ", (who.group(1) or who.group("bare") or "")).strip(" ,;")
    if not name or re.match(
        r"^(?:Company|Chief|Managing|Executive|Independent|Additional|"
        r"Non-Executive|Director|CFO|CEO|Remuneration|Committee)\b",
        name, re.I,
    ):
        return raw
    post = f"{name} {verb} {role.group(1).strip()}"
    dated = _EFFECTIVE.search(raw)
    if dated:
        post += f", effective {dated.group(1).strip()}"
    return post


def dedupe_bullets(points):
    """Drop near-duplicate bullets, keeping the shorter wording."""
    kept = []
    for raw in points:
        text = str(raw or "").strip()
        if not text:
            continue
        dup_at = next((i for i, k in enumerate(kept) if near_duplicate(text, k)), None)
        if dup_at is None:
            kept.append(text)
        elif len(text) < len(kept[dup_at]):
            kept[dup_at] = text
    return kept

