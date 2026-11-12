import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.state_store import StateStore, StateIntegrityError
from scripts.actions_shift import IST, shift_seconds


def _store():
    root = tempfile.mkdtemp()
    return StateStore(
        db_path=str(Path(root) / "db.sqlite"),
        json_state_path=str(Path(root) / "state.json"),
        allow_pending=True,
    )


def _item(i, blocked="", when="2026-11-04T18:00:00+05:30"):
    return {
        "id": f"NSE_X_{i}",
        "source": "NSE",
        "company": "Example Ltd",
        "title": f"Filing {i}",
        "description": "A named officer was appointed as CFO.",
        "published_at": when,
    }, blocked


class JournalDoesNotStallTheShift(unittest.TestCase):
    def test_stage_items_evicts_terminal_work_instead_of_raising(self):
        state = _store()
        for i in range(1024):
            item, _ = _item(i)
            state.pending_items[item["id"]] = {
                "item": item, "retry_after": 0, "blocked_reason": "NO_RELIABLE_PDF_EVIDENCE",
            }
        fresh, _ = _item(2000, when="2026-11-12T17:50:00+05:30")
        state.stage_items([fresh])
        self.assertIn(fresh["id"], state.pending_items)
        self.assertLessEqual(len(state.pending_items), 1024)

    def test_fresh_start_prunes_stale_pending_items(self):
        state = _store()
        old, _ = _item(1, when="2026-11-04T18:00:00+05:30")
        state.pending_items[old["id"]] = {"item": old, "retry_after": 0, "blocked_reason": ""}
        dropped = state.prune_pending(stale_ids=[old["id"]])
        self.assertEqual(dropped, 1)
        self.assertNotIn(old["id"], state.pending_items)
        self.assertTrue(state.is_seen(old["id"]))

    def test_blocked_item_is_kept_while_journal_has_room(self):
        state = _store()
        item, _ = _item(1)
        state.stage_items([item])
        state.defer_item(item["id"], blocked_reason="PDF_SIZE_LIMIT_EXCEEDED")
        state.prune_pending()
        self.assertIn(item["id"], state.pending_items)


class PdfBacklogDoesNotAbort(unittest.TestCase):
    def test_prepare_items_evicts_blocked_instead_of_raising(self):
        from src.daemon import RealtimeDaemon
        state = _store()
        daemon = RealtimeDaemon(dry_run=True)
        daemon.state = state
        daemon._pdf_pending = {f"busy{i}": ({"id": f"busy{i}"}, MagicMock(done=lambda: False)) for i in range(32)}
        daemon._pdf_backlog = {}
        daemon._pdf_blocked = {}
        daemon._pdf_retry = {}
        daemon._pdf_outcomes = {"extracted": 0, "unsupported": 0, "errors": 0, "queue_full_deferrals": 0}
        daemon._trace = lambda *a, **k: None
        for i in range(512):
            key = f"old{i}"
            daemon._pdf_backlog[key] = {
                "id": key, "source": "NSE", "pdf_url": "https://example.com/a.pdf",
                "title": "Results", "company": "X",
            }
            daemon._pdf_blocked[key] = "NO_RELIABLE_PDF_EVIDENCE"
        incoming = [{
            "id": "new1", "source": "NSE", "pdf_url": "https://example.com/b.pdf",
            "title": "Results", "company": "Y",
        }]
        list(daemon.prepare_items(incoming, wait_pdfs=False))
        self.assertLessEqual(len(daemon._pdf_backlog), 512)
        self.assertIn("new1", daemon._pdf_backlog)


class ShiftChunks(unittest.TestCase):
    def test_weekday_chunks_are_fifty_minutes(self):
        start = datetime(2026, 11, 12, 17, 40, tzinfo=IST)
        self.assertEqual(shift_seconds(start), 50 * 60)

    def test_near_window_end_is_shorter_than_a_chunk(self):
        start = datetime(2026, 11, 12, 23, 10, tzinfo=IST)
        self.assertEqual(shift_seconds(start), 20 * 60)


class ErrorsDoNotFailTheShift(unittest.TestCase):
    def test_run_daemon_logs_errors_and_returns(self):
        from src.daemon import RealtimeDaemon
        daemon = RealtimeDaemon(dry_run=True, shift_seconds=0)
        daemon.total_errors = 85
        daemon._source_pool = None
        daemon._pdf_pool = None
        daemon.state = _store()
        daemon.run_daemon()  # must not raise
