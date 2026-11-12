"""Queue the next market-wire chunk if we are still inside the posting window.

The current job is still running when this script runs, so it must only
dispatch (which queues behind the concurrency group) and never cancel.
"""
import os
import subprocess
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.actions_shift import IST, shift_seconds

WORKFLOW = "market-wire.yml"


def main():
    if shift_seconds(datetime.now(IST)) <= 0:
        print("outside 07:00-23:30 IST; not chaining")
        return 0
    ref = os.environ.get("CHAIN_REF") or os.environ.get("GITHUB_REF_NAME") or "main"
    cmd = ["gh", "workflow", "run", WORKFLOW, "--ref", ref,
           "-f", "max_minutes=0", "-f", "dry_run=false"]
    print("chaining next live chunk on", ref)
    result = subprocess.run(cmd, check=False)
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
