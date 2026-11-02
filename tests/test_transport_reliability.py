import unittest
from unittest.mock import MagicMock, patch
from types import SimpleNamespace
from curl_cffi.requests.exceptions import RequestException
from sources.pdf_download import download_pdf, PDFDownloadError
from sources.rss_response import parse_rss, FeedResponseError, MAX_FEED_BYTES
from sources.news_fetcher import NewsFetcher

PDF = b'%PDF-1.7\nexample body\n%%EOF\n'

class Client:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        status, chunks, headers = value
        for chunk in chunks:
            if isinstance(chunk, Exception):
                raise chunk
            if kwargs['content_callback'](chunk) != len(chunk):
                raise RequestException('write aborted', code=23)
        return SimpleNamespace(status_code=status, headers=headers)

class PDFTransportTests(unittest.TestCase):
    def fetch(self, client, **kwargs):
        return download_pdf('https://example.com/a.pdf', client=client, sleep=lambda _: None, **kwargs)

    def test_partial_timeout_restarts_clean_without_combining_responses(self):
        client = Client([(200, [b'%PDF-partial', RequestException('timeout', code=28)], {}), (200, [PDF], {})])
        self.assertEqual(self.fetch(client), PDF)
        self.assertEqual(len(client.calls), 2)

    def test_transient_http_failure_retries_once(self):
        client = Client([(503, [b'busy'], {}), (200, [PDF], {})])
        self.assertEqual(self.fetch(client), PDF)
        self.assertEqual(len(client.calls), 2)

    def test_repeated_transient_error_is_bounded(self):
        client = Client([RequestException('timeout', code=28)] * 3)
        with self.assertRaises(PDFDownloadError) as caught:
            self.fetch(client)
        self.assertEqual(caught.exception.attempts, 2)
        self.assertTrue(caught.exception.retryable)

    def test_access_denial_and_html_are_not_retried(self):
        for response, reason in [((403, [b'denied'], {}), 'PDF_HTTP_403'),
                                 ((200, [b'<html>not a PDF</html>'], {}), 'PDF_INVALID_CONTENT')]:
            client = Client([response])
            with self.assertRaises(PDFDownloadError) as caught:
                self.fetch(client)
            self.assertEqual(caught.exception.reason, reason)
            self.assertEqual(len(client.calls), 1)

    def test_size_limit_aborts_callback_before_accepting_excess(self):
        client = Client([(200, [b'%PDF-1234', b'x'*50], {})])
        with self.assertRaises(PDFDownloadError) as caught:
            self.fetch(client, max_bytes=10)
        self.assertEqual(caught.exception.reason, 'PDF_SIZE_LIMIT_EXCEEDED')
        self.assertEqual(len(client.calls), 1)

    def test_incomplete_pdf_is_not_accepted(self):
        client = Client([(200, [b'%PDF-truncated'], {}), (200, [PDF], {})])
        self.assertEqual(self.fetch(client), PDF)

    def test_retry_after_exceeding_budget_does_not_retry_early(self):
        client = Client([(429, [], {'Retry-After': '120'})])
        with self.assertRaises(PDFDownloadError):
            self.fetch(client, budget_seconds=45)
        self.assertEqual(len(client.calls), 1)

    def test_tls_failure_is_not_retried(self):
        client = Client([RequestException('TLS verification failed', code=60)])
        with self.assertRaises(PDFDownloadError):
            self.fetch(client)
        self.assertEqual(len(client.calls), 1)

    def test_archive_requires_exact_bse_live_path_and_404(self):
        client = Client([(404, [], {}), (200, [PDF], {})])
        url = 'https://www.bseindia.com/xml-data/corpfiling/AttachLive/a.pdf'
        self.assertEqual(download_pdf(url, client=client), PDF)
        self.assertEqual(client.calls[1][0], url.replace('AttachLive', 'AttachHis'))
        client = Client([(404, [], {})])
        with self.assertRaises(PDFDownloadError):
            self.fetch(client)
        self.assertEqual(len(client.calls), 1)

    def test_later_attempt_timeout_uses_remaining_total_budget(self):
        now = [0]
        class TimedClient(Client):
            def get(self, url, **kwargs):
                now[0] += 24 if not self.calls else 1
                return super().get(url, **kwargs)
        client = TimedClient([(503, [], {}), (200, [PDF], {})])
        download_pdf('https://example.com/a.pdf', client=client, clock=lambda: now[0],
                     sleep=lambda seconds: now.__setitem__(0, now[0] + seconds))
        self.assertEqual(client.calls[1][1]['timeout'], 20)

class RSSResponseTests(unittest.TestCase):
    def test_html_response_is_classified_without_logging_body(self):
        body = b'<!doctype html><html><head><title>Access denied</title></head></html>'
        with self.assertRaises(FeedResponseError) as caught:
            parse_rss(body, 'text/html', 200, 'https://example.com/feed')
        self.assertEqual(caught.exception.reason, 'RSS_ACCESS_CHALLENGE')
        self.assertNotIn('Access denied', str(caught.exception.diagnostics))
        self.assertEqual(caught.exception.diagnostics['body_kind'], 'html')

    def test_malformed_xml_is_not_silently_repaired(self):
        with self.assertRaises(FeedResponseError) as caught:
            parse_rss(b'<rss><channel><title>A & B</title></channel></rss>')
        self.assertEqual(caught.exception.reason, 'RSS_MALFORMED_XML')
        self.assertIn('parse_position', caught.exception.diagnostics)

    def test_wrong_schema_and_oversize_are_rejected(self):
        for data, reason in [(b'<items/>', 'RSS_UNEXPECTED_SCHEMA'),
                             (b'x'*(MAX_FEED_BYTES+1), 'RSS_SIZE_LIMIT_EXCEEDED')]:
            with self.assertRaises(FeedResponseError) as caught:
                parse_rss(data)
            self.assertEqual(caught.exception.reason, reason)

    def test_valid_empty_feed_is_distinct_from_html(self):
        root, metadata = parse_rss(b'<rss><channel/></rss>', 'application/rss+xml')
        self.assertEqual(root.tag, 'rss')
        self.assertEqual(metadata['root_tag'], 'rss')

    def test_backoff_preserves_outage_and_good_feed_then_recovers(self):
        fetcher = NewsFetcher()
        self.addCleanup(fetcher.session.close)
        bad = {'source': 'bad', 'url': 'https://example.com/bad'}
        good = {'source': 'good', 'url': 'https://example.com/good'}
        calls = []
        def fetch(feed):
            calls.append(feed['source'])
            if feed == bad and calls.count('bad') == 1:
                raise RuntimeError('invalid HTML response')
            return [{'id': feed['source']}]
        with patch('sources.news_fetcher.NEWS_FEEDS', [bad, good]), \
             patch.object(fetcher, 'fetch_feed', side_effect=fetch), \
             patch('sources.news_fetcher.time.monotonic', return_value=0) as clock:
            self.assertEqual(fetcher.fetch_all_feeds(), [{'id': 'good'}])
            clock.return_value = 1
            self.assertEqual(fetcher.fetch_all_feeds(), [{'id': 'good'}])
            self.assertEqual(fetcher.last_health[bad['url']]['status'], 'error')
            self.assertFalse(fetcher.last_health[bad['url']]['attempted'])
            self.assertEqual(calls.count('bad'), 1)
            clock.return_value = 31
            self.assertEqual(len(fetcher.fetch_all_feeds()), 2)
            self.assertEqual(fetcher.last_health[bad['url']]['status'], 'items')
            self.assertNotIn(bad['url'], fetcher._feed_backoff)
