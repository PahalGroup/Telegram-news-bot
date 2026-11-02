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


class IPORecoveryTests(unittest.TestCase):
    SALE = 'As part of the IPO, Edelweiss Securities and Investments Private Limited (“ESIPL”), a wholly owned subsidiary of the Company has sold 72,07,207 equity shares by way of Offer for Sale, at the price of Rs. 1,665 per share.'

    def recover(self, blocks):
        from src.ipo_evidence import recover_ipo_sale_paragraphs
        return recover_ipo_sale_paragraphs(blocks, 600)

    def test_complete_cover_paragraph_survives_false_table_overlap(self):
        block = [72, 260, 548, 452, 'Other cover text\n\n' + self.SALE.replace('owned subsidiary', 'owned\nsubsidiary') + '\n\nTable follows']
        self.assertEqual(self.recover([block]), [self.SALE])

    def test_fragments_columns_and_negated_sale_are_not_reconstructed(self):
        for blocks in [
            [[72, 260, 548, 452, self.SALE.split('owned')[0]]],
            [[72, 260, 250, 452, self.SALE]],
            [[72, 260, 548, 452, self.SALE.replace('has sold', 'has not sold')]],
            [[72, 260, 548, 452, self.SALE.replace('owned subsidiary', 'owned\n\nsubsidiary')]],
        ]:
            with self.subTest(blocks=blocks):
                self.assertEqual(self.recover(blocks), [])


class LateOutputTests(unittest.TestCase):
    def test_unfinished_articles_and_multiyear_growth_are_withheld(self):
        for text in [
            'Revenue from Operations is ₹159.11 Crores in H1 FY27 compared to ₹81.55 Crores in H1 FY26, a',
            'Robust revenue growth of 10.1% p.a. during FY 2021-2026 with increasing',
            'Revenue grew 10.1% during FY 2021-2026.',
        ]:
            with self.subTest(text=text):
                self.assertEqual(select_result_points('Financial results', text)[0], [])

    def rating(self, title, description):
        return FilterEngine(config_path='/nonexistent/rules.json').evaluate_stage_2({
            'company': 'Example', 'category': 'CREDIT_RATING', 'source': 'BSE',
            'title': title, 'description': description})

    def test_rating_disclosure_label_is_not_a_rating_event(self):
        self.assertFalse(self.rating(
            'Reg 23(5)(d): Disclosure of details of any credit rating obtained by the InvIT and any change in such rating', '')['publish'])

    def test_prose_rating_requires_agency_grade_and_action(self):
        good = 'ICRA has reaffirmed the long-term credit rating at AA- with a Stable outlook.'
        self.assertEqual(self.rating('Credit rating', good)['summary_points'], [good])
        for text in ['ICRA has reaffirmed the credit rating.',
                     'The long-term credit rating was reaffirmed at AA-.',
                     'ICRA may upgrade the credit rating to AA-.']:
            with self.subTest(text=text):
                self.assertFalse(self.rating('Credit rating', text)['publish'])

    def test_adjacent_fiscal_year_comparison_is_still_eligible(self):
        text = 'Consolidated revenue grew 10% in FY 2025-2026.'
        self.assertEqual(select_result_points('Financial results', text)[0], [text])
