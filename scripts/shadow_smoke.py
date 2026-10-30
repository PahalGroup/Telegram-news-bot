"""Bounded end-to-end smoke run. Telegram is replaced with a rejecting stub."""
import argparse
import contextlib
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.daemon import RealtimeDaemon
from src.pipeline_observer import PipelineObserver

class NoDelivery:
    is_live = False
    attempts = 0
    def send_message(self, *args, **kwargs):
        self.attempts += 1
        raise AssertionError("Shadow run attempted Telegram delivery")

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--duration', type=int, default=90)
    parser.add_argument('--output-dir', default='.')
    args = parser.parse_args()
    if not 1 <= args.duration <= 18000:
        parser.error('duration must be between 1 and 18000 seconds')
    output_dir = Path(args.output_dir)
    observer = PipelineObserver(output_dir)
    dispatcher = NoDelivery()
    records = []
    with patch("src.daemon.TelegramDispatcher", return_value=dispatcher):
        daemon = RealtimeDaemon(dry_run=True, shift_seconds=args.duration)
    daemon.observer = observer
    failure = None
    try:
        with (output_dir / "shadow-posts.txt").open("w", encoding="utf-8") as output, contextlib.redirect_stdout(output):
            started = time.monotonic()
            while time.monotonic() - started < args.duration:
                cycle_started = time.monotonic()
                count = daemon.process_cycle()
                records.append({
                    "cycle": daemon.cycle_count, "ready_posts": count,
                    "processing_seconds": round(time.monotonic() - cycle_started, 3),
                    "pending_sources": len(getattr(daemon, "_source_pending", {})),
                    "pending_pdfs": len(getattr(daemon, "_pdf_pending", {})),
                    "errors": daemon.total_errors,
                })
                time.sleep(min(12, max(0, args.duration - (time.monotonic() - started))))
            # Drain already queued work; this does not submit another network poll.
            if getattr(daemon, "_pdf_pending", None):
                from concurrent.futures import wait
                wait([f for _, f in daemon._pdf_pending.values()], timeout=60)
            with patch.object(daemon, "poll_all_sources", return_value=[]):
                count = daemon.process_cycle()
                records.append({"cycle": daemon.cycle_count, "ready_posts": count, "drain": True})
    except BaseException as exc:
        failure = type(exc).__name__
        raise
    finally:
        report = {
            "status": "failed" if failure else ("degraded" if daemon.total_errors else "completed"),
            "fatal_error_type": failure,
            "telegram_attempts": dispatcher.attempts,
            "ready_posts": daemon.total_dispatched,
            "errors": daemon.total_errors,
            "pdf_outcomes": getattr(daemon, "_pdf_outcomes", {}),
            "pending_pdfs": len(getattr(daemon, "_pdf_pending", {})),
            "pending_sources": len(getattr(daemon, "_source_pending", {})),
            "cycles": records,
            "requested_seconds": args.duration,
            "note": "Bounded observation plus up to 60 seconds PDF drain. Ready is not accuracy, coverage, or delivery proof.",
        }
        observer.finish(**report)
        (output_dir / "shadow-report.json").write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))
        for attr in ("_source_pool", "_pdf_pool"):
            pool = getattr(daemon, attr, None)
            if pool:
                pool.shutdown(wait=True, cancel_futures=True)
        daemon._dry_state_dir.cleanup()
    return int(bool(dispatcher.attempts or daemon.total_errors))

if __name__ == "__main__":
    sys.exit(main())

