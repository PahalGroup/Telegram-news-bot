import unittest
from concurrent.futures import Future
from unittest.mock import MagicMock
from sources.annexure_parser import AnnexureParser, PDFExtractionError
from src.daemon import RealtimeDaemon

class PDFFailureTests(unittest.TestCase):
    def test_http_error_is_not_an_unsupported_document(self):
        session = MagicMock()
        session.get.return_value.status_code = 503
        with self.assertRaises(PDFExtractionError):
            AnnexureParser.extract_annexure_bullets("https://example.com/a.pdf", session)

    def test_bse_archive_fallback_only_after_404(self):
        session = MagicMock()
        session.get.side_effect = [MagicMock(status_code=404), MagicMock(status_code=503)]
        with self.assertRaises(PDFExtractionError):
            AnnexureParser.extract_annexure_bullets(
                "https://www.bseindia.com/xml-data/corpfiling/AttachLive/abc.pdf", session)
        self.assertIn("/AttachHis/abc.pdf", session.get.call_args.args[0])

    def test_worker_failure_counts_as_error_and_stays_retryable(self):
        daemon = RealtimeDaemon.__new__(RealtimeDaemon)
        future = Future()
        future.set_exception(PDFExtractionError("timeout"))
        daemon._pdf_pending = {"event": ({"id": "event"}, future)}
        daemon._pdf_retry = {}
        daemon._pdf_pool = None
        daemon.total_errors = 0
        daemon.state = MagicMock()
        self.assertEqual(daemon.prepare_items([]), [])
        self.assertEqual(daemon.total_errors, 1)
        self.assertEqual(daemon._pdf_outcomes["errors"], 1)
        self.assertIn("event", daemon._pdf_retry)
        daemon.state.mark_seen.assert_not_called()
