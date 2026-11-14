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
