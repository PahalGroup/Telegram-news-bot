"""Actual late-session defects plus counterexamples that must remain eligible."""
import unittest
from src.filter_engine import FilterEngine
from src.result_evidence import select_result_points

GRANT = ('The Nomination and Remuneration Committee of Physicswallah Limited has approved '
         'the grant of 1,84,828 stock options to eligible employees under ESOP Plan 2025.')
CONDITIONAL = ('In case of any corporate actions such as rights issue, bonus issue, split or '
               'consolidation of equity shares, requisite adjustments to the options shall be made.')

class CapitalAndPeriodTests(unittest.TestCase):
    def setUp(self):
        self.engine = FilterEngine(config_path='/nonexistent/rules.json')

    def action(self, text, title='Corporate update'):
        category = self.engine._classify_category(title, text)
        return self.engine.evaluate_stage_2({'company': 'Example', 'source': 'NSE',
            'category': category, 'title': title, 'description': text})

    def test_actual_esop_grant_is_not_split(self):
        r = self.action(GRANT + '\n' + CONDITIONAL)
        self.assertTrue(r['publish'])
        self.assertEqual(r['headline'], 'EXAMPLE – STOCK OPTION GRANT')
        self.assertEqual(r['summary_points'], [GRANT])

    def test_esop_grant_without_split_keywords_is_recognized(self):
        self.assertTrue(self.action(GRANT)['publish'])

    def test_hypothetical_adjustment_alone_is_withheld(self):
        self.assertFalse(self.action(CONDITIONAL, 'Stock option plan')['publish'])

    def test_negated_or_proposed_grants_are_not_reported_as_approved(self):
        for text in [GRANT.replace('has approved', 'has not approved'),
                     GRANT.replace('has approved', 'may approve'),
                     GRANT.replace('1,84,828 stock options', 'stock options')]:
            with self.subTest(text=text):
                self.assertFalse(self.action(text)['publish'])

    def test_real_split_preserves_face_values_and_condition(self):
        text = ('The Board approved the sub-division of each equity share of face value Rs.10 '
                'into five equity shares of face value Rs.2 each, subject to shareholder approval.')
        r = self.action(text, 'Stock split')
        self.assertTrue(r['publish'])
        self.assertEqual(r['headline'], 'EXAMPLE – STOCK SPLIT')
        self.assertEqual(r['summary_points'], [text])

    def test_actual_split_and_grant_are_both_retained(self):
        split = 'The Board approved a stock split of equity shares in the ratio 1:5.'
        r = self.action(GRANT + '\n' + split)
        self.assertEqual(r['headline'], 'EXAMPLE – CAPITAL ACTIONS')
        self.assertEqual(set(r['summary_points']), {GRANT, split})

    def points(self, text):
        return select_result_points('Financial results', text)[0]

    def test_unlabelled_signed_percentage_row_is_withheld(self):
        self.assertEqual(self.points('OPERATING EBITDA % 9.4% 7.8% -159 bps 9.0% -119 bps'), [])

    def test_labelled_margin_comparison_remains(self):
        text = 'Consolidated EBITDA margin was 9.4% in Q2 FY27 versus 7.8% in Q2 FY26, up 159 bps.'
        self.assertEqual(self.points(text), [text])

    def test_prior_period_recognition_note_is_not_current_result(self):
        for text in [
            'The Company refunded USD 2 million and recognised revenue of INR 2,921 lakhs in the previous quarter.',
            'During the previous year, the Company recognised revenue of INR 184,002 lakhs from a voucher.',
        ]:
            with self.subTest(text=text):
                self.assertEqual(self.points(text), [])

    def test_current_result_with_prior_period_comparator_remains(self):
        text = 'Revenue grew to INR 120 crore in the current quarter from INR 100 crore in the previous quarter.'
        self.assertEqual(self.points(text), [text])
