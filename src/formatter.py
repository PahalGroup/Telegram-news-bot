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
    def format_post(cls, eval_data: Dict[str, Any], raw_item: Optional[Dict[str, Any]] = None) -> str:
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

        # 4. Reject generic paperwork boilerplate placeholders that contain no metrics or facts
        boilerplate_placeholders = [
            r"^\s*•?\s*as per the attachment\.?\s*$",
            r"^\s*•?\s*please refer the enclosed disclosure\.?\s*$",
            r"^\s*•?\s*please find attached (?:intimation|document)\.?\s*$",
            r"^\s*•?\s*disclosures? under regulation 30.*are being attached\.?\s*$",
            r"^\s*•?\s*the enclosed file contains the details\.?\s*$",
            r"^\s*•?\s*approved the items mentioned in the attached document\.?\s*$"
        ]
        body_text = re.sub(r"<[^>]+>", "", text).strip()
        lines = [l.strip() for l in body_text.splitlines() if l.strip()]
        if len(lines) <= 2:
            last_line = lines[-1].lower()
            if any(re.match(pat, last_line) for pat in boilerplate_placeholders):
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
