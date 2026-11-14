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
