import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from src.daemon import RealtimeDaemon
from src.scheduler import IST


def daemon_with(last_cycle_at):
    d = RealtimeDaemon.__new__(RealtimeDaemon)
    d.state = MagicMock()
    d.state.last_cycle_at = last_cycle_at
    return d


class BacklogGuard(unittest.TestCase):
    now = datetime(2026, 11, 3, 22, 0, tzinfo=IST)

    def test_fresh_start_treats_older_than_20_min_as_backlog(self):
        d = daemon_with(None)
        self.assertEqual(d._backlog_cutoff(self.now), self.now - timedelta(minutes=20))
        # the cutoff is fixed for the run, not sliding
        self.assertEqual(d._backlog_cutoff(self.now + timedelta(hours=1)), self.now - timedelta(minutes=20))

    def test_long_gap_since_last_cycle_is_a_fresh_start(self):
        d = daemon_with((self.now - timedelta(hours=9)).isoformat())
        self.assertIsNotNone(d._backlog_cutoff(self.now))

    def test_chained_shift_is_not_a_fresh_start(self):
        d = daemon_with((self.now - timedelta(minutes=3)).isoformat())
        self.assertIsNone(d._backlog_cutoff(self.now))

    def test_backlog_items_are_marked_seen_not_composed(self):
        d = daemon_with(None)
        d.cycle_count = 0
        d.total_dispatched = 0
        d.state.pending_items = None
        d.state.is_seen.return_value = False
        old = {"id": "BSE_old", "source": "BSE", "published_at": (self.now - timedelta(minutes=45)).isoformat()}
        new = {"id": "BSE_new", "source": "BSE", "published_at": (self.now - timedelta(minutes=2)).isoformat()}
        d.poll_all_sources = lambda **k: [old, new]
        d.prepare_items = lambda items, **k: items
        d.clock = lambda: self.now
        d.outbox = MagicMock(); d.outbox.has.return_value = False
        d._compose = MagicMock(); d._trace = MagicMock()
        for f in ("check_morning_pack", "check_ipo_listings", "check_fii_dii_routine"):
            setattr(d, f, MagicMock())
        d.release_due = MagicMock(return_value=0)
        d.process_cycle()
        d.state.mark_seen.assert_called_once_with("BSE_old", "BSE")
        self.assertEqual([c.args[0]["id"] for c in d._compose.call_args_list], ["BSE_new"])
        self.assertEqual(d.state.last_cycle_at, self.now.isoformat())


class BsePaging(unittest.TestCase):
    def test_stops_after_page_one_when_it_overlaps_last_poll(self):
        from sources import bse_fetcher
        f = bse_fetcher.BSEFetcher()
        now = datetime.now(IST).replace(tzinfo=None)
        row = lambda i: {"NEWSID": str(i), "SLONGNAME": "X Ltd", "NEWSSUB": "X Ltd - 1 - Update", "HEADLINE": "",
                         "NEWS_DT": now.isoformat(), "ATTACHMENTNAME": ""}
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"Table": [row(1), row(2)]}
        with patch.object(bse_fetcher.requests, "get", return_value=resp) as get:
            f.fetch_announcements(skip_pdf=True)
            self.assertEqual(get.call_count, 3)        # first poll: no history, read all pages
            get.reset_mock()
            f.fetch_announcements(skip_pdf=True)
            self.assertEqual(get.call_count, 1)        # page 1 reached known items
