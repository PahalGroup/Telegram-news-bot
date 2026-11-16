"""Regressions for the 14 Nov 2026 live-run TypeError, FY label, Tembo dup, and fragments."""
import unittest
from datetime import datetime, timezone, timedelta

from src.composer import _period_label, filing_bullets
from src.description_merge import (
    flatten_points, is_clean_sentence, merge_exchange_description, tidy_point,
)
from src.result_events import is_near_duplicate_result, result_amounts
from src.scheduler import Pending


IST = timezone(timedelta(hours=5, minutes=30))
FILED = datetime(2026, 11, 14, 13, 53, tzinfo=IST)

PATANJALI_REV = (
    "Revenue from Operations was Rs. 11,322.58 crores, compared to "
    "Rs. 9,798.84 crores in Q2FY26, representing 15.55% YoY growth."
)
PATANJALI_FRAGMENT = (
    "'Listing Regulations'), we wish to inform you that the Board of Directors "
    "of Patanjali Foods Limited ('the Company') at their meeting held on today i.e."
)
VEEFIN = (
    "The Company has received resignation of Mrs. Payal Maisheri, as a Chief "
    "Financial Officer of the Company with effect from 14th November, 2026. 2."
)
TEMBO_A = [
    "Revenue for Q2FY27 stood at INR 232 crores.",
    "For H1FY27, revenue increased 8.2% YoY to INR 534 crores.",
    "EBITDA for Q2FY27 increased 11.2% YoY to INR 37 crores, with EBITDA margin at 15.9%.",
    "For H1FY27, EBITDA grew 16.6% YoY to INR 54 crores.",
    "PAT for Q2FY27 increased 10.1% YoY to INR 24 crores.",
    "For H1FY27, PAT grew 20.7% YoY to INR 35 crores.",
]
TEMBO_B = TEMBO_A[1:]  # BSE copy, missing the press-release Q2 revenue line


def _pending(key, company, event, bullets, url):
    hdr = f"<b>{company} – {event}</b>"
    return Pending(key, company, event, hdr, bullets, FILED, FILED,
                   f'<a href="{url}">Source</a>')


class FlattenAndMergeTests(unittest.TestCase):
    def test_empty_evidence_tuple_does_not_break_join(self):
        # Live TypeError: pdf_router returned bullets=("", []) and join saw a list.
        bullets = flatten_points(("", []))
        self.assertEqual(bullets, [])
        text = merge_exchange_description("Board approved results.", ("", []))
        self.assertIsInstance(text, str)
        self.assertIn("Board approved results.", text)

    def test_nested_lists_flatten_to_strings(self):
        points = flatten_points(["Revenue was Rs. 10 crore.", ["PAT was Rs. 2 crore."], None, 0])
        self.assertEqual(points, ["Revenue was Rs. 10 crore.", "PAT was Rs. 2 crore.", "0"])
        "\n".join(flatten_points(["a", ["b", ["c"]], ("", [])]))

    def test_fragment_is_kept_for_gate_but_not_prepended(self):
        merged = merge_exchange_description(
            PATANJALI_FRAGMENT, [PATANJALI_REV, "Operating Revenue grew by 15.55% on a YoY basis."]
        )
        lines = merged.split("\n")
        self.assertNotEqual(lines[0], PATANJALI_FRAGMENT)
        self.assertEqual(lines[0], PATANJALI_REV)
        self.assertIn(PATANJALI_FRAGMENT, lines)  # still present for classify/gate

    def test_clean_exchange_sentence_is_prepended(self):
        original = "The Board appointed Mr. Tejas Sampat as Chief Financial Officer with effect from 14 November 2026."
        merged = merge_exchange_description(original, ["PAT was Rs. 2 crore."])
        self.assertTrue(merged.startswith(original))

    def test_fragment_is_not_a_clean_sentence(self):
        self.assertFalse(is_clean_sentence(PATANJALI_FRAGMENT))
        self.assertTrue(is_clean_sentence(PATANJALI_REV))


