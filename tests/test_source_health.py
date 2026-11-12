import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from sources.bse_fetcher import BSEFetcher
from sources.liveblog_fetcher import LiveBlogFetcher

class SourceHealthTests(unittest.TestCase):
    def test_bse_access_denied_is_not_empty_success(self):
        with patch('sources.bse_fetcher.requests.get', return_value=MagicMock(status_code=403)), \
             patch('sources.bse_fetcher.time.sleep'):
            with self.assertRaises(RuntimeError):
                BSEFetcher().fetch_announcements(skip_pdf=True)

    def test_bse_unexpected_schema_is_not_empty_success(self):
        response = MagicMock(status_code=200)
        response.json.return_value = {'error': 'unavailable'}
        with patch('sources.bse_fetcher.requests.get', return_value=response), \
             patch('sources.bse_fetcher.time.sleep'):
            with self.assertRaises(RuntimeError):
                BSEFetcher().fetch_announcements(skip_pdf=True)

    def test_bse_empty_table_is_valid(self):
        response = MagicMock(status_code=200)
        response.json.return_value = {'Table': []}
        with patch('sources.bse_fetcher.requests.get', return_value=response):
            self.assertEqual(BSEFetcher().fetch_announcements(skip_pdf=True), [])

    def test_liveblog_access_denied_is_not_empty_success(self):
        fetcher = LiveBlogFetcher()
        fetcher.session = MagicMock()
        fetcher.session.get.return_value.status_code = 403
        with self.assertRaises(RuntimeError):
            fetcher.fetch_live_flashes()

    def test_liveblog_offset_and_utc_represent_same_time(self):
        now = datetime(2026, 10, 28, 10, 0, tzinfo=timezone.utc)
        for value in ['2026-10-28T10:00:00Z', '2026-10-28T15:30:00+05:30', '2026-10-28T15:30:00']:
            self.assertTrue(LiveBlogFetcher.is_fresh(value, now))
        for value in ['', 'bad', '2026-10-28T09:00:00Z', '2026-10-28T11:00:00Z']:
            self.assertFalse(LiveBlogFetcher.is_fresh(value, now))


class BSERetryTests(unittest.TestCase):
    def test_timeouts_are_retried_then_succeed(self):
        from curl_cffi.requests.exceptions import Timeout
        ok = MagicMock(status_code=200)
        ok.json.return_value = {'Table': []}
        calls = {'n': 0}
        def flaky(*args, **kwargs):
            calls['n'] += 1
            if calls['n'] < 3:
                raise Timeout('timed out')
            return ok
        with patch('sources.bse_fetcher.requests.get', side_effect=flaky), \
             patch('sources.bse_fetcher.time.sleep'):
            self.assertEqual(BSEFetcher().fetch_announcements(skip_pdf=True), [])
        self.assertEqual(calls['n'], 5)  # page 1 retried twice, then pages 2 and 3

    def test_invalid_json_is_retried(self):
        import json
        bad = MagicMock(status_code=200)
        bad.json.side_effect = json.JSONDecodeError('Expecting value', 'x', 0)
        ok = MagicMock(status_code=200)
        ok.json.return_value = {'Table': []}
        with patch('sources.bse_fetcher.requests.get', side_effect=[bad, ok, ok, ok]), \
             patch('sources.bse_fetcher.time.sleep'):
            # page 1 retries once then succeeds; pages 2-3 also called
            self.assertEqual(BSEFetcher().fetch_announcements(skip_pdf=True), [])
