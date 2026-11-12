"""Behaviour tests for the channel-quality upgrade, driven by saved public-source fixtures."""
import io
import json
import os
import re
import unittest
from datetime import datetime, timedelta
from unittest import mock

from src import editorial, morning_pack
from src.concall_summary import summarise, format_concall
from src.ipo_evidence import parse_past_issues, listing_open, listing_post, allotment_post, registrar_link
from src.scheduler import IST, Outbox, Pending, in_active_window, fresh_enough
from sources.market_cues import parse_yahoo, parse_nse_status
from sources.nse_fetcher import large_deals
from sources.pib_fetcher import parse_releases
from sources.x_fetcher import parse_timeline, XFetcher

FX = os.path.join(os.path.dirname(__file__), "fixtures")


def fx(*p, mode="r"):
    with open(os.path.join(FX, *p), mode, **({} if "b" in mode else {"encoding": "utf-8"})) as f:
        return f.read()


def at(h, m=0, day=4):  # Wed 4 Nov 2026 IST
    return datetime(2026, 11, day, h, m, tzinfo=IST)


RULES = editorial.load_rules()


class EditorialTests(unittest.TestCase):
    def test_header_is_title_case_company_and_caps_event_never_truncated(self):
        long = "HINDUSTAN AERONAUTICS AND ADVANCED DEFENCE MANUFACTURING SYSTEMS LIMITED"
        h = editorial.header(long, "order win")
        self.assertEqual(h, "<b>Hindustan Aeronautics And Advanced Defence Manufacturing Systems Ltd – ORDER WIN</b>")
        self.assertEqual(editorial.header("Tata Motors Ltd", "RESULTS"), "<b>Tata Motors Ltd – RESULTS</b>")

    def test_procedural_filings_dropped(self):
        for sub in ("Board Meeting Intimation", "Trading Window", "Newspaper Publication", "Shareholders meeting"):
            ev = editorial.classify({"subcat": sub, "title": "x"}, RULES)
            self.assertEqual(ev.rule, "always_drop", sub)

    def test_outcome_mentioning_record_date_is_not_procedural(self):
        ev = editorial.classify({"subcat": "Outcome of Board Meeting",
                                 "title": "Board approves dividend of Rs 5 per share; record date 14 Nov"}, RULES)
        self.assertEqual(ev.label, "DIVIDEND")

    def test_order_win_needs_number(self):
        item = {"subcat": "Award of Order / Receipt of Order", "title": "Receipt of order"}
        ev = editorial.classify(item, RULES)
        self.assertEqual(ev.label, "ORDER WIN")
        self.assertFalse(editorial.gate(item, ev, RULES)[0])
        item["description"] = "The company has received an order worth Rs 412 crore from NHAI."
        self.assertTrue(editorial.gate(item, ev, RULES)[0])

    def test_tax_threshold(self):
        item = {"title": "Receipt of GST demand order", "description": "demand of Rs 2.1 lakh"}
        ev = editorial.classify(item, RULES)
        self.assertEqual(ev.label, "TAX DEMAND")
        self.assertFalse(editorial.gate(item, ev, RULES)[0])
        item["description"] = "demand of Rs 25 crore including penalty"
        self.assertTrue(editorial.gate(item, ev, RULES)[0])
        item["description"] = "demand of Rs 3 crore"
        self.assertTrue(editorial.gate(item, ev, RULES, revenue_cr=200)[0])   # 1.5% of revenue

    def test_junk_stripped_and_bullets_have_facts(self):
        text = ("Dear Sir/Madam,\nPursuant to Regulation 30 of SEBI (LODR) Regulations, 2015, we inform you.\n"
                "The company has received an order worth Rs 412 crore from NHAI for a highway project.\n"
                "The order is to be executed by 31 March 2028.\nThis is for your information and records.\n"
                "We are pleased to share this.\nThanking you, Yours faithfully")
        bullets = editorial.pick_bullets(text)
        self.assertEqual(len(bullets), 2)
        joined = " ".join(bullets)
        self.assertNotRegex(joined, editorial.JUNK_IN_POST)
        self.assertIn("412 crore", joined)

    def test_global_news_whitelist_and_cap(self):
        keep = editorial.global_news_decision("US Fed holds rates steady", RULES, 99)
        self.assertTrue(keep[0])
        self.assertFalse(editorial.global_news_decision("Crude rises 0.5% in Asia", RULES, 99)[0])

    def test_deal_template(self):
        deal = large_deals(json.loads(fx("sources", "nse_largedeal.json")))[0]
        post = editorial.format_deal(deal)
        self.assertRegex(post.splitlines()[0], r"^<b>.+ Ltd – (BLOCK|BULK) DEAL</b>$")
        self.assertRegex(post, r"Value ₹[\d,.]+ Cr")


