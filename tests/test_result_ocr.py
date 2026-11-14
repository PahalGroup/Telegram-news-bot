"""Local tesseract on a synthetic scanned statement; failed checks are withheld."""
import unittest
from pathlib import Path
import pymupdf

from sources.result_ocr import parse_ocr_text, parse_scanned_document, test_mode as ocr_test_mode


CLEAN = """
MEHAI EXAMPLE LIMITED
Unaudited Consolidated Financial Results for the quarter and half year ended 30 September 2026
(Rs. in Crore)

Particulars                  Quarter ended 30 September 2026  Quarter ended 30 June 2026  Quarter ended 30 September 2025  Half year ended 30 September 2026
Revenue from operations      100.00                           90.00                       80.00                            190.00
Other income                 5.00                             4.00                        3.00                             9.00
Total income                 105.00                           94.00                       83.00                            199.00
Profit before tax            20.00                            18.00                       15.00                            38.00
Tax expense                  5.00                             4.50                        3.75                             9.50
Profit after tax             15.00                            13.50                       11.25                            28.50
"""


def image_only_pdf(text) -> bytes:
    with pymupdf.open() as src:
        page = src.new_page(width=900, height=500)
        page.insert_text((30, 40), text, fontsize=8, fontname="helv")
        pix = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
    with pymupdf.open() as out:
        outpage = out.new_page(width=pix.width, height=pix.height)
        outpage.insert_image(outpage.rect, stream=pix.tobytes("png"))
        return out.tobytes()


class OcrParseTests(unittest.TestCase):
    def test_clean_text_passes_sum_checks(self):
        card, reason = parse_ocr_text(CLEAN)
        self.assertTrue(card, reason)
        self.assertEqual(card["scope"], "CONSOLIDATED")
        self.assertEqual(card["metrics"]["revenue"]["cur"], 100.0)
        self.assertEqual(card["metrics"]["pat"]["cur"], 15.0)
        self.assertEqual(card["metrics"]["revenue"]["qoq"], 90.0)
        self.assertEqual(card["metrics"]["pat"]["yoy"], 11.25)

    def test_failed_income_check_is_withheld(self):
        broken = CLEAN.replace("Total income                 105.00", "Total income                 80.00")
        card, reason = parse_ocr_text(broken)
        self.assertIsNone(card)
        self.assertEqual(reason, "OCR_CHECK_TOTAL_INCOME")

    def test_failed_pat_check_is_withheld(self):
        broken = CLEAN.replace("Profit after tax             15.00", "Profit after tax             9.00")
        card, reason = parse_ocr_text(broken)
        self.assertIsNone(card)
        self.assertEqual(reason, "OCR_CHECK_PBT_MINUS_TAX")

    def test_half_year_below_quarter_is_withheld(self):
        broken = CLEAN.replace("190.00", "50.00", 1).replace("199.00", "59.00", 1)
        card, reason = parse_ocr_text(broken)
        self.assertIsNone(card)
        self.assertEqual(reason, "OCR_CHECK_QUARTER_VS_HALF")

    def test_image_only_pdf_round_trip(self):
        data = image_only_pdf(CLEAN)
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            self.assertEqual(doc[0].get_text().strip(), "")
            card, reason = parse_scanned_document(doc)
        self.assertTrue(card, reason)
        self.assertAlmostEqual(card["metrics"]["revenue"]["cur"], 100.0, places=1)
        self.assertAlmostEqual(card["metrics"]["pat"]["cur"], 15.0, places=1)
        self.assertEqual(card["scope"], "CONSOLIDATED")

    def test_mode_flag_defaults_off(self):
        self.assertFalse(ocr_test_mode())


    def test_amounts_in_rs_crs_is_crore(self):
        text = CLEAN.replace("(Rs. in Crore)", "Amounts\nin Rs. Crs")
        card, reason = parse_ocr_text(text)
        self.assertTrue(card, reason)
        self.assertEqual(card["unit"], "crore")
        self.assertEqual(card["metrics"]["revenue"]["cur"], 100.0)

    def test_serial_column_is_dropped(self):
        text = """
EXAMPLE LIMITED
Unaudited Standalone Financial Results for the quarter ended 30 September 2026
(Rs. in Lakhs)
Particulars  Quarter ended 30 September 2026  Quarter ended 30 June 2026  Quarter ended 30 September 2025
1 Revenue from operations  48761.40  45051.93  39249.98
2 Other income  989.68  1072.11  1051.56
3 Total income  49751.08  46124.04  40301.54
7 Profit before tax  3586.11  3760.88  3099.37
8 Tax expense  925.13  961.64  779.17
9 Profit after tax  2660.98  2799.24  2320.20
"""
        card, reason = parse_ocr_text(text)
        self.assertTrue(card, reason)
        self.assertAlmostEqual(card["metrics"]["revenue"]["cur"], 487.614, places=3)
        self.assertAlmostEqual(card["metrics"]["pat"]["cur"], 26.6098, places=3)

    def test_auditor_notes_page_is_skipped(self):
        notes = (
            "Independent Auditor's Review Report\n"
            "We have reviewed the accompanying statement of unaudited financial results of "
            "Example Limited for the quarter ended 30 September 2026.\n"
            "We draw your attention to the fact that the Company is a 10% profit partner "
            "in a joint venture. Basis for opinion: our review was conducted as required.\n"
            "The statement of financial results is the responsibility of the management.\n"
        ) * 3
        with pymupdf.open() as doc:
            page = doc.new_page()
            page.insert_text((40, 40), notes, fontsize=8, fontname="helv")
            page2 = doc.new_page()
            page2.insert_text((40, 40), CLEAN, fontsize=8, fontname="helv")
            data = doc.tobytes()
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            card, reason = parse_scanned_document(doc)
        self.assertTrue(card, reason)
        self.assertEqual(card["page"], 2)
        self.assertEqual(card["metrics"]["revenue"]["cur"], 100.0)
