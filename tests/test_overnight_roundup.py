"""Synthetic overnight-roundup tests. No live Telegram, no network, no real PDFs."""
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock

from src.scheduler import IST
from src import morning_pack, overnight_roundup
from src.state_store import StateStore


def at(h, m=0, day=14, month=11, year=2026):
    return datetime(year, month, day, h, m, tzinfo=IST)


def filing(**kw):
    item = {
        "id": kw.pop("id", "NSE_EXAMPLE_1"),
        "source": kw.pop("source", "NSE"),
        "company": kw.pop("company", "Example Infra Limited"),
        "title": kw.pop("title", "Outcome of Board Meeting"),
        "subcat": kw.pop("subcat", "Outcome of Board Meeting"),
        "description": kw.pop("description", ""),
        "published_at": kw.pop("published_at", "14-Nov-2026 00:19:04"),
    }
    item.update(kw)
    return item


class WindowTests(unittest.TestCase):
    def test_window_is_previous_2330_to_cutoff(self):
        now = at(7, 9)
        cutoff = now - timedelta(minutes=20)
        start, end = overnight_roundup.closed_window(now, cutoff=cutoff, fresh_start=now)
        self.assertEqual(start, at(23, 30, day=13))
        self.assertEqual(end, cutoff)
        self.assertTrue(overnight_roundup.in_closed_window("14-Nov-2026 00:19:04", start, end))
        self.assertTrue(overnight_roundup.in_closed_window("13-Nov-2026 23:50:42", start, end))
        # After the cutoff the item is eligible for its own post, not the roundup.
        self.assertFalse(overnight_roundup.in_closed_window("14-Nov-2026 07:13:40", start, end))
        self.assertFalse(overnight_roundup.in_closed_window(cutoff, start, end))


class SummariseTests(unittest.TestCase):
    def test_results_with_scorecard_include_revenue_and_pat(self):
        item = filing(
            title="Un-audited Financial Results for the quarter ended September 30, 2026",
            _prebuilt={"template": "A", "card": {
                "period": "Q2 FY27", "scope": "consolidated",
                "metrics": {"revenue": {"cur": 212.78}, "pat": {"cur": 18.13}},
            }},
        )
        row = overnight_roundup.summarise_item(item)
        self.assertEqual(row["kind"], "RESULTS")
        self.assertIn("Q2 FY27 results", row["plain"])
        self.assertIn("revenue ₹212.78 Cr", row["plain"])
        self.assertIn("PAT ₹18.13 Cr", row["plain"])

    def test_results_without_scorecard_are_named_without_invented_figures(self):
        item = filing(
            company="Fusion CX Limited",
            title=("Un-audited Financial Results for the quarter and half-year ended September 30, 2026. "
                   "Recommendation of Final Dividend of Rs. 0.75 per equity share."),
        )
        row = overnight_roundup.summarise_item(item)
        self.assertEqual(row["kind"], "RESULTS")
        self.assertIn("results", row["plain"])
        self.assertIn("₹0.75 / share", row["plain"])
        self.assertNotRegex(row["plain"], r"revenue ₹")
        self.assertNotRegex(row["plain"], r"PAT ₹")

    def test_order_uses_rupee_amount_already_in_the_exchange_text(self):
        item = filing(
            company="Example Infra Limited",
            title="Awarding of order(s) or contract(s)",
            subcat="Awarding of order(s) or contract(s)",
            description=("The Company has received a new order amounting to Qatari Riyals ~50million "
                         "(₹1,289.3 Million) from Example Telecom QPSC."),
        )
        row = overnight_roundup.summarise_item(item)
        self.assertEqual(row["kind"], "ORDER_WIN")
        self.assertIn("₹128.93 Cr", row["plain"])
        self.assertIn("Example Telecom", row["plain"])

    def test_procedural_and_presentations_are_skipped(self):
        skips = [
            filing(title="Investor Presentation", subcat="Investor Presentation"),
            filing(title="Newspaper Publication", subcat="General Updates"),
            filing(title="Voting Results and the Scrutinizer's Report of the NCLT convened meeting",
                   subcat="Amalgamation/Merger"),
            filing(title="Management Commentary of the Company on the Financial Results",
                   subcat="Updates"),
            filing(title="ALM Reporting - Debt listed on BSE", subcat="Updates"),
            filing(title="Record date for the purpose of Dividend is 27-Nov-2026", subcat="Record Date"),
        ]
        for item in skips:
            self.assertIsNone(overnight_roundup.summarise_item(item), item["title"])

    def test_wholly_owned_subsidiary_is_a_deal_line(self):
        item = filing(
            company="Sarveshwar Foods Limited",
            title="approval of incorporation of a wholly owned subsidiary in the Netherlands",
            subcat="General Updates",
        )
        row = overnight_roundup.summarise_item(item)
        self.assertEqual(row["kind"], "DEAL")
        self.assertIn("wholly owned subsidiary", row["plain"])

    def test_company_filings_are_merged_into_one_line(self):
        rows = [
            overnight_roundup.summarise_item(filing(
                id="a", company="Fusion CX Limited",
                title="Un-audited Financial Results for the quarter ended September 30, 2026",
            )),
            overnight_roundup.summarise_item(filing(
                id="b", company="Fusion CX Limited",
                title="Board recommended Final Dividend of Rs. 0.75 per equity share",
                subcat="Dividend",
            )),
        ]
        merged = overnight_roundup.merge_summaries(rows)
        self.assertEqual(len(merged), 1)
        self.assertIn("results", merged[0]["plain"])
        self.assertIn("₹0.75 / share", merged[0]["plain"])