class DealTests(unittest.TestCase):
    def test_threshold_and_value(self):
        snap = json.loads(fx("sources", "nse_largedeal.json"))
        deals = large_deals(snap, min_cr=50)
        self.assertTrue(deals)
        for d in deals:
            self.assertGreaterEqual(d["value_cr"], 50)
            self.assertAlmostEqual(d["value_cr"], d["qty"] * d["price"] / 1e7, delta=0.05)


class SchedulerTests(unittest.TestCase):
    def test_quiet_hours(self):
        self.assertFalse(in_active_window(at(23, 31)))
        self.assertFalse(in_active_window(at(6, 29)))
        self.assertFalse(in_active_window(at(23, 30)))
        self.assertTrue(in_active_window(at(23, 29)))
        self.assertTrue(in_active_window(at(7, 0)))
        self.assertTrue(in_active_window(at(22, 59)))

    def test_freshness(self):
        now = at(12)
        self.assertFalse(fresh_enough(now - timedelta(minutes=31), now, "ORDER WIN"))
        self.assertTrue(fresh_enough(now - timedelta(minutes=29), now, "ORDER WIN"))
        self.assertTrue(fresh_enough(now - timedelta(hours=2, minutes=50), now, "RESULTS"))
        self.assertFalse(fresh_enough(now - timedelta(hours=3, minutes=5), now, "EARNINGS CALL"))

    def p(self, key, company, event, bullets, t):
        return Pending(key, company, event, f"<b>{company} – {event}</b>", bullets, t)

    def test_merge_same_company_same_event_and_split_different_event(self):
        ob = Outbox()
        self.assertEqual(ob.put(self.p("1", "ABC", "ORDER WIN", ["a 1 cr"], at(10))), "QUEUED")
        self.assertEqual(ob.put(self.p("2", "ABC", "ORDER WIN", ["b 2 cr"], at(10, 5))), "MERGED_QUEUED")
        self.assertEqual(ob.put(self.p("3", "ABC", "DIVIDEND", ["c"], at(10, 6))), "QUEUED")
        first = ob.due(at(10, 7))
        self.assertEqual(first.bullets, ["a 1 cr", "b 2 cr"])
        self.assertIsNone(ob.due(at(10, 7) + timedelta(seconds=19)))     # 20 s gap
        self.assertEqual(ob.due(at(10, 7) + timedelta(seconds=20)).event, "DIVIDEND")
        # same event again within 30 min, nothing new -> dropped
        self.assertEqual(ob.put(self.p("4", "ABC", "ORDER WIN", ["a 1 cr"], at(10, 20))), "MERGED_ALREADY_SENT")

    def test_nothing_released_overnight(self):
        ob = Outbox()
        ob.put(self.p("1", "ABC", "ORDER WIN", ["x"], at(23, 10)))
        for h, m in ((23, 30), (0, 0), (3, 0), (6, 29)):
            self.assertIsNone(ob.due(at(h, m)))
        self.assertEqual(len(ob.drop_overnight(at(6, 0))), 1)


class ConcallTests(unittest.TestCase):
    def test_brigade_summary(self):
        pts = summarise(fx("concall", "brigade.txt"))
        self.assertTrue(4 <= len(pts) <= 6)
        joined = " ".join(pts)
        self.assertIn("9,000 crores presales guidance", joined)
        for p in pts:
            self.assertRegex(p, r"\d")
            self.assertNotRegex(p, r"\b(?:I|we|our)\b")

    def test_acutaas_guidance_found(self):
        pts = summarise(fx("concall", "acutaas.txt"))
        self.assertTrue(any("guidance" in p.lower() for p in pts))

    def test_garbled_transcript_withheld(self):
        self.assertEqual(summarise(fx("concall", "kaveri.txt")), [])

    def test_format(self):
        post = format_concall("BRIGADE ENTERPRISES LIMITED", ["a 1%", "b 2%", "c 3%", "d 4%"], "Q2 FY27")
        self.assertTrue(post.startswith("<b>Brigade Enterprises Ltd – EARNINGS CALL Q2 FY27</b>"))


