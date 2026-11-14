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

from sources.result_grid import parse_word_boxes, unit_from_text, run_sum_checks, close, TOL_ABS, TOL_REL


class TableAwareTests(unittest.TestCase):
    def test_garbled_unit_lines(self):
        self.assertEqual(unit_from_text("Statement of results\n(@ in lakhs)\nRevenue from operations 1.00"), "lakh")
        self.assertEqual(unit_from_text("Statement\n(Armount in Lakhs.)\nRevenue"), "lakh")
        self.assertEqual(unit_from_text("Particulars\n(INR in lakhs exceptas stated)\nRevenue"), "lakh")
        self.assertEqual(unit_from_text("Amounts\nin Rs. Crs\nRevenue from operations"), "crore")

    def test_word_grid_recovers_columns_and_passes_checks(self):
        # Synthetic boxes in a 6-column SEBI layout (cur, qoq, yoy, half, half-yoy, year).
        def row(y, label, nums, x0=20):
            words = [(x0, y, x0 + 8 * len(label), y + 10, label)]
            x = 280
            for n in nums:
                t = f"{n:.2f}"
                words.append((x, y, x + 50, y + 10, t))
                x += 70
            return words
        words = [
            (20, 10, 400, 20, "Unaudited Consolidated Financial Results"),
            (20, 24, 140, 34, "(Rs. in Crore)"),
            (280, 36, 340, 46, "30.09.2026"),
            (350, 36, 410, 46, "30.06.2026"),
            (420, 36, 480, 46, "30.09.2025"),
            (490, 36, 550, 46, "30.09.2026"),
        ]
        for y, label, nums in (
            (60, "Revenue from operations", (100, 90, 80, 190)),
            (75, "Other income", (5, 4, 3, 9)),
            (90, "Total income", (105, 94, 83, 199)),
            (105, "Profit before tax", (20, 18, 15, 38)),
            (120, "Tax expense", (5, 4.5, 3.75, 9.5)),
            (135, "Profit after tax", (15, 13.5, 11.25, 28.5)),
        ):
            words.extend(row(y, label, nums))
        card, reason = parse_word_boxes(words)
        self.assertTrue(card, reason)
        self.assertEqual(card["metrics"]["revenue"]["cur"], 100.0)
        self.assertEqual(card["metrics"]["pat"]["cur"], 15.0)
        self.assertEqual(card["metrics"]["revenue"]["qoq"], 90.0)
        self.assertEqual(card["source"], "ocr-grid")

    def test_numbers_on_next_line_are_merged(self):
        words = [
            (20, 10, 400, 20, "Unaudited Standalone Financial Results for the quarter ended 30 September 2026"),
            (20, 24, 140, 34, "(Rs. in Crore)"),
            (20, 60, 200, 70, "Revenue from operations"),
            (280, 80, 330, 90, "100.00"),
            (350, 80, 400, 90, "90.00"),
            (420, 80, 470, 90, "80.00"),
            (20, 100, 160, 110, "Profit after tax"),
            (280, 120, 330, 130, "15.00"),
            (350, 120, 400, 130, "13.50"),
            (420, 120, 470, 130, "11.25"),
        ]
        card, reason = parse_word_boxes(words)
        self.assertTrue(card, reason)
        self.assertEqual(card["metrics"]["revenue"]["cur"], 100.0)
        self.assertEqual(card["metrics"]["pat"]["cur"], 15.0)

    def test_grid_still_withholds_failed_income_check(self):
        def row(y, label, nums):
            words = [(20, y, 20 + 8 * len(label), y + 10, label)]
            x = 280
            for n in nums:
                words.append((x, y, x + 50, y + 10, f"{n:.2f}"))
                x += 70
            return words
        words = [
            (20, 10, 400, 20, "Unaudited Standalone Financial Results"),
            (20, 24, 140, 34, "(Rs. in Crore)"),
        ]
        words.extend(row(60, "Revenue from operations", (100, 90, 80)))
        words.extend(row(75, "Other income", (5, 4, 3)))
        words.extend(row(90, "Total income", (80, 94, 83)))  # broken: 100+5 != 80
        words.extend(row(105, "Profit after tax", (15, 13.5, 11.25)))
        card, reason = parse_word_boxes(words)
        self.assertIsNone(card)
        self.assertEqual(reason, "OCR_CHECK_TOTAL_INCOME")

    def test_sum_check_tolerances_unchanged(self):
        self.assertEqual(TOL_ABS, 1.0)
        self.assertEqual(TOL_REL, 0.002)
        self.assertTrue(close(100.0, 100.2))
        self.assertFalse(close(100.0, 101.1))

    def test_net_profit_forthe_period_and_serial(self):
        """OCR often prints 'forthe' and a leading '1' before the revenue label."""
        def row(y, label, nums):
            words = [(20, y, 20 + 7 * len(label), y + 10, label)] if not label[0].isdigit() else []
            if label[:2].isdigit() or (label and label[0].isdigit()):
                # "1 Revenue from operations"
                words = [(20, y, 28, y + 10, "1"), (36, y, 220, y + 10, label.split(" ", 1)[1])]
            x = 280
            for n in nums:
                words.append((x, y, x + 50, y + 10, f"{n:.2f}"))
                x += 70
            return words
        words = [
            (20, 10, 400, 20, "Unaudited Standalone Financial Results"),
            (20, 24, 140, 34, "(Rs. in Crore)"),
        ]
        words.extend(row(60, "1 Revenue from operations", (100, 90, 80)))
        words.extend([(20, 90, 220, 100, "VIL Net Profit forthe period"),
                      (280, 90, 330, 100, "15.00"), (350, 90, 400, 100, "13.50"),
                      (420, 90, 470, 100, "11.25")])
        from sources.result_grid import parse_word_boxes
        card, reason = parse_word_boxes(words)
        self.assertTrue(card, reason)
        self.assertEqual(card["metrics"]["revenue"]["cur"], 100.0)
        self.assertEqual(card["metrics"]["pat"]["cur"], 15.0)
