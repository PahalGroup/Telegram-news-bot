"""Inspect or reconcile a pending Telegram delivery after manual channel verification.
Inspection is the default. Reconciliation does not contact Telegram or send messages.
"""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.state_store import StateStore

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db")
    parser.add_argument("--snapshot")
    parser.add_argument("--item-id")
    parser.add_argument("--outcome", choices=["delivered", "not-delivered"])
    parser.add_argument("--message-id", type=int)
    parser.add_argument("--confirm-reviewed", action="store_true")
    args = parser.parse_args()
    if args.outcome and (not args.item_id or not args.confirm_reviewed):
        parser.error("Reconciliation requires --item-id and --confirm-reviewed after checking the channel")
    state = StateStore(args.db, args.snapshot, allow_pending=True)
    if args.outcome:
        state.reconcile_delivery(args.item_id, args.outcome == "delivered", args.message_id)
    print(json.dumps({"pending_deliveries": state.pending_deliveries}, indent=2))

if __name__ == "__main__":
    main()
