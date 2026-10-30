"""Failures found by reading actual shadow output; no production sends."""
import unittest
from src.filter_engine import FilterEngine


class ShadowContentTests(unittest.TestCase):
    def setUp(self):
        self.engine = FilterEngine(config_path='/nonexistent/rules.json')

    def results(self, text, title='Q2 FY27 financial results'):
        return self.engine.evaluate_stage_2({'company': 'Example', 'category': 'FINANCIAL_RESULTS',
                                            'title': title, 'description': text})

    def test_audit_boilerplate_without_metric_value_is_rejected(self):
        text = ('We have reviewed the accompanying consolidated financial results and its share of the net loss '
                'of its associate, being submitted pursuant to the requirements of Regulation 33 and the Board of Directors.')
        self.assertFalse(self.results(text)['publish'])

    def test_auditor_subsidiary_totals_are_not_group_results(self):
        text = ('We did not review the interim financial information of 25 subsidiaries whose total revenues '
                'were INR 67286 lakhs and net profit after tax was INR 11922 lakhs for the quarter.')
        self.assertFalse(self.results(text)['publish'])

    def test_metric_axis_label_is_not_news(self):
        for text in ['Profit After Tax (INR Crores)', '(p) Net profit margin (in %)',
                     'Half Year ended September 30, 2025 CASH FLOW FROM OPERATING ACTIVITIES: Net Profit Before Tax Adjustments']:
            with self.subTest(text=text):
                self.assertFalse(self.results(text)['publish'])

    def test_history_is_not_selected_before_current_results(self):
        old = 'Successfully listed on NSE and BSE in 2024, Net Profit crossed INR 100 crores.'
        current = 'Consolidated net profit was INR 250 crore for Q2 FY27.'
        result = self.results(old + '\n' + current)
        self.assertEqual(result['summary_points'], [current])

    def test_dividend_tax_paperwork_is_not_mou_or_dividend_decision(self):
        passed, _, _ = self.engine.evaluate_stage_1({'source': 'BSE', 'company': 'Example',
            'title': 'Tax on 2nd Interim Dividend',
            'description': 'Shareholders may submit Form 15H where the amount of dividend does not exceed INR 10000.'})
        self.assertFalse(passed)

    def test_amount_substring_cannot_create_mou_headline(self):
        result = self.engine.evaluate_stage_2({'company': 'Example', 'category': 'CORP_RESTRUCTURE',
            'title': 'Interim dividend declared', 'description': 'The board declared an interim dividend amount of INR 5 per share.'})
        self.assertNotIn('MEMORANDUM', result['headline'])

    def test_regulatory_reference_is_not_macro_action(self):
        category = self.engine._classify_category('Appointment of Secretarial Auditor',
            'Disclosure required under SEBI Circular No. 123; appointed by the board for FY27.')
        self.assertNotEqual(category, 'POLICY_MACRO')

    def test_explicit_rating_title_overrides_background_earnings(self):
        self.assertEqual(self.engine._classify_category('Credit Rating',
            'Revenue growth and EBITDA margin supported the credit profile. ICRA reaffirmed AA.'), 'CREDIT_RATING')

    def test_results_with_numbers_remain_verbatim(self):
        text = 'Net profit was INR -1.23 crore, down 54.40% YoY.'
        self.assertEqual(self.results(text)['summary_points'], [text])
