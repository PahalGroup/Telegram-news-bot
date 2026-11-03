import unittest
import pymupdf
from sources.annexure_parser import AnnexureParser
from src.result_evidence import select_result_points


class GroupScopeTests(unittest.TestCase):
    def test_group_portfolio_cannot_inherit_filing_issuer(self):
        with pymupdf.open() as doc:
            p = doc.new_page()
            p.insert_text((50, 50), 'Index\nGroup Financial Scorecard\nAlpha Limited\nBeta Limited\nGamma Limited\nRevenue was INR 100 crore for Q2 FY27.')
            self.assertEqual(AnnexureParser.extract_evidence(doc.tobytes()), [])

    def test_single_issuer_result_is_not_blocked(self):
        with pymupdf.open() as doc:
            p = doc.new_page()
            p.insert_text((50, 50), 'Alpha Limited\nConsolidated revenue was INR 100 crore for Q2 FY27.')
            self.assertTrue(AnnexureParser.extract_evidence(doc.tobytes()))

    def test_conflicting_margin_values_do_not_inherit_missing_scope(self):
        text = '\n'.join(['Achieved an EBITDA margin of 9.66% in Q2 FY27',
                          'Achieved an EBITDA margin of 10.20% in Q2 FY27',
                          'Achieved an EBITDA margin of 8.51% in H1 FY27'])
        points, reasons = select_result_points('', text)
        self.assertEqual(points, ['Achieved an EBITDA margin of 8.51% in H1 FY27'])
        self.assertEqual(reasons['conflicting_unresolved_margin'], 2)

    def test_accounting_balance_and_clipped_pbt_are_not_results(self):
        for point in ['Other current assets include unbilled revenue of 7638 lakh as at 30 September 2026.',
                      'Profit Before Tax (PBT): ₹213.16 million. a focus on lifecycle-led engagements, subscription-based',
                      'EBITDA margin expanded to 12.7% (-120 bps YoY): Lower on account of costs.']:
            with self.subTest(point=point):
                self.assertEqual(select_result_points('', point)[0], [])

    def test_valid_margin_decline_survives(self):
        point = 'EBITDA margin declined to 12.7% (-120 bps YoY).'
        self.assertEqual(select_result_points('', point)[0], [point])

    def test_flattened_scorecard_does_not_borrow_last_metric_value(self):
        point = 'Q2 FY27 Financial Performance Snapshot Revenue EBITDA Profit Before Tax Profit After Tax ₹ 332 Cr. ₹ 134 Cr. ₹ 111 Cr. ₹ 77 Cr.'
        self.assertEqual(select_result_points('', point)[0], [])

    def test_explicit_multi_metric_prose_survives(self):
        point = 'During the quarter, we reported consolidated revenue of ₹332.0 crore, EBITDA of ₹134 crore, and Profit After Tax of ₹77 crore.'
        self.assertEqual(select_result_points('', point)[0], [point])
