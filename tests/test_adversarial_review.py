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