class FragmentPostTests(unittest.TestCase):
    def test_listing_regulations_fragment_dropped_from_post(self):
        out = filing_bullets([
            PATANJALI_FRAGMENT,
            "Saturday, November 14, 2026, have inter alia, considered, approved and took on record "
            "the unaudited standalone and consolidated financial results for the quarter and "
            "half-year ended September 30, 2026.",
            PATANJALI_REV,
        ])
        self.assertTrue(out)
        self.assertFalse(any(is_clean_sentence(p) is False for p in out))
        self.assertFalse(any(p.startswith("'Listing Regulations") for p in out))
        self.assertFalse(any(p.startswith("Saturday, November 14") for p in out))
        self.assertIn(PATANJALI_REV, out)

    def test_stray_list_number_stripped(self):
        self.assertEqual(tidy_point(VEEFIN)[-2:], "6.")
        self.assertFalse(tidy_point(VEEFIN).endswith("2."))
        out = filing_bullets([VEEFIN, "Appointed Mr. Tejas Sampat as Chief Financial Officer."])
        self.assertTrue(out)
        self.assertTrue(any("Payal Maisheri" in p for p in out))
        self.assertFalse(any(p.rstrip().endswith("2.") for p in out))


class PeriodLabelTests(unittest.TestCase):
    def test_patanjali_comparison_only_fy26_uses_filing_date(self):
        self.assertEqual(
            _period_label(PATANJALI_REV, "FINANCIAL RESULTS", FILED),
            "Q2 FY27 RESULTS",
        )

    def test_both_years_still_picks_current(self):
        self.assertEqual(
            _period_label("₹277 Cr in Q2 FY27 vs ₹210 Cr in Q2 FY26; H1 FY27 ₹657 Cr",
                          "FINANCIAL RESULTS"),
            "Q2 FY27 RESULTS",
        )

    def test_in_line_with_prior_year_does_not_win(self):
        body = ("For H1 FY '27, revenue grew 9.7% to Rs. 335 crores. "
                "Profit after tax was INR4.2 crores, in line with Q2 FY '26 with a PAT margin of 2.4%.")
        self.assertEqual(_period_label(body, "FINANCIAL RESULTS"), "H1 FY27 RESULTS")


class TemboNearDupTests(unittest.TestCase):
    def test_bse_copy_is_near_duplicate_of_nse_press_release(self):
        first = _pending("NSE_TEMBO_1", "Tembo Global Industries Limited", "Q2 FY27 RESULTS",
                         TEMBO_A, "https://nsearchives.nseindia.com/a.pdf")
        second = _pending("BSE_TEMBO_2", "Tembo Global Industries Ltd", "Q2 FY27 RESULTS",
                          TEMBO_B, "https://www.bseindia.com/b.pdf")
        records = [{
            "item_id": "NSE_TEMBO_1",
            "headline": "TEMBO GLOBAL – FINANCIAL RESULTS",
            "category": "FINANCIAL_RESULTS",
            "company": first.company,
            "event": first.event,
            "lead": TEMBO_A[0],
            "body": "\n".join(TEMBO_A),
        }]
        self.assertFalse(is_near_duplicate_result(first, {"category": "FINANCIAL_RESULTS"}, []))
        self.assertTrue(is_near_duplicate_result(second, {"category": "FINANCIAL_RESULTS",
                                                          "company": second.company}, records))
        # Different Source URLs and the extra 232-crore line must not hide the overlap.
        self.assertGreaterEqual(len(result_amounts(second.text()) & result_amounts("\n".join(TEMBO_A))) / len(result_amounts(second.text())), 0.8)

    def test_new_facts_are_not_treated_as_duplicate(self):
        first = _pending("NSE_X_1", "Example Industries Limited", "Q2 FY27 RESULTS",
                         ["Revenue for Q2FY27 was Rs. 100 crores.",
                          "EBITDA for Q2FY27 was Rs. 20 crores.",
                          "PAT for Q2FY27 was Rs. 8 crores."],
                         "https://nsearchives.nseindia.com/a.pdf")
        update = _pending("BSE_X_2", "Example Industries Ltd", "Q2 FY27 RESULTS",
                          ["Management has now reported a one-time tax credit of Rs. 45 crores.",
                           "Order book at the end of Q2FY27 stood at Rs. 900 crores.",
                           "Net debt declined to Rs. 12 crores."],
                          "https://www.bseindia.com/b.pdf")
        records = [{
            "company": first.company, "event": first.event, "category": "FINANCIAL_RESULTS",
            "lead": first.bullets[0], "body": "\n".join(first.bullets),
        }]
        self.assertFalse(is_near_duplicate_result(update, {"category": "FINANCIAL_RESULTS",
                                                           "company": update.company}, records))


class PdfRouterEmptyEvidenceTests(unittest.TestCase):
    def test_empty_evidence_is_a_list_of_strings(self):
        # The live crash: `return "", [texts] if evidence else ("", [])` produced
        # bullets=("", []) because of comma/ternary precedence.
        from src import pdf_router
        src = open(pdf_router.__file__).read()
        self.assertNotIn('if evidence else ("", [])', src)
        self.assertIn('return "", [e["text"] for e in evidence]', src)
