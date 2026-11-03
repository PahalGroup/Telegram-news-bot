"""Print the morning pack posts (no Telegram). The live daemon posts them at 07:15/08:45/09:05 IST."""
import argparse
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sources.market_cues import fetch_cues  # noqa: E402
from src import morning_pack  # noqa: E402
from src.scheduler import IST  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", choices=list(morning_pack.SLOTS), default=None)
    ap.add_argument("--records", help="JSON file of published records for Stocks in Focus")
    a = ap.parse_args()
    now = datetime.now(IST)
    cues = fetch_cues()
    records = json.load(open(a.records)) if a.records else []
    for slot in [a.slot] if a.slot else morning_pack.SLOTS:
        print(f"--- {slot} ---")
        print(morning_pack.build(slot, now, cues, records) or "(nothing to post)")


if __name__ == "__main__":
    main()
