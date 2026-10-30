"""
Main Production Entrypoint (main.py)
Unifies GitHub Actions runner and local execution directly into the calibrated src.daemon engine.
"""

import sys
import argparse

# Ensure UTF-8 output on all platforms
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

from src.daemon import RealtimeDaemon

def main():
    parser = argparse.ArgumentParser(description="Indian Market Wire Production Engine")
    parser.add_argument("--live", action="store_true", help="Run live daemon shift")
    parser.add_argument("--once", action="store_true", help="Execute single polling cycle and exit")
    parser.add_argument("--dry-run", action="store_true", help="Evaluate and format without publishing")
    parser.add_argument("--duration", type=int, default=3240, help="Shift duration in seconds (default: 54 min = 3240s)")
    args, unknown = parser.parse_known_args()

    # If invoked by GitHub Actions without arguments, run the continuous daemon shift
    daemon = RealtimeDaemon(dry_run=args.dry_run, shift_seconds=args.duration)
    if args.once:
        count = daemon.process_cycle(wait_sources=True)
        daemon.state.save_state(sync_git=False)
        print(f"[✓] Single cycle complete. Dispatched {count} announcements.")
        if daemon.total_errors:
            raise RuntimeError("Single pass had errors; inspect the run log.")
    else:
        daemon.run_daemon()

if __name__ == "__main__":
    main()

