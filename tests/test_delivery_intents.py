import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from src.state_store import StateStore, StateIntegrityError
from telegram_dispatcher import TelegramDispatcher, DeliveryUncertain

class DeliveryIntentTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = str(Path(tmp.name) / "wire.db")
        self.snapshot = str(Path(tmp.name) / "state.json")
        self.state = StateStore(self.db, self.snapshot)

    def test_intent_is_durable_before_request_and_blocks_restart(self):
        self.state.begin_delivery("event", "post-hash")
        self.assertIn("event", json.loads(Path(self.snapshot).read_text())["pending_deliveries"])
        with self.assertRaises(StateIntegrityError):
            StateStore(self.db, self.snapshot)

    def test_success_clears_intent_but_keeps_dedup(self):
        self.state.begin_delivery("event", "post-hash")
        self.state.mark_seen("event", fingerprint="post-hash")
        self.state.record_published("event", "Example", "ORDER_WIN")
        self.state.complete_delivery("event")
        restarted = StateStore(self.db, self.snapshot)
        self.assertTrue(restarted.is_seen("new-id", "post-hash"))
        self.assertEqual(restarted.pending_deliveries, {})

    def test_confirmed_rejection_leaves_item_retryable(self):
        self.state.begin_delivery("event", "post-hash")
        self.state.complete_delivery("event")
        restarted = StateStore(self.db, self.snapshot)
        self.assertFalse(restarted.is_seen("event", "post-hash"))

    def test_reconciliation_requires_verified_message_id(self):
        self.state.begin_delivery("event", "post-hash")
        operator = StateStore(self.db, self.snapshot, allow_pending=True)
        with self.assertRaises(ValueError):
            operator.reconcile_delivery("event", delivered=True)
        operator.reconcile_delivery("event", delivered=True, message_id=123)
        self.assertTrue(StateStore(self.db, self.snapshot).is_seen("event", "post-hash"))

    def test_timeout_does_not_trigger_three_network_attempts(self):
        dispatcher = TelegramDispatcher(bot_token="test", channel_id="test")
        self.addCleanup(dispatcher.session.close)
        session = MagicMock()
        session.post.side_effect = TimeoutError("response timed out")
        with patch.object(dispatcher, "session", session), patch.object(dispatcher, "_log_locally"):
            with self.assertRaises(DeliveryUncertain):
                dispatcher.send_message("Example announcement")
        self.assertEqual(session.post.call_count, 1)

    def test_malformed_success_is_not_delivery_confirmation(self):
        dispatcher = TelegramDispatcher(bot_token="test", channel_id="test")
        self.addCleanup(dispatcher.session.close)
        session = MagicMock()
        session.post.return_value.json.return_value = {"ok": True, "result": {}}
        with patch.object(dispatcher, "session", session), patch.object(dispatcher, "_log_locally"):
            with self.assertRaises(DeliveryUncertain):
                dispatcher.send_message("Example announcement")
        self.assertEqual(session.post.call_count, 1)
