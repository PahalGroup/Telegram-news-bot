"""Layout chrome: dash bullets, blank line after the headline, Filing link."""
import unittest
from src.post_style import render, link, BULLET, LINK_LABEL
from sources.result_scorecard import format_scorecard


class PostStyleTests(unittest.TestCase):
    def test_render_uses_dash_and_blank_line(self):
        text = render("<b>Example Ltd – ORDER WIN</b>", ["Order value is INR 100 crore."],
                      link("https://www.bseindia.com/a.pdf"))
        self.assertIn("<b>Example Ltd – ORDER WIN</b>\n\n- Order value is INR 100 crore.", text)
        self.assertIn(f">{LINK_LABEL}</a>", text)
        self.assertTrue(BULLET.startswith("-"))
        self.assertNotIn("• ", text)
        self.assertNotIn("Source", text)

    def test_scorecard_headline_folds_scope_and_leads_with_profit(self):
        card = {"scope": "CONSOLIDATED", "period": "Q2 FY27", "metrics": {
            "revenue": {"cur": 100, "yoy": 80, "qoq": 90},
            "ebitda": {"cur": 20, "yoy": 16, "qoq": 18},
            "pat": {"cur": 10, "yoy": 8, "qoq": 9},
        }}
        text = format_scorecard("Example Ltd", card)
        self.assertIn("Example Ltd – Q2 FY27 (CONSOLIDATED)", text)
        self.assertNotIn("RESULTS", text)
        self.assertLess(text.index("Net Profit"), text.index("Revenue"))
        self.assertIn("- Net Profit: ₹10.00 Cr | YoY ↑ 25.0% | QoQ ↑ 11.1%", text)


from src.post_style import shorten_management, dedupe_bullets, near_duplicate
from src import composer, editorial
from datetime import datetime


class ManagementBulletTests(unittest.TestCase):
    LONG = (
        "Remuneration Committee, in their meeting held on November 13, 2026, have considered "
        "and approved the appointment of Ms. Shubha Singh (ICSI Membership No. A16735) as the "
        "Company Secretary and Compliance Officer of the Company, designated as a Key Managerial "
        "Personnel, with effect from December 01, 2026."
    )
    SHORT = (
        "Appointment of Ms. Shubha Singh as Company Secretary and Compliance Officer of the "
        "Company, designated as a Key Managerial Personnel, with effect from December 01, 2026."
    )
    WANT = "Ms. Shubha Singh appointed as Company Secretary and Compliance Officer, effective December 01, 2026"

    def test_shorten_who_role_date(self):
        self.assertEqual(shorten_management(self.LONG), self.WANT)
        self.assertEqual(shorten_management(self.SHORT), self.WANT)

    def test_near_duplicate_long_and_short_forms(self):
        self.assertTrue(near_duplicate(self.LONG, self.SHORT))
        self.assertEqual(dedupe_bullets([self.LONG, self.SHORT]), [self.SHORT])

    def test_compose_management_is_one_short_bullet(self):
        ev = editorial.Event("MANAGEMENT CHANGE", "needs_decision")
        pending, why = composer.compose_filing(
            {"id": "polyplex", "company": "Polyplex Corporation Ltd", "source": "BSE",
             "pdf_url": "https://www.bseindia.com/example.pdf"},
            {"headline": "Polyplex Corporation Ltd – MANAGEMENT CHANGE",
             "summary_points": [self.LONG, self.SHORT]},
            ev, datetime(2026, 11, 13, 19, 33),
        )
        self.assertIsNotNone(pending, why)
        text = pending.text()
        self.assertEqual(text.count("Shubha Singh"), 1)
        self.assertIn(self.WANT, text)
        self.assertNotIn("Remuneration Committee", text)
        self.assertNotIn("ICSI Membership", text)
        self.assertIn("Filing", text)

    def test_unparseable_management_line_is_left_alone(self):
        raw = "The Board noted a general update on organisational matters without naming anyone."
        self.assertEqual(shorten_management(raw), raw)
