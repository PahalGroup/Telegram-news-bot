import json
import unittest
from pathlib import Path
from sources.pdf_layout import join_bullet_continuations
from sources.disclosure_tables import disclosure_table_evidence
from src.disclosure_evidence import acquisition, regulatory
from src.result_evidence import select_result_points

class DisclosureRepairTests(unittest.TestCase):
    def test_original_dcw_geometry_retains_complete_bullet(self):
        fixture = json.loads((Path(__file__).parent / 'fixtures/dcw_bullet_geometry.json').read_text())
        rows = join_bullet_continuations(fixture['blocks'], fixture['width'])
        point = next(b[4] for b in rows if '6,629 Mn, driven' in b[4])
        self.assertIn('growth in Speciality Chemicals and 19.9% growth in Basic Chemicals.', point)
        self.assertNotIn('Revenue from \nOperations', point)
        text = next(b[4] for b in rows if 'grew 32.7%' in b[4])
        self.assertIn('loss of INR 188 Mn', text)
        self.assertIn('normalisation of VCM', text)

    def test_bullet_does_not_cross_new_bullet_table_or_gap(self):
        first = [500, 100, 920, 114, '• Revenue grew 20% to INR 100 crore, driven by']
        continuation = [514, 117, 900, 130, 'higher volumes in the specialty chemicals business.']
        for rows in [
            [first, [500, 117, 920, 130, '• Another independent revenue fact']],
            [first, [514, 140, 900, 153, continuation[4]]],
            [first, [500, 114, 920, 116, ''], continuation],
            [first, continuation, [514, 117, 880, 130, 'Ambiguous second continuation']],
        ]:
            result = join_bullet_continuations(rows, 960)
            self.assertEqual(result[0][4], first[4])

    def test_chart_caption_is_not_current_result(self):
        points, _ = select_result_points('', 'Geographical Revenue Break-up (As on FY26) 72% 28% DOMESTIC EXPORT')
        self.assertEqual(points, [])

    def test_acquisition_keeps_target_cost_and_pending_transfer(self):
        text = ('Name of the target entity: Example Hospitals Private Limited.\n'
                'Objects and effects of acquisition (including but not limited to disclosure of reasons).\n'
                'KIMS has acquired 51.00% of the equity share capital of Target Company for up to ₹79.50 Crore.\n'
                'The Shares will be transferred in due course.\n'
                'The target hospital commissioned 183 beds in 2021.')
        result = acquisition('KIMS', 'Agreement to acquire 51% equity stake', text)
        self.assertTrue(result['publish'])
        self.assertEqual(result['headline'], 'KIMS – ACQUISITION AGREEMENT')
        self.assertIn('79.50', str(result['summary_points']))
        self.assertIn('will be transferred', str(result['summary_points']))
        self.assertNotIn('183 beds', str(result['summary_points']))
        self.assertNotIn('Objects and effects', str(result['summary_points']))

    def test_target_history_or_unconfirmed_action_is_insufficient(self):
        for text in ['Name of the target entity: Example Hospitals Limited.\nThe target commissioned 183 beds.',
                     'Name of the target entity: Example Hospitals Limited.\nThe company has not acquired 51% stake.']:
            self.assertFalse(acquisition('EXAMPLE', 'Acquisition of shares', text)['publish'])

    def test_fda_context_and_outcome_preserve_negative_qualification(self):
        context = 'US FDA conducted an unannounced inspection of the manufacturing facility in Ambernath from 21 to 29 October 2026.'
        outcome = 'Upon conclusion, a Form 483 containing 3 observations and 2 product observations was issued, all 5 do not relate to data integrity.'
        result = regulatory('RUBICON', 'General Update', context + '\n' + outcome)
        self.assertEqual(result['summary_points'], [context, outcome])
        self.assertFalse(regulatory('RUBICON', 'General Update', context)['publish'])

    def test_rating_pairs_keep_outlook_and_reject_ambiguous_columns(self):
        rows = [['Long Term Rating', 'CRISIL AA/Stable'], ['Shirt Term Rating', 'CRISIL A1+'],
                ['Total Bank Loan Facilities Rated', 'Rs. 1050 Crore'],
                ['Long Term Rating', 'CRISIL AA/Stable', 'CRISIL A/Positive']]
        facts = disclosure_table_evidence(rows, 1)
        self.assertEqual(len(facts), 3)
        self.assertEqual(facts[0]['text'], 'Long Term Rating: CRISIL AA/Stable')
