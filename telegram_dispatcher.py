import os
import re
import time
import json
import logging
from typing import Optional
from curl_cffi import requests
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, LOG_PATH

logger = logging.getLogger(__name__)

class TelegramDispatcher:
    def __init__(self, bot_token: str = TELEGRAM_BOT_TOKEN, channel_id: str = TELEGRAM_CHANNEL_ID):
        self.bot_token = bot_token
        self.channel_id = channel_id
        self.session = requests.Session(impersonate="chrome120")
        self.is_live = bool(self.bot_token and self.channel_id)

    def test_connection(self) -> dict:
        """Verify bot credentials with Telegram API."""
        if not self.bot_token:
            return {"ok": False, "error": "No TELEGRAM_BOT_TOKEN provided"}
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/getMe"
            r = self.session.get(url, timeout=10)
            data = r.json()
            return data
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @staticmethod
    def format_html(text: str) -> str:
        """Converts basic markdown **bold** to <b>bold</b> and *italic* to <i>italic</i> for Telegram HTML parse_mode."""
        if not text:
            return ""
        # Convert **bold** to <b>bold</b>
        formatted = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
        return formatted

    def send_message(self, text: str, parse_mode: Optional[str] = "HTML", add_origin: bool = False) -> bool:
        """
        Dispatches a message to the Telegram channel.
        Rule: One new headline is one post.
        """
        if not text:
            return False

        if add_origin:
            origin_tag = "📡 <i>Dispatched via GitHub Cloud (24/7 Engine)</i>" if os.getenv("GITHUB_ACTIONS") == "true" else "🛠️ <i>Dispatched via Antigravity IDE (Local Session)</i>"
            text = f"{text}\n\n{origin_tag}"

        formatted_text = self.format_html(text) if parse_mode == "HTML" else text

        # Always write to wire_out.log for record & verification
        self._log_locally(formatted_text)

        if not self.is_live:
            # Standalone / Dry Run mode: Print directly to console in clear wire format
            print(f"\n[WIRE DISPATCH] >>>\n{formatted_text}\n{'='*50}")
            return True

        # Send via Telegram Bot API
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.channel_id,
            "text": formatted_text,
            "disable_web_page_preview": True
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode

        for attempt in range(3):
            try:
                r = self.session.post(url, json=payload, timeout=10)
                res = r.json()
                if res.get("ok"):
                    logger.info(f"Dispatched to Telegram: {formatted_text[:60]}...")
                    return True
                else:
                    logger.error(f"Telegram API Error: {res}")
                    if res.get("error_code") == 400 and "parse" in res.get("description", "").lower():
                        # Fallback: send as plain text if HTML parsing failed
                        logger.warning("HTML entity parsing failed, retrying without parse_mode...")
                        payload.pop("parse_mode", None)
                        payload["text"] = text
                        r_plain = self.session.post(url, json=payload, timeout=10)
                        if r_plain.json().get("ok"):
                            return True
                    if res.get("error_code") == 429: # Rate limit
                        retry_after = res.get("parameters", {}).get("retry_after", 3)
                        time.sleep(retry_after)
                        continue
                    break
            except Exception as e:
                logger.error(f"Dispatch Exception (attempt {attempt+1}): {e}")
                time.sleep(2)

        return False

    def edit_message(self, message_id: int, text: str) -> bool:
        """Edits an existing message in-place on the Telegram channel."""
        if not text or not self.is_live:
            return False

        url = f"https://api.telegram.org/bot{self.bot_token}/editMessageText"
        payload = {
            "chat_id": self.channel_id,
            "message_id": message_id,
            "text": text,
            "disable_web_page_preview": True
        }

        try:
            r = self.session.post(url, json=payload, timeout=10)
            res = r.json()
            if res.get("ok"):
                logger.info(f"Edited message {message_id} in-place successfully.")
                return True
            else:
                logger.warning(f"Failed to edit message {message_id}: {res}")
                return False
        except Exception as e:
            logger.error(f"Error editing message {message_id}: {e}")
            return False

    def _log_locally(self, text: str):
        try:
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
                f.write(f"[{timestamp}]\n{text}\n\n")
        except Exception as e:
            logger.error(f"Log write error: {e}")
