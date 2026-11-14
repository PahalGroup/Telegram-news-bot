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
