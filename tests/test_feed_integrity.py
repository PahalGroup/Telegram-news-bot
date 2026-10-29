import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from sources.feed_utils import fresh_rss_date
from sources.nse_fetcher import NSEFetcher
from sources.rbi_fetcher import RBIFetcher
from sources.news_fetcher import NewsFetcher

class FeedIntegrityTests(unittest.TestCase):
    def test_missing_bad_naive_stale_future_dates_are_withheld(self):
        now = datetime(2026, 10, 29, 10, tzinfo=timezone.utc)
        for value in ["", "bad", "Thu, 29 Oct 2026 10:00:00", "Wed, 28 Oct 2026 10:00:00 +0000",
                      "Thu, 29 Oct 2026 11:00:00 +0000"]:
            with self.subTest(value=value):
                self.assertFalse(fresh_rss_date(value, 2700, now))
        self.assertTrue(fresh_rss_date("Thu, 29 Oct 2026 15:30:00 +0530", 2700, now))

    def test_nse_http_and_schema_errors_raise(self):
        fetcher = NSEFetcher()
        fetcher.session = MagicMock()
        fetcher._warm = True
        for code, data in [(403, []), (200, {"error": "unavailable"}), (200, ["invalid"])]:
            for method in [fetcher.fetch_announcements, fetcher.fetch_fii_dii]:
                with self.subTest(code=code, data=data, method=method.__name__):
                    fetcher.session.get.return_value.status_code = code
                    fetcher.session.get.return_value.json.return_value = data
                    with self.assertRaises(RuntimeError):
                        method()

    def test_rbi_html_success_response_is_error(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b"<html><body>Unavailable</body></html>"
        with patch("sources.rbi_fetcher.urllib.request.urlopen", return_value=response):
            with self.assertRaises(RuntimeError):
                RBIFetcher().fetch_press_releases()

    def test_partial_rss_failure_keeps_good_items_and_records_failure(self):
        fetcher = NewsFetcher()
        feeds = [{"source": "good", "url": "https://example.com/good"},
                 {"source": "bad", "url": "https://example.com/bad"}]
        def fetch(feed):
            if feed["source"] == "bad":
                raise RuntimeError("HTTP 403")
            return [{"title": "valid fresh article"}]
        with patch("sources.news_fetcher.NEWS_FEEDS", feeds), patch.object(fetcher, "fetch_feed", side_effect=fetch):
            self.assertEqual(len(fetcher.fetch_all_feeds()), 1)
        self.assertEqual(fetcher.last_health[feeds[1]["url"]]["status"], "error")
        self.assertEqual(fetcher.last_health[feeds[0]["url"]]["status"], "items")

    def test_rbi_naive_dates_use_explicit_indian_timezone_policy(self):
        from zoneinfo import ZoneInfo
        now = datetime(2026, 10, 29, 12, 30, tzinfo=timezone.utc)
        value = "Thu, 29 Oct 2026 17:45:00"
        self.assertFalse(fresh_rss_date(value, 2700, now))
        self.assertTrue(fresh_rss_date(value, 2700, now, naive_timezone=ZoneInfo("Asia/Kolkata")))

class BSEPartialFetchTests(unittest.TestCase):
    def response(self, items=None, code=200):
        response = MagicMock(status_code=code)
        response.json.return_value = {"Table": items or []}
        return response

    def test_later_page_failure_preserves_earlier_announcements(self):
        from sources.bse_fetcher import BSEFetcher
        row = {"SLONGNAME": "Example", "NEWSSUB": "Receipt of order", "NEWSID": "123",
               "NEWS_DT": datetime.now(timezone.utc).isoformat()}
        fetcher = BSEFetcher()
        with patch("sources.bse_fetcher.requests.get", side_effect=[
                self.response([row]), self.response(code=503), self.response()]):
            items = fetcher.fetch_announcements(skip_pdf=True)
        self.assertEqual([item["newsid"] for item in items], ["123"])
        self.assertIn("2", fetcher.last_health["page_errors"])

    def test_invalid_or_future_bse_dates_are_withheld(self):
        from sources.bse_fetcher import BSEFetcher
        from datetime import timedelta
        for date in ["", "bad", (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()]:
            row = {"SLONGNAME": "Example", "NEWSSUB": "Receipt of order", "NEWSID": "123", "NEWS_DT": date}
            with patch("sources.bse_fetcher.requests.get", return_value=self.response([row])):
                self.assertEqual(BSEFetcher().fetch_announcements(skip_pdf=True), [])