class RenderTests(unittest.TestCase):
    def test_post_matches_opening_cues_style(self):
        items = [
            filing(
                company="Vertoz Limited",
                title="the financial results for the period ended September 30, 2026",
                published_at="13-Nov-2026 23:50:42",
            ),
            filing(
                id="NSE_FUSION_1",
                company="Fusion CX Limited",
                title=("Un-audited Financial Results for the quarter ended September 30, 2026. "
                       "Final Dividend of Rs. 0.75 per equity share."),
            ),
        ]
        posts = overnight_roundup.build_posts(items, at(7, 15))
        self.assertEqual(len(posts), 1)
        text = posts[0]
        self.assertTrue(text.startswith("<b>Overnight Filings – 14 Nov 2026</b>"))
        self.assertIn("<i>Results</i>", text)
        self.assertIn("<b>Fusion CX Ltd</b>", text)  # company_title casing
        self.assertIn("• ", text)
        self.assertLessEqual(overnight_roundup._telegram_len(text), overnight_roundup.SAFE_LEN)

    def test_oversized_list_splits_into_at_most_two_posts_and_drops_the_rest(self):
        items = []
        for i in range(200):
            items.append(filing(
                id=f"NSE_CO_{i}",
                company=f"Example Company {i:02d} Limited",
                title="Un-audited Financial Results for the quarter ended September 30, 2026",
                _prebuilt={"template": "A", "card": {
                    "period": "Q2 FY27", "scope": "consolidated",
                    "metrics": {"revenue": {"cur": 100 + i}, "pat": {"cur": 10 + i}},
                }},
            ))
        posts = overnight_roundup.build_posts(items, at(7, 15))
        self.assertEqual(len(posts), 2)
        self.assertIn("(1/2)", posts[0])
        self.assertIn("(2/2)", posts[1])
        for post in posts:
            self.assertLessEqual(overnight_roundup._telegram_len(post), overnight_roundup.TELEGRAM_MAX)
        # Ranked by significance; every line that ships has the parsed figures.
        self.assertIn("revenue ₹", posts[0])
        shipped = posts[0].count("• ") + posts[1].count("• ")
        self.assertLess(shipped, 200)
        self.assertGreater(shipped, 20)


class PersistenceTests(unittest.TestCase):
    def test_morning_done_and_buffer_survive_save_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "market_wire.db")
            js = os.path.join(tmp, "daemon_state.json")
            store = StateStore(db_path=db, json_state_path=js, allow_pending=True)
            start, end = overnight_roundup.closed_window(at(7, 9), cutoff=at(6, 49), fresh_start=at(7, 9))
            store.begin_overnight_window(start, end, "2026-11-14")
            self.assertTrue(store.note_overnight(filing(
                title="Un-audited Financial Results for the quarter ended September 30, 2026",
            ), start, end))
            # Outside the window is ignored.
            self.assertFalse(store.note_overnight(filing(
                id="NSE_LATE_1",
                title="Un-audited Financial Results for the quarter ended September 30, 2026",
                published_at="14-Nov-2026 07:13:40",
            ), start, end))
            store.morning_done["overnight_roundup"] = "2026-11-14"
            store.save_state()
            again = StateStore(db_path=db, json_state_path=js, allow_pending=True)
            self.assertEqual(again.morning_done["overnight_roundup"], "2026-11-14")
            self.assertEqual(len(again.overnight_items), 1)
            self.assertEqual(again.overnight_window["date"], "2026-11-14")
            # A second start on the same morning does not wipe the buffer.
            again.begin_overnight_window(start, end, "2026-11-14")
            self.assertEqual(len(again.overnight_items), 1)

    def test_idempotent_note_does_not_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(db_path=os.path.join(tmp, "db"), json_state_path=os.path.join(tmp, "s.json"),
                               allow_pending=True)
            start, end = overnight_roundup.closed_window(at(7, 9), cutoff=at(6, 49))
            item = filing(title="Un-audited Financial Results for the quarter ended September 30, 2026")
            self.assertTrue(store.note_overnight(item, start, end))
            self.assertFalse(store.note_overnight(item, start, end))
            self.assertEqual(len(store.overnight_items), 1)