class SourceParserTests(unittest.TestCase):
    def test_pib_market_ministries_only(self):
        rel = parse_releases(fx("sources", "pib_allrel.html"))
        self.assertTrue(rel)
        for r in rel:
            self.assertRegex(r["link"], r"^https://pib\.gov\.in/PressReleaseDetail\.aspx\?PRID=\d+$")

    def test_x_skips_retweets_and_replies(self):
        payload = {"data": [{"id": "1", "text": "Policy repo rate unchanged at 5.25%", "created_at": "2026-11-04T04:30:00Z"},
                            {"id": "2", "text": "RT x", "referenced_tweets": [{"type": "retweeted", "id": "9"}]},
                            {"id": "3", "text": "@a thanks", "referenced_tweets": [{"type": "replied_to", "id": "8"}]}]}
        out = parse_timeline("RBI", payload)
        self.assertEqual([o["id"] for o in out], ["X_1"])
        self.assertEqual(out[0]["link"], "https://x.com/RBI/status/1")

    def test_x_disabled_without_token(self):
        with mock.patch.dict(os.environ, {"X_BEARER_TOKEN": ""}):
            self.assertEqual(XFetcher(session=mock.Mock()).fetch_recent(), [])

    def test_rbi_speeches_from_fixture(self):
        from sources.rbi_fetcher import RBIFetcher
        data = fx("sources", "rbi_speeches.xml", mode="rb")

        class Resp(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *a): return False
        with mock.patch("urllib.request.urlopen", return_value=Resp(data)):
            items = RBIFetcher().fetch_speeches()
        self.assertTrue(items)
        self.assertTrue(all(i["kind"] == "speech" for i in items))

    def test_yahoo_daily_change_uses_closes_not_chart_previous_close(self):
        payload = json.loads(fx("sources", "yahoo_GSPC.json"))
        closes = [c for c in payload["chart"]["result"][0]["indicators"]["quote"][0]["close"] if c]
        q = parse_yahoo(payload)
        self.assertAlmostEqual(q["pct"], round((closes[-1] - closes[-2]) / closes[-2] * 100, 2))

    def test_nse_status_gift(self):
        out = parse_nse_status(json.loads(fx("sources", "nse_market_status.json")))
        self.assertIn("GIFT Nifty", out)
        self.assertGreater(out["GIFT Nifty"]["last"], 10000)


class MorningPackTests(unittest.TestCase):
    def cues(self):
        c = {}
        for f, label in (("GSPC", "S&P 500"), ("IXIC", "Nasdaq"), ("DJI", "Dow Jones"), ("BZ_F", "Brent crude"),
                         ("N225", "Nikkei"), ("INR_X", "USD/INR"), ("INDIAVIX", "India VIX")):
            c[label] = parse_yahoo(json.loads(fx("sources", f"yahoo_{f}.json")))
        c.update(parse_nse_status(json.loads(fx("sources", "nse_market_status.json"))))
        return c

    def test_slots(self):
        self.assertEqual(morning_pack.due_slots(at(7, 15), {}), ["opening_cues"])
        self.assertEqual(morning_pack.due_slots(at(7, 16), {"opening_cues": "2026-11-04"}), [])
        self.assertEqual(morning_pack.due_slots(at(8, 50), {}), ["stocks_in_focus"])
        self.assertEqual(morning_pack.due_slots(at(9, 5), {}), ["preopen"])
        self.assertEqual(morning_pack.due_slots(at(7, 15, day=7), {}), [])   # Saturday

    def test_opening_cues_arrows_match_sign(self):
        post = morning_pack.opening_cues(self.cues(), at(7, 15))
        self.assertTrue(post.startswith("<b>Opening Cues"))
        for line in post.splitlines():
            m = re.search(r"([↑↓→]) ([\d.]+)%", line)
            if m:
                self.assertNotEqual(m.group(2), "")

    def test_stocks_in_focus_from_previous_evening(self):
        recs = [{"ts": at(18, 0, day=3).isoformat(), "company": "Tata Motors Ltd", "event": "RESULTS", "lead": "PAT ₹4,000 Cr"},
                {"ts": at(12, 0, day=3).isoformat(), "company": "Old Ltd", "event": "ORDER WIN"},
                {"ts": at(19, 0, day=3).isoformat(), "company": "Tata Motors Ltd", "event": "DIVIDEND"}]
        post = morning_pack.stocks_in_focus(recs, at(8, 45))
        self.assertIn("Tata Motors Ltd", post)
        self.assertNotIn("Old Ltd", post)
        self.assertEqual(post.count("Tata Motors"), 1)

    def test_preopen(self):
        self.assertIn("GIFT Nifty", morning_pack.preopen(self.cues(), at(9, 5)))


