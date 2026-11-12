"""Watchdog: if we are in the posting window and no shift is running, start one."""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.actions_shift import IST, shift_seconds

WORKFLOW = "market-wire.yml"
STALE_QUEUED_SECONDS = 15 * 60


def gh(*args, check=True):
    result = subprocess.run(["gh", *args], check=False, capture_output=True, text=True)
    if check and result.returncode != 0:
        sys.stderr.write(result.stderr)
        raise SystemExit(f"gh {' '.join(args)} failed ({result.returncode})")
    return result


def main():
    if shift_seconds(datetime.now(IST)) <= 0:
        print("outside 07:00-23:30 IST; watchdog idle")
        return 0
    raw = gh("run", "list", "-w", WORKFLOW, "-L", "20",
             "--json", "databaseId,status,createdAt,url,event").stdout
    runs = json.loads(raw)
    live = [r for r in runs if r["status"] == "in_progress"]
    if live:
        print("shift already running", live[0]["url"])
        return 0
    now = datetime.now(timezone.utc)
    for run in runs:
        if run["status"] not in ("queued", "waiting", "pending"):
            continue
        created = datetime.fromisoformat(run["createdAt"].replace("Z", "+00:00"))
        age = (now - created).total_seconds()
        if age <= STALE_QUEUED_SECONDS:
            print("shift already queued", run["url"])
            return 0
        print("stale queued run", run["url"], "age_s=", int(age), "; will start a new one")
        gh("run", "cancel", str(run["databaseId"]), check=False)
        gh("api", "-X", "POST",
           f"repos/{os.environ.get('GITHUB_REPOSITORY', 'PahalGroup/Telegram-news-bot')}/actions/runs/{run['databaseId']}/force-cancel",
           check=False)
    ref = os.environ.get("CHAIN_REF") or os.environ.get("GITHUB_REF_NAME") or "main"
    print("dispatching live shift on", ref)
    result = gh("workflow", "run", WORKFLOW, "--ref", ref,
                "-f", "max_minutes=0", "-f", "dry_run=false")
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
