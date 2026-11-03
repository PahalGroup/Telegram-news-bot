"""Exercise actual spawned workers, not a mocked executor."""
import unittest
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context
import pymupdf
from sources.annexure_parser import AnnexureParser


class PDFProcessTests(unittest.TestCase):
    def test_independent_documents_keep_their_own_facts(self):
        inputs = []
        for index in range(6):
            text = f'Company Example{index} reported net profit of INR {100 + index} crore for Q2 FY27.'
            with pymupdf.open() as doc:
                page = doc.new_page()
                page.insert_text((40, 60), text, fontsize=10)
                inputs.append((text, doc.tobytes()))
        with ProcessPoolExecutor(max_workers=3, mp_context=get_context('spawn')) as pool:
            futures = [pool.submit(AnnexureParser.extract_evidence, data) for _, data in inputs]
            for (expected, _), future in zip(inputs, futures):
                evidence = future.result(timeout=30)
                self.assertEqual([row['text'] for row in evidence], [expected])

