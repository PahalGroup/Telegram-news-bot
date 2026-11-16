"""Normalize PDF evidence and decide when the exchange description may appear in a post.

The exchange text is always kept for classify/gate. It is prepended to post
text only when it is a clean, complete sentence. Nested/non-string bullets
are flattened so join() cannot crash the cycle.
"""
from __future__ import annotations

import re


def flatten_points(points) -> list[str]:
    """Turn mixed bullets (str, list, tuple, None) into a flat list of strings."""
    out: list[str] = []
    if points is None or points is False:
        return out
    if isinstance(points, str):
        text = points.strip()
        return [text] if text else []
    if isinstance(points, (list, tuple, set)):
        for item in points:
            out.extend(flatten_points(item))
        return out
    text = str(points).strip()
    return [text] if text else []


def tidy_point(text: str) -> str:
    """Drop leftover numbered-list markers ('2.' after a sentence, leading '1.')."""
    point = re.sub(r"\s+", " ", (text or "").strip())
    point = re.sub(r"^(?:\(?\d{1,2}\)|[0-9]{1,2}\.|[a-zA-Z]\))\s+", "", point)
    point = re.sub(r"(?<=[.!?])\s+\d{1,2}\.\s*$", "", point)
    return point.strip()


def is_clean_sentence(text: str) -> bool:
    """A complete official sentence, not a mid-letter fragment or list stub."""
    point = tidy_point(text)
    if len(point) < 20:
        return False
    # Mid-sentence leftovers: "'Listing Regulations'), we wish..." / ", we wish to inform"
    if re.match(r"^['\"]", point):
        return False
    if re.match(r"^[)\],.:;]", point):
        return False
    if re.match(r"^[a-z]", point):
        return False
    if re.search(r"\bon today i\.e\.?\s*$", point, re.I):
        return False
    if re.search(r"\bi\.e\.?\s*$", point, re.I) and len(point) < 220:
        return False
    # Split after "today i.e. Saturday, November 14, 2026, have inter alia..."
    if re.match(
        r"^(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b",
        point, re.I,
    ) and re.search(r"\b(?:have|has|inter alia)\b", point, re.I):
        return False
    return True


def merge_exchange_description(original: str, bullets) -> str:
    """Description used for classify/gate: always includes the exchange text.

    Clean complete exchange sentences are prepended so they can also appear in
    the post. Fragments stay in this string for gating only; compose drops them.
    """
    points = [tidy_point(p) for p in flatten_points(bullets)]
    points = [p for p in points if p]
    original = tidy_point(original or "")
    if original and is_clean_sentence(original) and original not in points:
        points = [original] + points
    elif original and original not in points:
        # Keep for gate/classify, after the evidence, so pick_bullets prefers
        # cleaner PDF lines first. Compose will skip the fragment.
        points = points + [original]
    return "\n".join(points)
