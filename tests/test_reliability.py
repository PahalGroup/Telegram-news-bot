"""Offline regressions: never contact Telegram, exchanges, or model APIs."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.daemon import RealtimeDaemon
from src.filter_engine import FilterEngine
from src.state_store import StateStore
from telegram_dispatcher import TelegramDispatcher


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.state = StateStore(str(root / "wire.db"), str(root / "state.json"))

    def daemon(self, delivery=123):
        daemon = RealtimeDaemon.__new__(RealtimeDaemon)
        daemon.dry_run = False
        daemon.state = self.state
        daemon.dispatcher = MagicMock()
        daemon.dispatcher.send_message.return_value = delivery
        daemon.filter_engine = MagicMock()
        daemon.filter_engine.normalize_entity_name.return_value = "EXAMPLE"
        daemon.filter_engine.evaluate_stage_1.return_value = (True, "", {
            "company": "EXAMPLE", "category": "ORDER_WIN", "fingerprint": "example-event",
        })
        daemon.filter_engine.evaluate_stage_2.return_value = {
            "publish": True, "materiality_score": 90, "category": "ORDER_WIN",
            "headline": "EXAMPLE – CONTRACT AWARD",
            "summary_points": ["Contract value: ₹455.85 crore", "Completion within 24 months"],
        }
        daemon.poll_all_sources = MagicMock(return_value=[{
            "id": "event-1", "source": "BSE", "title": "Example wins contract",
        }])
        daemon.check_fii_dii_routine = MagicMock()
        daemon.cycle_count = daemon.total_dispatched = daemon.total_errors = 0
        return daemon

    def test_failed_delivery_remains_retryable(self):
        daemon = self.daemon(False)
        self.assertEqual(daemon.process_cycle(), 0)
        self.assertFalse(self.state.is_seen("event-1"))
        self.assertEqual(self.state.published_records, [])
        self.assertEqual(self.state.event_cooldowns, {})
        daemon.dispatcher.send_message.return_value = 321
        self.assertEqual(daemon.process_cycle(), 1)
        self.assertTrue(self.state.is_seen("event-1"))

    def test_delivery_exception_stops_for_reconciliation(self):
        daemon = self.daemon()
        from src.state_store import StateIntegrityError
        daemon.dispatcher.send_message.side_effect = RuntimeError("delivery outcome unknown")
        with self.assertRaises(StateIntegrityError):
            daemon.process_cycle()
        self.assertFalse(self.state.is_seen("event-1"))
        self.assertIn("event-1", self.state.pending_deliveries)

    def test_success_persists_id_and_cooldown_together(self):
        daemon = self.daemon()
        self.assertEqual(daemon.process_cycle(), 1)
        payload = json.loads(Path(self.state.json_state_path).read_text())
        self.assertIn("event-1", payload["seen_ids"])
        self.assertIn("EXAMPLE:ORDER_WIN", payload["event_cooldowns"])
        self.assertEqual(daemon.process_cycle(), 0)
        daemon.dispatcher.send_message.assert_called_once()

    def test_snapshot_keeps_all_ids_and_is_deterministic(self):
        self.state.seen_ids = {f"event-{i}" for i in range(2501)}
        self.state.semantic_hashes = {f"hash-{i}" for i in range(1101)}
        self.state.save_state()
        before = Path(self.state.json_state_path).read_bytes()
        self.state.save_state()
        self.assertEqual(before, Path(self.state.json_state_path).read_bytes())
        restored = StateStore(
            str(Path(self.tmp.name) / "fresh.db"), self.state.json_state_path
        )
        self.assertEqual(restored.seen_ids, self.state.seen_ids)
        self.assertEqual(restored.semantic_hashes, self.state.semantic_hashes)

    def test_failed_atomic_replace_preserves_previous_snapshot(self):
        self.state.seen_ids.add("old")
        self.state.save_state()
        original = Path(self.state.json_state_path).read_bytes()
        self.state.seen_ids.add("new")
        with patch("src.state_store.os.replace", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                self.state.save_state()
        self.assertEqual(original, Path(self.state.json_state_path).read_bytes())
        self.assertEqual(list(Path(self.tmp.name).glob(".state-*.json")), [])

    def test_live_mode_rejects_missing_credentials(self):
        with patch("src.daemon.TelegramDispatcher") as dispatcher:
            dispatcher.return_value.is_live = False
            with self.assertRaisesRegex(RuntimeError, "Live mode requires"):
                RealtimeDaemon()

    def test_dry_run_uses_private_state_and_does_not_dispatch(self):
        with patch("src.daemon.TelegramDispatcher") as dispatcher:
            dispatcher.return_value.is_live = False
            daemon = RealtimeDaemon(dry_run=True)
            self.addCleanup(daemon._dry_state_dir.cleanup)
            self.assertTrue(daemon.state.json_state_path.startswith(daemon._dry_state_dir.name))
            daemon.poll_all_sources = lambda: []
            daemon.check_fii_dii_routine = lambda: None
            daemon.process_cycle()
            dispatcher.return_value.send_message.assert_not_called()

    def test_unconfigured_dispatcher_never_claims_delivery(self):
        dispatcher = TelegramDispatcher(bot_token="", channel_id="")
        self.addCleanup(dispatcher.session.close)
        with patch.object(dispatcher, "_log_locally"), patch("builtins.print"):
            self.assertIs(dispatcher.send_message("test"), False)

    def flows_daemon(self, rows, delivery=True):
        daemon = self.daemon(delivery)
        del daemon.check_fii_dii_routine
        daemon.nse = MagicMock()
        daemon.nse.fetch_fii_dii.return_value = rows
        daemon.fii_posted_today = False
        return daemon

    def call_flows(self, daemon):
        with patch("src.daemon.datetime") as clock:
            clock.now.return_value = datetime(2026, 10, 26, 13, 0, tzinfo=timezone.utc)
            daemon.check_fii_dii_routine()

    def flow_rows(self, date="26-OCT-2026"):
        return [
            {"date": date, "category": "FII/FPI", "netValue": "-1,708.48"},
            {"date": date, "category": "DII", "netValue": "3,045.02"},
        ]

    def test_stale_flows_are_not_published(self):
        daemon = self.flows_daemon(self.flow_rows("23-OCT-2026"))
        self.call_flows(daemon)
        daemon.dispatcher.send_message.assert_not_called()

    def test_missing_or_invalid_flow_is_not_zero_filled(self):
        for bad in ["", "NaN", "invalid", "Infinity"]:
            with self.subTest(bad=bad):
                rows = self.flow_rows()
                rows[1]["netValue"] = bad
                daemon = self.flows_daemon(rows)
                self.call_flows(daemon)
                daemon.dispatcher.send_message.assert_not_called()

    def test_failed_flows_delivery_does_not_consume_date(self):
        daemon = self.flows_daemon(self.flow_rows(), False)
        self.call_flows(daemon)
        self.assertFalse(self.state.is_seen("FII_DII_26-OCT-2026"))
        daemon.dispatcher.send_message.return_value = 321
        self.call_flows(daemon)
        self.assertTrue(self.state.is_seen("FII_DII_26-OCT-2026"))
        message = daemon.dispatcher.send_message.call_args.args[0]
        self.assertIn("1,336.54", message)

    def test_broker_rating_alone_is_rejected(self):
        engine = FilterEngine(config_path=str(Path(self.tmp.name) / "absent.json"))
        candidate = {
            "title": "Citi on RBL Bank: BUY", "description": "",
            "category": "RESEARCH_REPORT", "company": "RBL BANK",
            "broker_info": {"house": "CITI", "stock": "RBL BANK", "rating": "BUY", "target": ""},
        }
        self.assertFalse(engine.evaluate_stage_2(candidate)["publish"])
        candidate["broker_info"]["target"] = "₹455.85"
        result = engine.evaluate_stage_2(candidate)
        self.assertTrue(result["publish"])
        self.assertEqual(result["broker_meta"]["target"], "₹455.85")

    def test_screener_widgets_are_rejected(self):
        engine = FilterEngine(config_path=str(Path(self.tmp.name) / "absent.json"))
        for label in ["gainers", "losers"]:
            passed, _, _ = engine.evaluate_stage_1({
                "source": "LIVEBLOG",
                "title": f"NIFTY 50 stocks among top {label} in today's trade",
                "description": "",
            })
            self.assertFalse(passed)

    def test_shutdown_saves_state_on_interrupt(self):
        daemon = self.daemon()
        daemon.shift_seconds = 10
        daemon.start_time = __import__("time").time()
        daemon.process_cycle = MagicMock(side_effect=KeyboardInterrupt)
        with patch.object(daemon.state, "save_state") as save:
            with self.assertRaises(KeyboardInterrupt):
                daemon.run_daemon()
            save.assert_called_once_with(sync_git=False)


if __name__ == "__main__":
    unittest.main()