class IPOTests(unittest.TestCase):
    def test_listing_premium(self):
        issues = parse_past_issues(json.loads(fx("sources", "nse_past_issues.json")))
        bls = next(i for i in issues if i["symbol"] == "BLSPOLY")
        o = listing_open(json.loads(fx("sources", "yahoo_listing.json")), bls["listing_date"])
        self.assertEqual(o, 248.0)
        post = listing_post(bls, o)
        self.assertIn("<b>BLS Polymers Ltd – IPO LISTING</b>", post)
        self.assertIn("↑ 65.3%", post)
        self.assertFalse(any(i["symbol"] == "MKSONS" for i in issues))   # not listed yet

    def test_allotment_link(self):
        self.assertEqual(registrar_link("Registrar: KFin Technologies Ltd")[1], "https://ipostatus.kfintech.com/")
        self.assertIsNone(allotment_post("X Ltd", "no registrar named"))


if __name__ == "__main__":
    unittest.main()


class BulletHygieneTests(unittest.TestCase):
    def test_glyphs_duplicates_and_boilerplate(self):
        from src.composer import filing_bullets
        out = filing_bullets(["Net Profit increased by 36.06% Q2-on-Q2",
                              "FINANCIAL HIGHLIGHTS Net Profit increased by 36.06% Q2-on-Q2 in 2026",
                              "Revenue increased by 30.73% to X274.23 crore.",
                              "Appointed Mr. A Bhartia (DIN: ) as Director from 02 Nov 2026 at the Mee^ng",
                              "Results along with the Limited Review Report approved on 2 Nov 2026"])
        self.assertEqual(out, ["Net Profit increased by 36.06% Q2-on-Q2", "Revenue increased by 30.73% to ₹274.23 crore.",
                               "Appointed Mr. A Bhartia as Director from 02 Nov 2026 at the Meeting"])

    def test_results_label_names_latest_quarter(self):
        from src.composer import _period_label
        self.assertEqual(_period_label("₹277 Cr in Q2 FY27 vs ₹210 Cr in Q2 FY26; H1 FY27 ₹657 Cr", "FINANCIAL RESULTS"), "Q2 FY27 RESULTS")
        self.assertEqual(_period_label("PAT ₹8,943 crore in H1 FY27", "FINANCIAL RESULTS"), "H1 FY27 RESULTS")
        self.assertEqual(_period_label("PAT up 20%", "FINANCIAL RESULTS"), "FINANCIAL RESULTS")
        # Post 6134: a prior-year comparison ("in line with Q2 FY '26") must not
        # set the title year when the filing names H1 FY '27 as the period.
        body = ("For H1 FY '27, revenue grew 9.7% to Rs. 335 crores. "
                "Domestic revenue for the quarter grew approximately 36% sequentially and 27% year-on-year, "
                "increasing its share of revenue to 19.5% from 15.5% in Q1. "
                "Profit after tax was INR4.2 crores, in line with Q2 FY '26 with a PAT margin of 2.4%. "
                "For H1 FY '27, revenue from operations grew 9.7%")
        self.assertEqual(_period_label(body, "FINANCIAL RESULTS"), "H1 FY27 RESULTS")


class GlyphTests(unittest.TestCase):
    def test_rupee_and_quarter_glyph_damage(self):
        from src.composer import _clean
        self.assertEqual(_clean("PAT increased by 62% tot 3,436 crore in 02 FY27 v/s z 2,122 crore in 02 FY26."),
                         "PAT increased by 62% to ₹3,436 crore in Q2 FY27 v/s ₹2,122 crore in Q2 FY26.")
        self.assertEqual(_clean("EBITDA to X31.5 crore (Q2 FY26:%23.7 crore)"), "EBITDA to ₹31.5 crore (Q2 FY26:₹23.7 crore)")
        self.assertEqual(_clean("OperaƟonal EBITDA up 43.8%"), "Operational EBITDA up 43.8%")
        self.assertEqual(_clean("Revenue grew 3.9x to 25 crore"), "Revenue grew 3.9x to 25 crore")


