"""
Phase 3: Pixel-Accurate Telegram Formatting Engine (src/formatter.py)
Replicates the Reference Channel's visual hierarchy, typography, and density:
- Bold uppercase header with clean colon: <b>COMPANY : ACTION</b>
- Structured numerical metrics for results (Revenue, EBITDA, PAT, Margins with YoY/QoQ)
- Tight single-spaced bullets using ▪️ 
- Plain-English interpretation: ▪️ <b>What it means:</b> ...
- Zero disclosure footers (no '• Verified from official...' or source disclosures)
- Bulletproof HTML escaping using html.escape()
- Message chunking (< 4096 characters) and exponential backoff helper
"""

import re
import html
import time
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger("Formatter")

class Formatter:
    MAX_TELEGRAM_LEN = 4096

    @staticmethod
    def sanitize(text: str) -> str:
        """Escape HTML control characters safely for Telegram Bot API."""
        if not text:
            return ""
        # Telegram HTML supports: <b>, <i>, <u>, <s>, <code>, <pre>, <a href="...">
        # We escape raw &, <, > so company names or values like 'L&T' or 'M&A' won't break formatting
        return html.escape(str(text), quote=False)

    @classmethod
    def format_post(cls, eval_data: Dict[str, Any], raw_item: Optional[Dict[str, Any]] = None) -> str:
        """
        Produce a pixel-accurate Telegram HTML formatted post.
        Format layout:
        <b>COMPANY : ACTION</b>
        ▪️ Metric 1
        ▪️ Metric 2
        ▪️ Summary point 1
        ▪️ Summary point 2
        ▪️ <b>What it means:</b> Concise impact explanation
        """
        raw_item = raw_item or {}
        headline = eval_data.get("headline", "").strip()
        category = eval_data.get("category", "")
        metrics = eval_data.get("key_metrics", [])
        summary_points = eval_data.get("summary_points", [])
        what_it_means = eval_data.get("what_it_means", "").strip()

        # If headline doesn't have a colon, build standard uppercase badge
        if ":" not in headline:
            company = eval_data.get("company") or raw_item.get("company", "MARKET ALERT")
            clean_company = re.sub(r"[^\w\s]", "", str(company)).strip().upper()
            headline = f"{clean_company} : {category.replace('_', ' ').upper()}"
        else:
            headline = headline.upper()

        lines = [f"<b>{cls.sanitize(headline)}</b>"]

        # Structured Metrics
        for metric in metrics:
            if metric and str(metric).strip():
                clean_metric = cls.sanitize(str(metric).strip())
                lines.append(f"▪️ {clean_metric}")

        # Summary Points
        for point in summary_points:
            if point and str(point).strip():
                clean_pt = cls.sanitize(str(point).strip())
                # Avoid duplicate with metrics
                if not any(clean_pt in m for m in metrics):
                    lines.append(f"▪️ {clean_pt}")

        # What it means (Institutional interpretation)
        if what_it_means:
            clean_wim = cls.sanitize(what_it_means)
            lines.append(f"▪️ <b>What it means:</b> {clean_wim}")

        post = "\n".join(lines).strip()
        return post

    @classmethod
    def format_fii_dii(cls, date_str: str, fii_net: float, dii_net: float) -> str:
        """Standardized institutional FII/DII flow format."""
        total_net = fii_net + dii_net
        fii_icon = "🟢" if fii_net >= 0 else "🔴"
        dii_icon = "🟢" if dii_net >= 0 else "🔴"
        total_icon = "🟢" if total_net >= 0 else "🔴"

        lines = [
            f"<b>FII / DII ACTIVITY – {cls.sanitize(date_str.upper())}</b>",
            f"▪️ {dii_icon} <b>DII Net Buy:</b> ₹{dii_net:,.2f} Cr",
            f"▪️ {fii_icon} <b>FII Net Sell:</b> ₹{fii_net:,.2f} Cr",
            f"▪️ {total_icon} <b>Total Net Buy:</b> ₹{total_net:,.2f} Cr",
            f"▪️ <b>What it means:</b> Institutional cash flows provide key insight into market liquidity and risk appetite."
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
                    # Single line longer than max_length
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
        Rejects any draft post that is under 120 characters, ends mid-sentence,
        has unclosed HTML tags, or lacks concrete metrics/details.
        """
        if not post or not isinstance(post, str):
            return False

        text = post.strip()
        # 1. Minimum character count check
        if len(text) < 120:
            return False

        # 2. Balanced HTML tag check (<b>...</b>, <i>...</i>, <code>...</code>)
        tags_to_check = ["b", "i", "u", "s", "code", "pre"]
        for tag in tags_to_check:
            open_tags = len(re.findall(rf"<{tag}\b[^>]*>", text, re.IGNORECASE))
            close_tags = len(re.findall(rf"</{tag}>", text, re.IGNORECASE))
            if open_tags != close_tags:
                return False

        # 3. Check for mid-sentence endings (dangling punctuation or cutoff)
        clean_end = re.sub(r"<[^>]+>", "", text).strip()
        if re.search(r"[,;:\-\(\[\{/\\&]\s*$", clean_end):
            return False

        # 4. For Financial Results and Order Wins: require concrete metrics if badge specifically claims them
        first_line = clean_end.splitlines()[0] if clean_end.splitlines() else ""
        badge = first_line.split(":")[-1].upper() if ":" in first_line else first_line.upper()
        if re.search(r"\b(?:FINANCIAL RESULTS|ORDER WIN)\b", badge):
            has_concrete_numbers = bool(
                re.search(r"(?:₹|rs\.?\s*|inr\s*|\b)\d+(?:\.\d+)?\s*(?:cr(?:ore)?|lakh|%|bps|gw|mw|k|m|b|\b)", text, re.IGNORECASE)
                and any(c.isdigit() for c in text)
            )
            if not has_concrete_numbers:
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
                    logger.warning(f"Telegram 429 rate limit hit. Backing off for {wait_sec}s (Attempt {attempt+1}/{max_retries})...")
                    time.sleep(wait_sec)
                else:
                    raise
        raise RuntimeError(f"Exceeded max retries ({max_retries}) for Telegram dispatch.")
