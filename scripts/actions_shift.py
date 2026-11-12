"""One GitHub Actions shift of the market wire.

Three scheduled runs a day (07:00, 13:00, 19:00 IST) chain into one 07:00–23:30 IST
window. Each shift runs until the next slot starts (and never past 23:30 IST or the
6 h job limit), polling every POLL_INTERVAL seconds (15). State lives in data/ and
state/, which the workflow restores from and saves to the Actions cache, so a later
shift never reposts what an earlier one sent. Quiet hours are enforced here (no shift
starts outside the window) and in the daemon outbox.

    python scripts/actions_shift.py --dry-run
"""
import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

IST = timezone(timedelta(hours=5, minutes=30))
SLOTS = (7, 13, 19)          # shift starts, IST
WINDOW_END = (23, 30)        # last post time, IST
MAX_SHIFT = 50 * 60          # short chunks: save state, chain the next, survive runner drops
HANDOFF = 120                # end this many seconds before the next shift's slot


def shift_seconds(now: datetime) -> int:
    """Seconds this shift should run, or 0 outside 07:00–23:30 IST."""
    now = now.astimezone(IST)
    close = now.replace(hour=WINDOW_END[0], minute=WINDOW_END[1], second=0, microsecond=0)
    if not (now.hour >= SLOTS[0] and now < close):
        return 0
    ends = [now.replace(hour=h, minute=0, second=0, microsecond=0) for h in SLOTS[1:]] + [close]
    end = next(e for e in ends if e > now)
    if end != close:
        end -= timedelta(seconds=HANDOFF)
    return max(0, min(int((end - now).total_seconds()), MAX_SHIFT))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-minutes", type=int, default=0, help="cap this shift (manual test runs)")
    a = ap.parse_args()
    secs = shift_seconds(datetime.now(IST))
    if a.max_minutes:
        secs = min(secs, a.max_minutes * 60)
    if secs <= 0:
        logging.info("Outside 07:00–23:30 IST; nothing to do.")
        return
    _log_bot_role()
    from src.daemon import RealtimeDaemon
    logging.info("Shift of %s s, poll %s s, dry_run=%s", secs, os.getenv("POLL_INTERVAL", "15"), a.dry_run)
    RealtimeDaemon(dry_run=a.dry_run, shift_seconds=secs).run_daemon()


def _log_bot_role():
    """Log whether the bot can post, without printing the token or request URLs."""
    import json, urllib.parse, urllib.request
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or ""
    chat = os.environ.get("TELEGRAM_CHANNEL_ID") or ""
    if not token or not chat:
        logging.warning("Telegram secrets are not set; live posts will fail")
        return
    try:
        def api(method, **params):
            body = urllib.parse.urlencode(params).encode() if params else None
            req = urllib.request.Request(
                "https://api.telegram.org/bot%s/%s" % (token, method), data=body, method="POST")
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.load(resp)
        me = api("getMe")["result"]
        status = api("getChatMember", chat_id=chat, user_id=me["id"])["result"]["status"]
        logging.info("Bot @%s channel role: %s", me.get("username"), status)
        if status not in ("administrator", "creator"):
            logging.error("Bot is not a channel admin; posts will be rejected")
    except Exception as exc:
        # Never log the exception body: urllib errors can include the request URL.
        logging.error("Could not verify bot channel role (%s)", type(exc).__name__)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    main()
