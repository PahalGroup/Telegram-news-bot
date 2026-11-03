"""Scorecard parsing on real BSE result statements (statement page only).

Expected values were checked by hand against each PDF's printed rows.
"""
import json
import unittest
from pathlib import Path

import fitz

from sources.result_scorecard import (change, company_name, format_scorecard,
                                      indian_number, parse_document)

FIX = Path(__file__).parent / "fixtures" / "results"
EXPECTED = json.loads((FIX / "expected.json").read_text())


class RealStatements(unittest.TestCase):
    def test_ten_plus_real_pdfs_match_hand_checked_values(self):
        self.assertGreaterEqual(len(EXPECTED), 10)
        for name, exp in EXPECTED.items():
            with self.subTest(name):
                card = parse_document(fitz.open(FIX / name))
                self.assertIsNotNone(card)
                self.assertEqual(card["scope"], exp["scope"])
                self.assertEqual(card["period"], exp["period"])
                for metric, vals in exp["metrics"].items():
                    for k, v in vals.items():
                        self.assertAlmostEqual(card["metrics"][metric][k], v, places=3)

    def test_signs_follow_the_numbers(self):
        for name in EXPECTED:
            card = parse_document(fitz.open(FIX / name))
            text = format_scorecard(EXPECTED[name]["company"], card)
            for metric, vals in card["metrics"].items():
                for k in ("yoy", "qoq"):
                    if k in vals and vals["cur"] > 0 and vals[k] > 0 and vals["cur"] != vals[k]:
                        arrow = "↑" if vals["cur"] > vals[k] else "↓"
                        self.assertIn(arrow, text)

    def test_hand_values(self):
        card = parse_document(fitz.open(FIX / "chambal_ccd9.pdf"))
        text = format_scorecard("Chambal Fertilisers & Chemicals Ltd", card)
        self.assertIn("<b>Chambal Fertilisers &amp; Chemicals Ltd – Q2 FY27 RESULTS</b>\n<i>Standalone</i>", text)
        self.assertIn("• Revenue: ₹6,999.46 Cr ↑9.1% YoY | ↑39.2% QoQ", text)
        self.assertIn("• Net Profit: ₹634.95 Cr ↑5.5% YoY | ↓9.7% QoQ", text)


class Wording(unittest.TestCase):
    def test_indian_grouping(self):
        self.assertEqual(indian_number(150503.77), "1,50,503.77")
        self.assertEqual(indian_number(999.5), "999.50")

    def test_loss_turns(self):
        self.assertEqual(change(5.0, -2.0, profit=True), "vs loss ₹2.00 Cr")
        self.assertEqual(change(-1.0, 3.0, profit=True), "vs profit ₹3.00 Cr")
        self.assertEqual(change(-1.0, -3.0), "vs -₹3.00 Cr")
        self.assertEqual(change(110, 100), "↑10.0%")
        self.assertEqual(change(90, 100), "↓10.0%")

    def test_loss_line(self):
        card = {"scope": "STANDALONE", "period": "Q2 FY27", "metrics": {
            "revenue": {"cur": 10, "yoy": 8}, "pat": {"cur": -0.5, "yoy": -1.2}}}
        self.assertIn("• Net Loss: ₹0.50 Cr vs loss ₹1.20 Cr YoY", format_scorecard("X Ltd", card))

    def test_company_name(self):
        self.assertEqual(company_name("Zydus Wellness Ltd-$"), "ZYDUS WELLNESS")
        self.assertEqual(company_name("Moil Limited"), "MOIL")
