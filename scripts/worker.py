"""Always-on worker for dedicated hosting (systemd, a VM, Fly/Render, etc.).

Runs the daemon in back-to-back shifts forever, polling every 12 s. Posting is
paced (≥20 s apart) and limited to 07:00–23:00 IST by the daemon itself.

    python scripts/worker.py --dry-run          # no Telegram, prints posts
    python scripts/worker.py                    # needs TELEGRAM_BOT_TOKEN + TELEGRAM_CHANNEL_ID
"""
import argparse
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.daemon import RealtimeDaemon  # noqa: E402
from src.state_store import StateIntegrityError  # noqa: E402

logger = logging.getLogger("Worker")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--shift", type=int, default=3600, help="seconds per shift before state is checkpointed")
    a = ap.parse_args()
    daemon = RealtimeDaemon(dry_run=a.dry_run, shift_seconds=a.shift)
    while True:
        daemon.start_time = time.time()
        daemon.total_errors = 0
        try:
            daemon.run_daemon()
        except StateIntegrityError:
            raise          # uncertain delivery: stop and let a human reconcile
        except RuntimeError as e:
            logger.error("Shift ended with errors: %s", e)   # keep running; errors are in the log
        time.sleep(1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    main()