class DaemonSlotTests(unittest.TestCase):
    def test_roundup_posts_once_and_survives_a_restart_during_grace(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "market_wire.db")
            js = os.path.join(tmp, "daemon_state.json")

            def make():
                from src.daemon import RealtimeDaemon
                with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHANNEL_ID": ""}):
                    d = RealtimeDaemon(dry_run=True, shift_seconds=1)
                # Point the dry daemon at our durable snapshot after init.
                d.state = StateStore(db_path=db, json_state_path=js, allow_pending=True)
                d.morning_done = d.state.morning_done
                d.clock = lambda: at(7, 16)
                d.outbox.last_sent = None
                return d

            first = make()
            start, end = overnight_roundup.closed_window(at(7, 9), cutoff=at(6, 49), fresh_start=at(7, 9))
            first.state.begin_overnight_window(start, end, "2026-11-14")
            first.state.note_overnight(filing(
                company="Vertoz Limited",
                title="the financial results for the period ended September 30, 2026",
                published_at="13-Nov-2026 23:50:42",
            ), start, end)
            first.check_morning_pack(at(7, 16))
            self.assertEqual(len(first.dry_posts), 1)
            self.assertIn("Overnight Filings", first.dry_posts[0]["text"])
            self.assertEqual(first.morning_done["overnight_roundup"], "2026-11-14")
            first.state.save_state()

            second = make()
            self.assertEqual(second.morning_done.get("overnight_roundup"), "2026-11-14")
            second.check_morning_pack(at(7, 16))
            self.assertEqual(second.dry_posts, [])

    def test_weekend_roundup_is_due_and_opening_cues_are_not(self):
        saturday = at(7, 15, day=14)  # 14 Nov 2026 is a Saturday
        self.assertEqual(saturday.weekday(), 5)
        self.assertEqual(morning_pack.due_slots(saturday, {}), ["overnight_roundup"])

    def test_split_second_part_sends_on_the_next_cycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            from src.daemon import RealtimeDaemon
            with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHANNEL_ID": ""}):
                d = RealtimeDaemon(dry_run=True, shift_seconds=1)
            d.state = StateStore(db_path=os.path.join(tmp, "db"),
                                 json_state_path=os.path.join(tmp, "s.json"), allow_pending=True)
            d.morning_done = d.state.morning_done
            d.clock = lambda: at(7, 16)
            d.outbox.last_sent = None
            start, end = overnight_roundup.closed_window(at(7, 9), cutoff=at(6, 49))
            d.state.begin_overnight_window(start, end, "2026-11-14")
            for i in range(80):
                d.state.note_overnight(filing(
                    id=f"NSE_CO_{i}",
                    company=f"Example Company {i:02d} Limited",
                    title="Un-audited Financial Results for the quarter ended September 30, 2026",
                    published_at="14-Nov-2026 00:20:00",
                    _prebuilt={"template": "A", "card": {
                        "period": "Q2 FY27", "scope": "consolidated",
                        "metrics": {"revenue": {"cur": 100 + i}, "pat": {"cur": 10 + i}},
                    }},
                ), start, end)
            d.check_morning_pack(at(7, 16))
            self.assertEqual(len(d.dry_posts), 1)
            self.assertIn("(1/2)", d.dry_posts[0]["text"])
            self.assertTrue(d.state.overnight_pending_parts)
            self.assertNotEqual(d.morning_done.get("overnight_roundup"), "2026-11-14")
            # Pacing would block an immediate retry; clear the gap for the test.
            d.outbox.last_sent = None
            d.check_morning_pack(at(7, 17))
            self.assertEqual(len(d.dry_posts), 2)
            self.assertIn("(2/2)", d.dry_posts[1]["text"])
            self.assertEqual(d.morning_done["overnight_roundup"], "2026-11-14")
            self.assertFalse(d.state.overnight_pending_parts)


class CutoffUnchangedTests(unittest.TestCase):
    def test_backlog_item_is_not_queued_individually(self):
        from src.daemon import RealtimeDaemon, BACKLOG_AGE
        self.assertEqual(BACKLOG_AGE, timedelta(minutes=20))
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHANNEL_ID": ""}):
                d = RealtimeDaemon(dry_run=True, shift_seconds=1)
            d.state = StateStore(db_path=os.path.join(tmp, "db"),
                                 json_state_path=os.path.join(tmp, "s.json"), allow_pending=True)
            d.morning_done = d.state.morning_done
            now = at(7, 9)
            d.clock = lambda: now
            # Force a fresh start with a known cutoff.
            d.state.last_cycle_at = (now - timedelta(hours=8)).isoformat()
            cutoff = d._backlog_cutoff(now)
            self.assertEqual(cutoff, now - timedelta(minutes=20))
            start, end = d._overnight_bounds(now)
            item = filing(
                title="Un-audited Financial Results for the quarter ended September 30, 2026",
                published_at="14-Nov-2026 00:19:04",
            )
            d.state.note_overnight(item, start, end)
            # The cutoff path marks it seen and does not compose an individual post.
            d._trace = lambda *a, **k: None
            d.outbox.has = lambda _id: False
            d.state.mark_seen(item["id"], "NSE")
            self.assertTrue(d.state.is_seen(item["id"]))
            self.assertEqual(d.dry_posts, [])


if __name__ == "__main__":
    unittest.main()