class MorningAndMacroGuards(unittest.TestCase):
    def test_preopen_skips_yesterdays_close(self):
        from datetime import datetime
        from src.scheduler import IST
        now = datetime(2026, 11, 4, 9, 5, tzinfo=IST)
        stale = {"last": 23810.85, "pct": 1.58, "status": "CLOSE", "time": "03-Nov-2026 15:30"}
        live = {"last": 23900.0, "pct": 0.4, "status": "Pre-Open", "time": "04-Nov-2026 09:08"}
        gift = {"last": 23860.0, "change": 100.0, "pct": 0.42}
        self.assertNotIn("indicative", morning_pack.preopen({"GIFT Nifty": gift, "Nifty indicative": stale}, now))
        self.assertIn("indicative", morning_pack.preopen({"GIFT Nifty": gift, "Nifty indicative": live}, now))

    def test_stocks_in_focus_no_double_escape(self):
        from datetime import datetime
        from src.scheduler import IST
        rec = [{"ts": "2026-11-03T18:00:00+05:30", "company": "Brigade Hotel Ventures Ltd", "event": "EARNINGS CALL",
                "lead": "Room and F&amp;B revenue rose 10% in Q2"}]
        text = morning_pack.stocks_in_focus(rec, datetime(2026, 11, 4, 8, 45, tzinfo=IST))
        self.assertIn("F&amp;B", text)
        self.assertNotIn("&amp;amp;", text)

    def test_macro_without_fact_is_dropped(self):
        from datetime import datetime
        from src.composer import compose_prebuilt
        from src.scheduler import IST
        item = {"id": "S1", "_prebuilt": {"template": "MACRO", "label": "RBI SPEECH", "title": "Leading Today -",
                                          "bullets": ["It is a pleasure to be here with the leadership team"]}}
        self.assertEqual(compose_prebuilt(item, datetime(2026, 11, 3, 16, 0, tzinfo=IST))[0], None)


class RegressionsFromReplay(unittest.TestCase):
    def test_sentence_initial_A_is_not_a_rating(self):
        self.assertIsNone(editorial.NUMBER.search("A bank's strategy is shaped by leadership"))
        self.assertIsNotNone(editorial.NUMBER.search("CARE reaffirmed A- (Stable)"))

    def test_block_trade_not_repeated_as_bulk(self):
        from sources.nse_fetcher import large_deals
        row = {"symbol": "GALLANTT", "name": "Gallantt Ispat Limited", "clientName": "X Pvt Ltd", "buySell": "BUY",
               "qty": "5000000", "watp": "501", "date": "03-Nov-2026"}
        deals = large_deals({"BLOCK_DEALS_DATA": [row], "BULK_DEALS_DATA": [row]})
        self.assertEqual([d["kind"] for d in deals], ["BLOCK DEAL"])

    def test_focus_event_case(self):
        from src.morning_pack import _event_case
        self.assertEqual(_event_case("Q2 FY27 RESULTS"), "Q2 FY27 Results")


class OpenIssueFixes(unittest.TestCase):
    def test_rating_table_cell_bleed_removed(self):
        from src.composer import _clean
        self.assertEqual(_clean("Issuer Rating - [ICRA]A(Stable); Rating Assigned - $"),
                         "Issuer Rating - [ICRA]A(Stable); Rating Assigned")
        self.assertFalse(_clean("Fund based working capital 170.45 [ICRA]A(Stable); Rating Assigned RBI").endswith("RBI"))

    def test_letter_spaced_heading_collapsed(self):
        from src.composer import _clean
        out = _clean("P R E S S R E L E A S E MICL GROUP SECURES TWO EPC WORK ORDERS WORTH ₹583 CRORE")
        self.assertEqual(out, "MICL GROUP SECURES TWO EPC WORK ORDERS WORTH ₹583 CRORE")

    def test_subsidiary_foreign_currency_results_dropped(self):
        from datetime import datetime
        from src import composer, editorial
        ev = editorial.Event("FINANCIAL RESULTS", "test")
        pts = ["EBITDA: EUR 40.7 million (EUR -38 million in Q3/25)",
               "Group revenue rose by 57.0% year over year to EUR 351.1 million (compared with EUR 223.6 million).",
               "EBITDA amounted to EUR 40.7 million in the third quarter of 2026; the margin improved to 11.6%."]
        p, why = composer.compose_filing({"id": "x", "company": "Bajaj Auto Ltd", "source": "BSE"},
                                         {"summary_points": pts}, ev, datetime(2026, 11, 3, 12))
        self.assertIsNone(p)
        self.assertEqual(why, "TOO_FEW_FACTUAL_BULLETS")

    def test_press_release_results_get_period_label(self):
        from datetime import datetime
        from src import composer
        self.assertEqual(composer._period_label("Volume led growth of 17.6% in Q2’FY27", "FINANCIAL RESULTS"), "Q2 FY27 RESULTS")
        self.assertEqual(composer._period_label("profit for 2Q FY 2026-27", "FINANCIAL RESULTS"), "Q2 FY27 RESULTS")
        self.assertEqual(composer._period_label("Revenue up 19% YoY", "FINANCIAL RESULTS", datetime(2026, 11, 3)), "Q2 FY27 RESULTS")
        self.assertEqual(composer._period_label("Revenue up 19% YoY", "FINANCIAL RESULTS", datetime(2026, 5, 20)), "Q4 FY26 RESULTS")
