"""Adversarial regressions: synthetic inputs, no Telegram or network access."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
import pymupdf
from src.state_store import StateStore
from src.event_evidence import evaluate_event
from src.filter_engine import FilterEngine
from sources.annexure_parser import AnnexureParser

class StateIntegrityTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = str(Path(tmp.name) / "wire.db")
        self.snapshot = Path(tmp.name) / "state.json"

    def test_corrupt_snapshot_blocks_startup_and_is_preserved(self):
        self.snapshot.write_text('{"seen_ids":')
        before = self.snapshot.read_bytes()
        with self.assertRaises(RuntimeError):
            StateStore(self.db, str(self.snapshot))
        self.assertEqual(before, self.snapshot.read_bytes())

    def test_invalid_snapshot_schema_blocks_startup(self):
        for data in [{}, {"seen_ids": "abc", "semantic_hashes": []},
                     {"seen_ids": [], "semantic_hashes": [None]},
                     {"seen_ids": [], "semantic_hashes": [], "event_cooldowns": {"x": float("nan")}}]:
            with self.subTest(data=data):
                self.snapshot.write_text(json.dumps(data))
                with self.assertRaises(RuntimeError):
                    StateStore(self.db, str(self.snapshot))

    def test_corrupt_database_blocks_startup(self):
        Path(self.db).write_bytes(b"not a sqlite database")
        with self.assertRaises(RuntimeError):
            StateStore(self.db, str(self.snapshot))

    def test_failed_ledger_write_is_visible(self):
        state = StateStore(self.db, str(self.snapshot))
        with patch("src.state_store.sqlite3.connect", side_effect=sqlite3.OperationalError("disk full")):
            with self.assertRaises(RuntimeError):
                state.mark_seen("delivered-event", fingerprint="claim")

class EvidenceBoundaryTests(unittest.TestCase):
    def test_late_pdf_fact_survives_download_and_selection(self):
        fact = "Example received an order worth INR 250 crore from Utility Limited."
        with pymupdf.open() as doc:
            page = doc.new_page()
            for i in range(14):
                page.insert_text((40, 40 + i * 38),
                                 f"The company reviews investment policy for division number {i}.", fontsize=9)
            page.insert_text((40, 620), fact, fontsize=9)
            pdf = doc.tobytes()
        session = MagicMock()
        session.get.return_value.status_code = 200
        session.get.return_value.content = pdf
        _, bullets = AnnexureParser.extract_annexure_bullets("https://example.com/original.pdf", session)
        result = evaluate_event("ORDER_WIN", "EXAMPLE", "Receipt of order", "\n".join(bullets))
        self.assertIn(fact, result["summary_points"])

    def test_results_filter_precedes_bullet_limit(self):
        lines = [f"The board approved investment policy for division number {i}." for i in range(9)]
        fact = "Consolidated net profit was INR 250 crore for the quarter ended September 2026."
        result = FilterEngine().evaluate_stage_2({
            "company": "Example", "category": "FINANCIAL_RESULTS", "title": "Quarterly results",
            "description": "\n".join(lines + [fact])})
        self.assertIn(fact, result["summary_points"])

    def test_uncertain_or_negated_capacity_is_withheld(self):
        for text in [
            "The 150 MW project has never been commissioned by Example.",
            "The 150 MW project will be fully commissioned after all regulatory permissions are obtained.",
            "The 150 MW project may be commissioned next year.",
        ]:
            with self.subTest(text=text):
                self.assertFalse(evaluate_event("CAPACITY_UPDATE", "EXAMPLE", "", text)["publish"])

    def test_distant_negation_cannot_become_order_win(self):
        text = ("Example has not, despite lengthy negotiations and discussions with the customer, "
                "received an order worth INR 250 crore.")
        self.assertFalse(evaluate_event("ORDER_WIN", "EXAMPLE", "", text)["publish"])

    def test_auction_cannot_invent_crore_unit(self):
        result = FilterEngine().evaluate_stage_2({
            "company": "RBI", "source": "RBI", "category": "POLICY_MACRO",
            "title": "RBI variable rate repo auction", "description": "Notified amount: INR 500 million."})
        text = "\n".join(result["summary_points"])
        self.assertNotIn("Crore", text)
        self.assertIn("500 million", text)

class EventQualificationTests(unittest.TestCase):
    def test_order_reference_and_future_delivery_do_not_negate_award(self):
        text = ("Example received an order vide Ref. No. ABC/123 for 50 MW capacity "
                "which will be delivered within 25 years.")
        self.assertEqual(evaluate_event("ORDER_WIN", "EXAMPLE", "", text)["summary_points"], [text])

    def test_negated_financing_and_management_are_withheld(self):
        for category, text in [
            ("FINANCING", "The bank has never sanctioned a loan of INR 250 crore to Example."),
            ("MANAGEMENT_CHANGE", "Mr. Example Person has not, after extensive discussions, resigned as director."),
        ]:
            self.assertFalse(evaluate_event(category, "EXAMPLE", "", text)["publish"])

class SinglePassTests(unittest.TestCase):
    def test_one_shot_drains_newly_queued_pdf(self):
        from concurrent.futures import Future
        from src.daemon import RealtimeDaemon
        daemon = RealtimeDaemon.__new__(RealtimeDaemon)
        daemon.state = MagicMock()
        daemon.state.is_seen.return_value = False
        daemon.filter_engine = MagicMock()
        daemon.filter_engine.should_drop.return_value = False
        daemon._pdf_pending = {}
        daemon._pdf_retry = {}
        daemon._pdf_pool = MagicMock()
        daemon.total_errors = 0
        done = Future()
        done.set_result(("", ["Example received an order worth INR 250 crore."]))
        daemon._pdf_pool.submit.return_value = done
        items = daemon.prepare_items([{"id": "one-shot", "source": "NSE",
                                       "pdf_url": "https://example.com/order.pdf"}], wait_pdfs=True)
        self.assertEqual(len(items), 1)
        self.assertIn("INR 250 crore", items[0]["description"])
        self.assertEqual(daemon._pdf_pending, {})

class ManagementAndUnitsTests(unittest.TestCase):
    def test_company_secretary_signature_does_not_define_category(self):
        self.assertNotEqual(FilterEngine()._classify_category(
            "Corporate update", "Signed by Company Secretary"), "MANAGEMENT_CHANGE")

    def test_takeover_charge_is_retained_as_stated(self):
        from src.quality import sentences
        text = ("In continuation to our letter dated 26.09.2026, it is informed that "
                "Shri Example Person has taken over the charge as Executive Director "
                "with effect from 12.10.2026.")
        points = sentences(text)
        result = evaluate_event("MANAGEMENT_CHANGE", "EXAMPLE", "", "\n".join(points))
        self.assertTrue(result["publish"])
        self.assertIn("12.10.2026", str(result["summary_points"]))
        self.assertNotIn("APPOINTMENT", result["headline"])

    def test_smt_name_not_split_at_abbreviation(self):
        from src.quality import sentences
        text = "Smt. Example Person was appointed as Independent Director with effect from 14.08.2026."
        self.assertEqual(sentences(text), [text])
        self.assertTrue(evaluate_event("MANAGEMENT_CHANGE", "EXAMPLE", "", text)["publish"])

    def test_broker_target_does_not_invent_currency(self):
        engine = FilterEngine()
        for target in ["500", "USD 500", "INR 500"]:
            result = engine._parse_broker_note("Citi on Example: BUY, target " + target, "")
            self.assertEqual(result["target"], target)
