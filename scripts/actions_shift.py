"""One GitHub Actions shift of the market wire.

Three scheduled runs a day (07:00, 13:00, 19:00 IST) chain into one 07:00–23:00 IST
window. Each shift runs until the next slot starts (and never past 23:00 IST or the
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
WINDOW_END = 23              # last post time, IST
MAX_SHIFT = 350 * 60         # GitHub's job limit is 360 min; leave room to save state
HANDOFF = 120                # end this many seconds before the next shift's slot


def shift_seconds(now: datetime) -> int:
    """Seconds this shift should run, or 0 outside 07:00–23:00 IST."""
    now = now.astimezone(IST)
    if not (SLOTS[0] <= now.hour < WINDOW_END):
        return 0
    ends = [now.replace(hour=h, minute=0, second=0, microsecond=0) for h in SLOTS[1:] + (WINDOW_END,)]
    end = next(e for e in ends if e > now)
    if end.hour != WINDOW_END:
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
        logging.info("Outside 07:00–23:00 IST; nothing to do.")
        return
    from src.daemon import RealtimeDaemon
    logging.info("Shift of %s s, poll %s s, dry_run=%s", secs, os.getenv("POLL_INTERVAL", "15"), a.dry_run)
    RealtimeDaemon(dry_run=a.dry_run, shift_seconds=secs).run_daemon()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    main()
