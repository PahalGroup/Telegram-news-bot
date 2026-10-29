"""Bounded end-to-end smoke run. Telegram is replaced with a rejecting stub."""
import contextlib
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.daemon import RealtimeDaemon

class NoDelivery:
    is_live = False
    attempts = 0
    def send_message(self, *args, **kwargs):
        self.attempts += 1
        raise AssertionError("Shadow run attempted Telegram delivery")

def main():
    dispatcher = NoDelivery()
    records = []
    with patch("src.daemon.TelegramDispatcher", return_value=dispatcher):
        daemon = RealtimeDaemon(dry_run=True, shift_seconds=90)
    try:
        with Path("shadow-posts.txt").open("w", encoding="utf-8") as output, contextlib.redirect_stdout(output):
            started = time.monotonic()
            while time.monotonic() - started < 90:
                cycle_started = time.monotonic()
                count = daemon.process_cycle()
                records.append({
                    "cycle": daemon.cycle_count, "ready_posts": count,
                    "processing_seconds": round(time.monotonic() - cycle_started, 3),
                    "pending_sources": len(getattr(daemon, "_source_pending", {})),
                    "pending_pdfs": len(getattr(daemon, "_pdf_pending", {})),
                    "errors": daemon.total_errors,
                })
                time.sleep(min(12, max(0, 90 - (time.monotonic() - started))))
            # Drain already queued work; this does not submit another network poll.
            if getattr(daemon, "_pdf_pending", None):
                from concurrent.futures import wait
                wait([f for _, f in daemon._pdf_pending.values()], timeout=60)
            with patch.object(daemon, "poll_all_sources", return_value=[]):
                count = daemon.process_cycle()
                records.append({"cycle": daemon.cycle_count, "ready_posts": count, "drain": True})
        report = {
            "status": "degraded" if daemon.total_errors else "completed",
            "telegram_attempts": dispatcher.attempts,
            "ready_posts": daemon.total_dispatched,
            "errors": daemon.total_errors,
            "pending_pdfs": len(getattr(daemon, "_pdf_pending", {})),
            "cycles": records,
            "note": "90-second smoke only. Ready-post counts are not accuracy, coverage, or delivery proof.",
        }
        Path("shadow-report.json").write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))
        return int(bool(dispatcher.attempts or daemon.total_errors))
    finally:
        for attr in ("_source_pool", "_pdf_pool"):
            pool = getattr(daemon, attr, None)
            if pool:
                pool.shutdown(wait=True, cancel_futures=True)
        daemon._dry_state_dir.cleanup()

if __name__ == "__main__":
    sys.exit(main())
