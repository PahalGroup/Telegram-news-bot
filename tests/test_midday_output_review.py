"""Regressions from the 2 November public no-delivery output review."""
import unittest
from src.filter_engine import FilterEngine


class MiddayOutputTests(unittest.TestCase):
    def setUp(self):
        self.engine = FilterEngine(config_path='/nonexistent/rules.json')

    def results(self, text):
        return self.engine.evaluate_stage_2({'company': 'Example', 'source': 'BSE',
            'category': 'FINANCIAL_RESULTS', 'title': 'Financial Results', 'description': text})

    def test_unaudited_subsidiary_scope_paragraph_is_not_group_result(self):
        text = ('The accompanying Statement includes unaudited interim financial results in respect of: '
                '2 subsidiaries, whose interim financial results include total assets of Rs. 199.70 million, '
                'total revenues of Rs. 14.09 million and total net loss after tax of Rs. 9.34 million, '
                'whose interim financial results have not been audited/ reviewed by their auditors.')
        self.assertFalse(self.results(text)['publish'])

    def test_franchisee_hypothetical_is_not_company_revenue(self):
        text = ('So our franchisee can be as big as like doing a revenue of INR5 lakhs to INR10 lakhs '
                'a month and can also be a franchisee who is managing a revenue of INR60,000 a month.')
        self.assertFalse(self.results(text)['publish'])

    def test_historical_multi_year_growth_is_not_current_results(self):
        text = ('Revenue Grew To ₹11,109.40 L (FY26) From ₹9,225.57 L (FY23); '
                'EBITDA Margin Expanded From 16.06% To 22.59%.')
        self.assertFalse(self.results(text)['publish'])

    def test_adjacent_year_comparison_remains_eligible(self):
        text = 'Revenue grew to INR 110 crore in FY27 from INR 100 crore in FY26.'
        self.assertEqual(self.results(text)['summary_points'], [text])

    def test_unscoped_scorecard_rows_are_withheld(self):
        for text in ['Total Revenue: 2% YoY Increase at ₹218 Crores',
                     'Profit After Tax: 9% YoY Increase at ₹11 Crores',
                     'REVENUE FROM OPERATIONS ₹5,104 L',
                     'Profit After Tax (PAT) ₹219.2 Mn']:
            with self.subTest(text=text):
                self.assertFalse(self.results(text)['publish'])

    def test_explicit_scope_and_period_rows_survive(self):
        for text in ['Consolidated Profit After Tax: 39% YoY Increase at ₹6,103 Crores',
                     'Standalone Profit After Tax: 26% YoY Increase at ₹5,527 Crores',
                     'Operating Revenue: 51% growth for Q2 FY2027']:
            with self.subTest(text=text):
                self.assertEqual(self.results(text)['summary_points'], [text])

    def test_garbled_transcript_is_not_a_result(self):
        text = ('With a PAT margin of 1.55% at, EBITDA level of you can see the year-over-year '
                'is growing from FY24 to 26, you have reached and, EBITDA at 4.58%.')
        self.assertFalse(self.results(text)['publish'])

    def test_private_use_bullets_are_separate_facts(self):
        text = '\uf0a7 Revenue grew 40.9% YoY in Q2 FY27 \uf0a7 9 new restaurants opened in Q2'
        self.assertEqual(self.results(text)['summary_points'], ['Revenue grew 40.9% YoY in Q2 FY27'])

    def test_transcript_title_outranks_incidental_crude_oil_and_fda_words(self):
        for desc in ['Crude oil and import duty affect our costs.', 'We discussed a USFDA inspection.']:
            with self.subTest(desc=desc):
                self.assertEqual(self.engine._classify_category(
                    'Announcement under Regulation 30 - Earnings Call Transcript', desc), 'FINANCIAL_RESULTS')

    def action(self, title, description):
        return self.engine.evaluate_stage_2({'company': 'Example', 'source': 'BSE',
            'category': 'CORP_RESTRUCTURE', 'title': title, 'description': description})

    def test_dividend_policy_is_not_a_dividend_declaration(self):
        self.assertFalse(self.action('Dividend Distribution Policy',
            'The Board approved modification to the Dividend Distribution Policy. '
            'The policy covers interim dividend and parameters before declaring any dividend.')['publish'])

    def test_final_dividend_is_not_mislabelled_interim(self):
        text = 'The Board recommended a final dividend of INR 5 per equity share.'
        result = self.action('Final dividend', text)
        self.assertTrue(result['publish'])
        self.assertEqual(result['headline'], 'EXAMPLE – FINAL DIVIDEND')
        self.assertEqual(result['summary_points'], [text])

    def test_interim_dividend_per_share_remains_eligible(self):
        self.assertTrue(self.action('Interim dividend',
            'The Board declared an interim dividend of Rs. 2.50 (250%) per equity share.')['publish'])

    def test_face_value_and_negated_declaration_are_not_dividends(self):
        for text in ['The Board approved a dividend policy for equity shares of face value INR 5 per share.',
                     'The Board has not declared an interim dividend of INR 5 per share.']:
            with self.subTest(text=text):
                self.assertFalse(self.action('Dividend update', text)['publish'])

    def test_generic_update_cannot_publish_a_title_after_losing_long_body(self):
        result = self.engine.evaluate_stage_2({'company': 'Example', 'source': 'NSE',
            'category': 'GENERAL_WIRE', 'title': 'Update On The Proposed Project',
            'description': 'The approved project costs INR 25 crore ' + ('with detailed background ' * 60)})
        self.assertFalse(result['publish'])

    def test_generic_update_keeps_concrete_project_detail(self):
        text = 'The company approved construction of a new factory with an investment of INR 25 crore.'
        result = self.engine.evaluate_stage_2({'company': 'Example', 'source': 'NSE',
            'category': 'GENERAL_WIRE', 'title': 'Project update', 'description': text})
        self.assertTrue(result['publish'])
        self.assertEqual(result['summary_points'], [text])

    def test_macro_notice_does_not_repeat_truncated_ellipsis(self):
        text = 'The Exchange sought clarification concerning SEBI .... The Exchange sought clarification about the reported SEBI order.'
        result = self.engine.evaluate_stage_2({'source': 'BSE', 'company': 'Example',
            'category': 'POLICY_MACRO', 'title': 'Clarification sought', 'description': text})
        self.assertFalse(any('...' in point for point in result['summary_points']))

    def test_period_alone_cannot_resolve_subsidiary_scope_in_mixed_filing(self):
        group = 'Consolidated Profit After Tax: 39% YoY Increase at ₹6,103 Crores'
        parent = 'Standalone Profit After Tax: 26% YoY Increase at ₹5,527 Crores'
        subsidiary = 'Profit After Tax in H1 FY27 at Rs. 144 crores, compared to a loss of Rs.160 crores in H1 FY26.'
        result = self.results('\n'.join([group, parent, subsidiary]))
        self.assertEqual(result['summary_points'], [group, parent])
        self.assertEqual(result['evidence_rejections']['ambiguous_scope_in_mixed_filing'], 1)

    def test_clipped_comparative_period_and_chart_tail_are_withheld(self):
        for text in [
            'Revenue from operations stood at ₹295.7 crore, up 9% YoY from ₹270.2 crore in Q2',
            'Q2 FY27 revenue of ₹4,293 million; Y-o-Y growth of +40.9% nue wth',
        ]:
            with self.subTest(text=text):
                self.assertFalse(self.results(text)['publish'])

    def test_constant_currency_growth_label_is_not_garbled_text(self):
        for suffix in ('YoY CC', 'yoy cc'):
            text = 'Revenue grew 43% ' + suffix
            self.assertEqual(self.results(text)['summary_points'], [text])
