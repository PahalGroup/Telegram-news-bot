import unittest
import pymupdf
from sources.financial_tables import resolve_merged_headers, table_evidence
from sources.annexure_parser import AnnexureParser

class MergedHeaderTests(unittest.TestCase):
    def setUp(self):
        self.rows = [['Particulars', 'Quarter ended', None, 'Year ended'],
                     [None, '30-09-2026', '30-09-2025', '31-03-2026'],
                     ['Net profit', '(12.50)', '10.25', '50.00']]
        self.boxes = [
            [(0,0,100,40), (100,0,300,20), None, (300,0,400,20)],
            [None, (100,20,200,40), (200,20,300,40), (300,20,400,40)],
            [(0,40,100,60),(100,40,200,60),(200,40,300,60),(300,40,400,60)]]

    def test_actual_span_resolves_quarter_and_year(self):
        rows = resolve_merged_headers(self.rows, self.boxes)
        facts = table_evidence(rows, 'Standalone INR in lakhs', 2)
        self.assertEqual(len(facts), 3)
        self.assertIn('Quarter ended', facts[1]['period'])
        self.assertIn('Year ended', facts[2]['period'])
        self.assertEqual(facts[0]['value'], '(12.50)')

    def test_blank_cell_without_spanning_rectangle_not_guessed(self):
        self.boxes[0][1] = (100,0,200,20)
        self.assertEqual(resolve_merged_headers(self.rows, self.boxes), self.rows)

    def test_two_period_dates_in_column_rejected(self):
        self.rows[1][1] += ' 30-06-2026'
        self.assertEqual(resolve_merged_headers(self.rows, self.boxes), self.rows)

    def test_actual_pdf_merged_header(self):
        with pymupdf.open() as doc:
            p = doc.new_page()
            p.insert_text((40,40), 'Consolidated INR in lakhs')
            for y in [70,110,150,190]: p.draw_line((40,y),(550,y))
            for x in [40,230,550]: p.draw_line((x,70),(x,190))
            p.draw_line((390,110),(390,190))
            p.insert_text((45,90),'Particulars',fontsize=9)
            p.insert_text((235,90),'Quarter ended',fontsize=9)
            p.insert_text((235,130),'30-09-2026',fontsize=9)
            p.insert_text((395,130),'30-09-2025',fontsize=9)
            p.insert_text((45,170),'Net profit',fontsize=9)
            p.insert_text((235,170),'(12.50)',fontsize=9)
            p.insert_text((395,170),'10.25',fontsize=9)
            facts = [f for f in AnnexureParser.extract_evidence(doc.tobytes()) if f.get('method')=='explicit_table']
        self.assertEqual(len(facts),2)
        self.assertEqual(facts[0]['value'],'(12.50)')
        self.assertIn('2025',facts[1]['period'])
