import unittest
from sources.pdf_layout import join_wrapped_blocks
from sources.annexure_parser import AnnexureParser
from src.event_evidence import evaluate_event
import pymupdf

class WrappedBlockTests(unittest.TestCase):
    def test_full_width_continuation_is_joined(self):
        blocks = [[72, 100, 540, 113, "The company appointed Mr. Example Person as Executive Director of\n"],
                  [72, 119, 400, 132, "Mining with effect from 12.10.2026.\n"]]
        self.assertEqual(len(join_wrapped_blocks(blocks, 600)), 1)

    def test_columns_paragraph_gap_and_numbered_rows_do_not_join(self):
        first = [72, 100, 540, 113, "The company appointed Mr. Example Person as Executive Director of\n"]
        for second in [[320, 119, 540, 132, "Unrelated column"],
                       [72, 145, 540, 158, "Separate paragraph"],
                       [72, 119, 540, 132, "2. Another item"]]:
            self.assertEqual(len(join_wrapped_blocks([first, second], 600)), 2)
        self.assertEqual(len(join_wrapped_blocks([
            [72, 100, 280, 113, "Long narrow column text that must not join a different row of data"],
            [72, 119, 280, 132, "Other narrow row"]], 600)), 2)

    def test_pdf_wrapped_management_retains_effective_date(self):
        with pymupdf.open() as doc:
            page = doc.new_page()
            page.insert_text((72, 100), "Mr. Example Person has taken over the charge as Executive Director", fontsize=12)
            page.insert_text((72, 120), "of Mining with effect from 12.10.2026.", fontsize=12)
            evidence = AnnexureParser.extract_evidence(doc.tobytes())
        result = evaluate_event("MANAGEMENT_CHANGE", "EXAMPLE", "", "\n".join(e["text"] for e in evidence))
        self.assertTrue(result["publish"])
        self.assertIn("12.10.2026", str(result["summary_points"]))
