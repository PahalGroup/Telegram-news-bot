"""Observed output regressions; preserve changed facts and explicit context."""
import unittest
from src.filter_engine import FilterEngine
from src.result_evidence import select_result_points


class OutputPrecisionTests(unittest.TestCase):
    def ipo(self, text):
        return FilterEngine(config_path='/nonexistent/rules.json').evaluate_stage_2({
            'company': 'Example', 'source': 'BSE', 'category': 'CORP_RESTRUCTURE',
            'title': 'IPO update', 'description': text})

    def test_ipo_fragment_is_withheld(self):
        self.assertFalse(self.ipo('As part of the IPO, Edelweiss Securities and Investments Private Limited (“ESIPL”), a wholly')['publish'])

    def test_ipo_form_boilerplate_is_withheld(self):
        self.assertFalse(self.ipo('Name of the target entity, details in brief such as size, turnover etc.\n'
            'Edelweiss Financial Services Limited Corporate Identity Number: L99999MH1995PLC094641 Registered Office: Edelweiss House, off.')['publish'])

    def test_ipo_headline_alone_is_withheld(self):
        self.assertFalse(self.ipo('')['publish'])

    def test_complete_named_ipo_action_is_retained(self):
        text = 'Example Asset Management Limited completed its initial public offer of equity shares worth INR 500 crore.'
        self.assertEqual(self.ipo(text)['summary_points'], [text])

    def test_generic_allotment_cannot_borrow_ipo_identity_from_title(self):
        self.assertFalse(self.ipo('The shares were allotted to the investors on October 29, 2026 and admitted for listing and trading with effect from November 2, 2026.')['publish'])

    def points(self, *rows):
        return select_result_points('Financial results', '\n'.join(rows))[0]

    def test_reordered_jtl_revenue_fact_only_appears_once(self):
        a = 'Revenue from Operations of ₹13,354 Mn in H1 FY27 (+37.2% YoY)'
        b = 'H1 FY27 Revenue from Operations reached ₹ 13,354 Mn, up 37.2% year on year'
        self.assertEqual(self.points(a, b), [a])

    def test_different_period_scope_amount_direction_or_currency_survives(self):
        base = 'Consolidated Revenue from Operations of INR 100 Mn in H1 FY27 (+10% YoY)'
        for other in [
            'Consolidated Revenue from Operations of INR 100 Mn in Q2 FY27 (+10% YoY)',
            'Standalone Revenue from Operations of INR 100 Mn in H1 FY27 (+10% YoY)',
            'Consolidated Revenue from Operations of INR 101 Mn in H1 FY27 (+10% YoY)',
            'Consolidated Revenue from Operations of INR 100 Mn in H1 FY27 (-10% YoY)',
            'Consolidated Revenue from Operations of USD 100 Mn in H1 FY27 (+10% YoY)',
        ]:
            with self.subTest(other=other):
                self.assertEqual(self.points(base, other), [base, other])

    def test_extra_margin_or_segment_information_is_retained(self):
        a = 'Revenue from Operations of ₹13,354 Mn in H1 FY27 (+37.2% YoY)'
        for suffix in [' with an EBITDA margin of 8.2%', ' in the India segment']:
            b = a + suffix
            self.assertEqual(self.points(a, b), [a, b])

    def test_dedup_precedes_five_point_limit(self):
        a = 'Revenue from Operations of ₹13,354 Mn in H1 FY27 (+37.2% YoY)'
        b = 'H1 FY27 Revenue from Operations reached ₹ 13,354 Mn, up 37.2% year on year'
        others = [f'Consolidated EBITDA of INR {i} Mn in Q{i} FY27 (+10% YoY)' for i in range(1, 5)]
        self.assertEqual(self.points(a, b, *others), [a, *others])
