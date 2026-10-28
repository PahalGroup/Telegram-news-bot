"""Regressions derived from the October exports; no live network or Telegram sends."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
import pymupdf
from src.quality import fingerprint, substantive
from src.formatter import Formatter
from src.filter_engine import FilterEngine
from sources.annexure_parser import AnnexureParser
from tests import test_reliability as fixtures

class AuditTests(unittest.TestCase):
    def setUp(self):
        self.engine = FilterEngine(config_path="/nonexistent/rules.json")

    def test_full_claim_fingerprints_preserve_distinct_numbers_and_signs(self):
        self.assertNotEqual(fingerprint("ABC", "Profit -1.23 crore"), fingerprint("ABC", "Profit 1.23 crore"))
        self.assertNotEqual(fingerprint("ABC", "Order 1.23 crore"), fingerprint("ABC", "Order 12.3 crore"))

    def test_financing_not_directorate(self):
        cat = self.engine._classify_category(
            "Board meeting outcome",
            "Board of Directors approved a lease facility up to Rs.81 crore and working capital loan.")
        self.assertEqual(cat, "FINANCING")

    def test_signatory_role_not_management_event(self):
        self.assertNotEqual(self.engine._classify_category(
            "Corporate update", "Signed by Chief Financial Officer"), "MANAGEMENT_CHANGE")

    def test_no_invented_fiscal_year(self):
        result = self.engine.evaluate_stage_2({
            "company": "ABC", "category": "FINANCIAL_RESULTS",
            "title": "Q2 FY28 financial results",
            "description": "Revenue from operations was INR 123 crore for Q2 FY28.",
        })
        self.assertTrue(result["publish"])
        self.assertNotIn("FY27", str(result))

    def test_profit_sign_and_percent_not_changed(self):
        result = self.engine.evaluate_stage_2({
            "company": "ABC", "category": "FINANCIAL_RESULTS", "title": "Financial results",
            "description": "Net profit was INR -1.23 crore, down 54.40% YoY.",
        })
        self.assertIn("-1.23", str(result))
        self.assertIn("54.40%", str(result))
        self.assertNotIn("₹54.40", str(result))

    def test_vrr_tenor_not_hardcoded(self):
        result = self.engine.evaluate_stage_2({
            "company": "RBI", "category": "POLICY_MACRO", "source": "RBI",
            "title": "RBI to conduct 3-day variable rate repo auction",
            "description": "Notified amount: Rs 200000 crore. Tenor 3 days.",
        })
        self.assertNotIn("4-DAY", result["headline"])
        self.assertIn("3-DAY", result["headline"])

    def test_placeholder_footer_cannot_make_post_complete(self):
        post = Formatter.format_post({
            "category": "GENERAL_WIRE", "headline": "VEDANTA LTD — Please refer the enclosed file.",
            "summary_points": ["Please refer the enclosed file.", "Verified from official BSE disclosure"],
        }, {"pdf_url": "https://www.bseindia.com/example.pdf"})
        self.assertEqual(post, "")

    def test_duplicate_bullets_removed_source_retained(self):
        post = Formatter.format_post({
            "category": "ORDER_WIN", "headline": "ABC — CONTRACT AWARD",
            "summary_points": ["Order value is INR 100 crore.", "Order value is INR 100 crore."],
        }, {"pdf_url": "https://www.bseindia.com/a.pdf?a=1&b=2"})
        self.assertEqual(post.count("Order value"), 1)
        self.assertIn('href="https://www.bseindia.com/a.pdf?a=1&amp;b=2"', post)
        self.assertIn("\n<a", post)

    def test_dangling_fragments_removed(self):
        post = Formatter.format_post({
            "category": "MANAGEMENT_CHANGE", "headline": "ABC — RESIGNATION",
            "summary_points": ["from the position of Chief Financial Officer", "resignation due to his other"],
        })
        self.assertEqual(post, "")

    def test_long_source_sentence_not_synthetically_truncated(self):
        value = "The company approved a contract " + "for new facilities " * 10 + "in Pune."
        self.assertEqual(self.engine._truncate_clean(value, 40), value)

    def test_pdf_does_not_invent_known_fixture_facts(self):
        with pymupdf.open() as doc:
            page = doc.new_page()
            page.insert_text((50, 50), "Transtech Punjab CBG LLP and Amitabh Tandon. Type-III Type-IV. 588.57.")
            data = doc.tobytes()
        evidence = AnnexureParser.extract_evidence(data)
        self.assertNotIn("51%", str(evidence))
        self.assertNotIn("65 Lakh", str(evidence))
        self.assertNotIn("600 units", str(evidence))
        self.assertNotIn("inclusive of GST", str(evidence))

    def test_pdf_preserves_negation_and_page(self):
        text = "The acquisition is not a related party transaction and does not require approval."
        with pymupdf.open() as doc:
            page = doc.new_page()
            page.insert_text((50, 50), text, fontsize=9)
            evidence = AnnexureParser.extract_evidence(doc.tobytes())
        self.assertEqual(evidence[0]["page"], 1)
        self.assertEqual(evidence[0]["text"], text)

    def test_dense_numeric_table_is_not_guessed(self):
        with pymupdf.open() as doc:
            page = doc.new_page()
            page.insert_text((50, 50), "Revenue from operations\n123.45\n112.34\n101.23", fontsize=10)
            evidence = AnnexureParser.extract_evidence(doc.tobytes())
        self.assertEqual(evidence, [])

class PipelineAuditTests(unittest.TestCase):
    setUp = fixtures.ReliabilityTests.setUp
    daemon = fixtures.ReliabilityTests.daemon
    def test_duplicate_content_new_source_id_is_blocked(self):
        daemon = self.daemon()
        self.assertEqual(daemon.process_cycle(), 1)
        daemon.poll_all_sources.return_value[0]["id"] = "event-2"
        self.assertEqual(daemon.process_cycle(), 0)
        daemon.dispatcher.send_message.assert_called_once()

    def test_distinct_same_company_category_is_not_suppressed(self):
        daemon = self.daemon()
        self.assertEqual(daemon.process_cycle(), 1)
        daemon.poll_all_sources.return_value = [{
            "id": "event-2", "source": "NSE", "title": "Example wins a second contract",
        }]
        daemon.filter_engine.evaluate_stage_2.return_value["summary_points"] = [
            "Contract value: INR 650 crore", "Completion within 36 months",
        ]
        self.assertEqual(daemon.process_cycle(), 1)

    def test_stage_error_remains_retryable(self):
        daemon = self.daemon()
        daemon.filter_engine.evaluate_stage_1.side_effect = RuntimeError("temporary parser failure")
        self.assertEqual(daemon.process_cycle(), 0)
        self.assertFalse(self.state.is_seen("event-1"))

    def test_pending_pdf_does_not_block_text(self):
        daemon = self.daemon()
        future = MagicMock()
        future.done.return_value = False
        daemon._pdf_pending = {"pdf": ({"id": "pdf"}, future)}
        daemon._pdf_retry = {}
        daemon._pdf_pool = None
        self.assertEqual(daemon.prepare_items([{"id": "text", "source": "NEWS"}]), [
            {"id": "text", "source": "NEWS"}])

if __name__ == "__main__":
    unittest.main()
