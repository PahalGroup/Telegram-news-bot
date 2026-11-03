import unittest
from src.event_evidence import evaluate_event
from sources.financial_tables import table_evidence

class EvidenceTests(unittest.TestCase):
    def event(self, category, text):
        return evaluate_event(category, 'EXAMPLE', 'Corporate announcement', text)

    def test_order_preserves_currency_and_negation(self):
        text = 'Example received a contract worth USD 25 million from Client Limited.'
        result = self.event('ORDER_WIN', text)
        self.assertTrue(result['publish'])
        self.assertEqual(result['summary_points'], [text])
        self.assertNotIn('₹', str(result))
        self.assertFalse(self.event('ORDER_WIN', 'Example has not received an order worth INR 25 crore.')['publish'])

    def test_order_requires_award_not_tender(self):
        self.assertFalse(self.event('ORDER_WIN', 'Example submitted a tender for a contract worth INR 25 crore.')['publish'])

    def test_late_order_survives_background(self):
        text = 'The board approved routine administration. '*9
        text += 'Example received an order for 50 MW capacity from Utility Limited.'
        self.assertTrue(self.event('ORDER_WIN', text)['publish'])

    def test_financing_requires_amount_and_action(self):
        self.assertFalse(self.event('FINANCING', 'The company discussed a loan facility with the bank.')['publish'])
        text = 'The board approved a loan facility of INR 81 crore from Example Bank.'
        self.assertEqual(self.event('FINANCING', text)['summary_points'], [text])

    def test_management_requires_person_role_action(self):
        self.assertFalse(self.event('MANAGEMENT_CHANGE', 'Signed by Mr. Example Person, Chief Financial Officer.')['publish'])
        text = 'Mr. Example Person has resigned as Chief Financial Officer effective October 30, 2026.'
        self.assertEqual(self.event('MANAGEMENT_CHANGE', text)['summary_points'], [text])

    def test_table_keeps_unit_period_scope_negative(self):
        rows = [['Particulars', 'Quarter ended 30-09-2026', 'Quarter ended 30-09-2025'],
                ['Revenue from operations', '1,234.50', '987.60'], ['Net profit', '(12.50)', '-8.10']]
        facts = table_evidence(rows, 'Consolidated results (Rs. in lakhs)', 3)
        self.assertEqual(len(facts), 4)
        self.assertEqual(facts[2]['value'], '(12.50)')
        self.assertEqual(facts[3]['value'], '-8.10')
        self.assertEqual(facts[0]['unit'], 'lakh')
        self.assertEqual(facts[0]['scope'], 'consolidated')
        self.assertEqual(facts[0]['page'], 3)
        self.assertIn('2025', facts[3]['period'])
        self.assertEqual(table_evidence(rows, 'Results', 1), [])
        self.assertEqual(table_evidence(rows, 'Standalone and Consolidated Rs. in lakhs', 1), [])

    def test_merged_headers_not_inferred(self):
        rows = [['Particulars', 'Quarter ended', None], ['Date', '30-09-2026', '30-09-2025'], ['Net profit', '12', '10']]
        self.assertEqual(table_evidence(rows, 'Standalone Rs. in crores', 1), [])

class PDFIntegrationTests(unittest.TestCase):
    def test_explicit_table_is_extracted_with_page_evidence(self):
        import pymupdf
        from sources.annexure_parser import AnnexureParser
        with pymupdf.open() as doc:
            page = doc.new_page()
            page.insert_text((40, 40), 'Standalone results (INR in lakhs)', fontsize=10)
            xs, ys = [40, 230, 520], [70, 110, 150, 190]
            for x in xs: page.draw_line((x, ys[0]), (x, ys[-1]))
            for y in ys: page.draw_line((xs[0], y), (xs[-1], y))
            rows = [('Particulars', 'Quarter ended 30-09-2026'),
                    ('Revenue from operations', '1,234.50'), ('Net profit', '(12.50)')]
            for i, row in enumerate(rows):
                for j, cell in enumerate(row): page.insert_text((xs[j]+5, ys[i]+20), cell, fontsize=9)
            facts = AnnexureParser.extract_evidence(doc.tobytes())
        tables = [f for f in facts if f.get('method') == 'explicit_table']
        self.assertEqual(len(tables), 2)
        self.assertEqual(tables[1]['value'], '(12.50)')
        self.assertEqual(tables[1]['page'], 1)

    def test_disclosure_table_prose_is_not_lost(self):
        import pymupdf
        from sources.annexure_parser import AnnexureParser
        with pymupdf.open() as doc:
            page = doc.new_page()
            for x in [40, 180, 550]: page.draw_line((x, 70), (x, 170))
            for y in [70, 120, 170]: page.draw_line((40, y), (550, y))
            page.insert_text((45, 90), 'Particulars', fontsize=9)
            page.insert_text((185, 90), 'Details', fontsize=9)
            page.insert_text((45, 140), 'Contract', fontsize=9)
            page.insert_text((185, 140), 'Company received an order worth USD 25 million from Client Ltd.', fontsize=9)
            facts = AnnexureParser.extract_evidence(doc.tobytes())
        self.assertTrue(any('USD 25 million' in f['text'] for f in facts))
