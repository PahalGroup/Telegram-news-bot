import unittest
from unittest.mock import patch
import pymupdf
from sources.ocr_review import inspect_scans, parse_tsv

class OCRReviewTests(unittest.TestCase):
    def blank(self):
        with pymupdf.open() as doc:
            doc.new_page()
            return doc.tobytes()

    def test_confidence_is_reported_not_corrected(self):
        tsv = 'block_num\tpar_num\tline_num\tconf\ttext\n1\t1\t1\t96\tProfit\n1\t1\t1\t52\t(12.5O)\n'
        parsed = parse_tsv(tsv)
        self.assertEqual(parsed['text'], 'Profit (12.5O)')
        self.assertEqual(parsed['low_confidence_words'], 1)

    def test_missing_engine_never_claims_success(self):
        with patch('sources.ocr_review.shutil.which', return_value=None):
            report = inspect_scans(self.blank())
        self.assertFalse(report['publishable'])
        self.assertEqual(report['pages'][0]['status'], 'ocr_unavailable')

    def test_timeout_never_produces_publishable_data(self):
        import subprocess
        with patch('sources.ocr_review.shutil.which', return_value='/usr/bin/tesseract'), patch(
            'sources.ocr_review.subprocess.run', side_effect=subprocess.TimeoutExpired('tesseract', 20)):
            report = inspect_scans(self.blank())
        self.assertEqual(report['pages'][0]['status'], 'ocr_timeout')
        self.assertFalse(report['pages'][0]['publishable'])

    def test_page_budget(self):
        with pymupdf.open() as doc:
            for _ in range(7): doc.new_page()
            data = doc.tobytes()
        with patch('sources.ocr_review.shutil.which', return_value=None):
            report = inspect_scans(data)
        self.assertEqual(len(report['pages']), 5)
        self.assertEqual(report['pages_not_inspected'], 2)
