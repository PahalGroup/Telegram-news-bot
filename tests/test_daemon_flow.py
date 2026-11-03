"""End-to-end daemon cycles on a fixed clock: quiet hours, pacing, deals, staleness."""
import json, unittest
from concurrent.futures import Future
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from src.daemon import RealtimeDaemon
from src.scheduler import IST

FIX = Path(__file__).parent / "fixtures" / "sources"


class Inline:
    def submit(self, fn, *a, **k):
        f = Future()
        f.set_result(fn(*a, **k))
        return f


def make(now, deals=(), rbi=()):
    d = RealtimeDaemon(dry_run=True)
    clock = {"now": now}
    d.clock = lambda: clock["now"]
    d._source_pool, d._source_pending = Inline(), {}
    for obj, name in ((d.bse, "fetch_announcements"), (d.nse, "fetch_announcements"),
                      (d.news, "fetch_all_feeds"), (d.liveblog, "fetch_live_flashes"),
                      (d.rbi, "fetch_speeches"), (d.nse, "fetch_past_issues")):
        setattr(obj, name, lambda *a, **k: [])
    d.nse.fetch_large_deals = lambda: list(deals)
    d.rbi.fetch_press_releases = lambda: list(rbi)
    d._fetch_pib = lambda: []
    d.check_fii_dii_routine = lambda: None
    d.check_morning_pack = lambda now: None
    return d, clock


def deal(sym, date="03-Nov-2026"):
    return {"kind": "BLOCK DEAL", "symbol": sym, "company": f"{sym.title()} Limited", "client": "Alpha Fund",
            "side": "BUY", "date": date, "qty": 2_000_000.0, "value": 1.2e9, "value_cr": 120.0, "price": 600.0}


class DaemonFlow(unittest.TestCase):
    def cycles(self, d, clock, n, step=12):
        for _ in range(n):
            d.process_cycle()
            clock["now"] += timedelta(seconds=step)

    def test_deal_flows_through_template_e(self):
        d, clock = make(datetime(2026, 11, 3, 17, 5, tzinfo=IST), deals=[deal("ACME")])
        self.cycles(d, clock, 2)
        self.assertEqual(len(d.dry_posts), 1)
        self.assertIn("<b>Acme Ltd – BLOCK DEAL</b>", d.dry_posts[0]["text"])

    def test_previous_session_deals_are_not_replayed(self):
        d, clock = make(datetime(2026, 11, 4, 9, 0, tzinfo=IST), deals=[deal("ACME")])
        self.cycles(d, clock, 3)
        self.assertEqual(d.dry_posts, [])

    def test_posts_are_paced_20s_apart(self):
        d, clock = make(datetime(2026, 11, 3, 17, 5, tzinfo=IST), deals=[deal(s) for s in ("ACME", "BETA", "GAMA")])
        self.cycles(d, clock, 12, step=5)
        times = [datetime.fromisoformat(p["at"]) for p in d.dry_posts]
        self.assertEqual(len(times), 3)
        self.assertTrue(all((b - a).total_seconds() >= 20 for a, b in zip(times, times[1:])))

    def test_nothing_released_in_quiet_hours(self):
        d, clock = make(datetime(2026, 11, 3, 23, 45, tzinfo=IST), deals=[deal("ACME")])
        self.cycles(d, clock, 30, step=60)          # 23:45 -> 00:15
        self.assertEqual(d.dry_posts, [])

    def test_stale_item_dropped(self):
        rbi = [{"title": "RBI announces 7-day VRR auction of ₹50,000 crore", "description": "Notified amount ₹50,000 crore on 03 Nov 2026",
                "link": "https://rbi.org.in/x", "pub_date": "Tue, 03 Nov 2026 10:00:00"}]
        d, clock = make(datetime(2026, 11, 3, 11, 0, tzinfo=IST), rbi=rbi)   # 60 min old
        self.cycles(d, clock, 3)
        self.assertEqual(d.dry_posts, [])


if __name__ == "__main__":
    unittest.main()
