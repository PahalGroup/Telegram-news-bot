"""
Pixel-Accurate Telegram Formatting Engine (src/formatter.py)
Replicates Reference Channel (stock_aaj_or_kal) visual layout:
- Tight single-line spacing (zero excessive blank lines)
- Bold uppercase header: <b>COMPANY – ACTION</b>
- Institutional Brokerage format:
    💡 <b>HOUSE ON STOCK</b>
    Rating: BUY | Target: ₹XXXX
    • Point 1
    • Point 2
- Clean bullet markers (• or ▪️)
- Zero disclosure footers (no '• Verified from official...' or source disclosures)
- Strict completeness validation
"""

import re
import html
import time
import logging
from urllib.parse import urlparse
from src.quality import clean, substantive, PLACEHOLDER
from typing import Dict, Any, List, Optional

logger = logging.getLogger("Formatter")

class Formatter:
    MAX_TELEGRAM_LEN = 4096

    @staticmethod
    def sanitize(text: str) -> str:
        """Escape HTML control characters safely for Telegram Bot API."""
        if not text:
            return ""
        return html.escape(str(text), quote=False)

    @classmethod
    def format_post(cls, eval_data, raw_item=None):
        data = dict(eval_data)
        points, seen = [], {clean(data.get("headline", "")).casefold().rstrip(".")}
        for point in data.get("summary_points", []):
            value = clean(point).lstrip("•-▪►* ")
            key = value.casefold().rstrip(".")
            if not value or key in seen or PLACEHOLDER.search(value):
                continue
            if re.search(r"^(?:that |of |from |and |or |/|t &|re-)", value, re.I):
                continue
            if re.search(r"\b(?:and|or|with|from|for|to|of|the|its|their|other)[.,;: ]*$", value, re.I):
                continue
            seen.add(key)
            points.append(value)
        if not points and data.get("category") not in ("POLICY_MACRO", "RESEARCH_REPORT"):
            return ""
        data["summary_points"] = points
        post = cls._format_post(data, raw_item)
        # Validate the body before appending a source; a link cannot rescue an empty post.
        if not cls.validate_completeness(post):
            return ""
        raw = raw_item or {}
        url = raw.get("source_url") or raw.get("pdf_url") or ""
        parsed = urlparse(url)
        if parsed.scheme == "https" and parsed.hostname:
            post += '\n<a href="' + html.escape(url, quote=True) + '">Source</a>'
        return post if len(post.encode("utf-16-le")) // 2 <= cls.MAX_TELEGRAM_LEN else ""

    @classmethod
    def _format_post(cls, eval_data: Dict[str, Any], raw_item: Optional[Dict[str, Any]] = None) -> str:
        """
        Produce a pixel-accurate Telegram HTML formatted post matching Reference Channel.
        """
        raw_item = raw_item or {}
        category = eval_data.get("category", "")
        headline = eval_data.get("headline", "").strip()
        broker_meta = eval_data.get("broker_meta")
        summary_points = eval_data.get("summary_points", [])

        # 1. Brokerage Note Format
        if category == "RESEARCH_REPORT" and broker_meta:
            house = cls.sanitize(broker_meta.get("house", "ANALYST"))
            stock = cls.sanitize(broker_meta.get("stock", ""))
            rating = cls.sanitize(broker_meta.get("rating", ""))
            target = cls.sanitize(broker_meta.get("target", ""))

            lines = [f"💡 <b>{house} ON {stock}</b>"]
            meta_line_parts = []
            if rating:
                meta_line_parts.append(f"Rating: {rating}")
            if target:
                meta_line_parts.append(f"Target: {target}")
            if meta_line_parts:
                lines.append(" | ".join(meta_line_parts))

            for pt in summary_points:
                clean_pt = cls.sanitize(pt.strip())
                if clean_pt:
                    if not clean_pt.startswith(("•", "-", "▪️")):
                        lines.append(f"• {clean_pt}")
                    else:
                        lines.append(clean_pt)

            return "\n".join(lines).strip()

        # 2. Policy & Macro / Breaking News
        if category == "POLICY_MACRO":
            clean_hl = cls.sanitize(headline.upper())
            lines = [f"<b>{clean_hl}</b>"]
            for pt in summary_points:
                clean_pt = cls.sanitize(pt.strip())
                if clean_pt:
                    lines.append(clean_pt)
            return "\n".join(lines).strip()

        # 3. Financial Results Scorecard (Exact Match with Reference Channel)
        if category == "FINANCIAL_RESULTS" and any(pt.startswith(("Net Profit:", "Revenue:", "EBITDA:")) for pt in summary_points):
            clean_hl = cls.sanitize(headline.upper())
            lines = [f"<b>{clean_hl}</b>\n"]
            for pt in summary_points:
                clean_pt = re.sub(r'[,;:\-\s]+$', '', cls.sanitize(pt.strip()))
                if clean_pt:
                    lines.append(clean_pt)
            return "\n\n".join([lines[0].strip()] + lines[1:]).strip()

        # 4. Standard Corporate Filings & Results
        clean_hl = cls.sanitize(headline.upper())
        lines = [f"<b>{clean_hl}</b>"]
        for pt in summary_points:
            clean_pt = re.sub(r'[,;:\-\s]+$', '', cls.sanitize(pt.strip()))
            if clean_pt:
                if not clean_pt.startswith(("•", "-", "▪️")):
                    lines.append(f"• {clean_pt}")
                else:
                    lines.append(clean_pt)

        return "\n".join(lines).strip()

    @classmethod
    def format_fii_dii(cls, date_str: str, fii_net: float, dii_net: float) -> str:
        """Standardized institutional FII/DII flow format matching Reference Channel."""
        total_net = fii_net + dii_net
        fii_icon = "🟢" if fii_net >= 0 else "🔴"
        dii_icon = "🟢" if dii_net >= 0 else "🔴"
        total_icon = "🟢" if total_net >= 0 else "🔴"
        fii_action = "Net Buy" if fii_net >= 0 else "Net Sell"
        dii_action = "Net Buy" if dii_net >= 0 else "Net Sell"
        total_action = "Net Buy" if total_net >= 0 else "Net Sell"

        lines = [
            f"<b>FII / DII ACTIVITY – {cls.sanitize(date_str.upper())}</b>",
            f"{dii_icon} <b>DII {dii_action}:</b> ₹{abs(dii_net):,.2f} Cr",
            f"{fii_icon} <b>FII {fii_action}:</b> ₹{abs(fii_net):,.2f} Cr",
            f"{total_icon} <b>Total {total_action}:</b> ₹{abs(total_net):,.2f} Cr"
        ]
        return "\n".join(lines)

    @classmethod
    def split_message_chunks(cls, message: str, max_length: int = MAX_TELEGRAM_LEN) -> List[str]:
        """Safely split message if it exceeds Telegram's 4096 character limit."""
        if len(message) <= max_length:
            return [message]
        
        chunks = []
        lines = message.split("\n")
        current_chunk = []
        current_len = 0

        for line in lines:
            line_len = len(line) + 1
            if current_len + line_len > max_length:
                if current_chunk:
                    chunks.append("\n".join(current_chunk))
                    current_chunk = [line]
                    current_len = line_len
                else:
                    chunks.append(line[:max_length])
                    current_chunk = [line[max_length:]]
                    current_len = len(line[max_length:]) + 1
            else:
                current_chunk.append(line)
                current_len += line_len

        if current_chunk:
            chunks.append("\n".join(current_chunk))

        return chunks

    @classmethod
    def validate_completeness(cls, post: str) -> bool:
        """
        Strict Pre-Send Completeness Validator:
        Rejects any draft post that is under 40 characters, ends mid-sentence,
        or has unclosed HTML tags.
        """
        if not post or not isinstance(post, str):
            return False

        text = post.strip()
        # 1. Minimum character count check
        if len(text) < 40:
            return False

        # 2. Balanced HTML tag check (<b>...</b>, <i>...</i>, <code>...</code>)
        tags_to_check = ["b", "i", "u", "s", "code", "pre"]
        for tag in tags_to_check:
            open_tags = len(re.findall(rf"<{tag}\b[^>]*>", text, re.IGNORECASE))
            close_tags = len(re.findall(rf"</{tag}>", text, re.IGNORECASE))
            if open_tags != close_tags:
                return False

        # 3. Check for mid-sentence endings (dangling conjunctions)
        clean_end = re.sub(r"<[^>]+>", "", text).strip()
        clean_end = re.sub(r"[,;:\-]\s*$", "", clean_end).strip()
        words = clean_end.split()
        if words and words[-1].lower() in ["and", "or", "to", "with", "for", "at", "by", "of", "in", "the", "a", "an"]:
            return False
        if re.search(r"[\(\[\{/\\&]\s*$", clean_end):
            return False

        body_text = re.sub(r"<[^>]+>", "", text).strip()
        lines = [l.strip() for l in body_text.splitlines() if l.strip()]
        
        # 4. Must have at least a headline AND substantive body content (No single-line headline dumps)
        if len(lines) < 2:
            return len(body_text) <= 350 and substantive(body_text)

        # 5. Reject generic paperwork boilerplate placeholders
        boilerplate_patterns = [
            r"as per (?:the )?attachment",
            r"please refer (?:to )?(?:the )?enclosed",
            r"please find attached",
            r"disclosures? under regulation 30.*attached",
            r"enclosed file contains",
            r"approved the items mentioned in the attached",
            r"kindly take the same on (?:your )?records?",
            r"take the above on record"
        ]
        substantive_lines = []
        for l in lines[1:]: # skip headline
            if l == "Source" or PLACEHOLDER.search(l):
                continue
            if not any(re.search(pat, l, re.IGNORECASE) for pat in boilerplate_patterns):
                substantive_lines.append(l)
        if not substantive_lines:
            return False

        return True

    @classmethod
    def execute_with_retry(cls, func, *args, max_retries: int = 3, **kwargs):
        """Execute a network dispatch function with exponential backoff on 429 rate limit."""
        for attempt in range(max_retries):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                err_str = str(e).lower()
                if "429" in err_str or "too many requests" in err_str:
                    wait_sec = (attempt + 1) * 3
                    logger.warning(f"Telegram 429 rate limit hit. Backing off for {wait_sec}s...")
                    time.sleep(wait_sec)
                else:
                    raise
        raise RuntimeError(f"Exceeded max retries ({max_retries}) for Telegram dispatch.")
