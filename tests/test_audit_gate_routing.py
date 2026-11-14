"""Routing and named-person fixes for the gap-audit 'gate too strict' misses."""
import unittest
from datetime import datetime

import pymupdf

from src.filter_engine import FilterEngine
from src.event_evidence import evaluate_event, named_person
from src import composer, editorial
from sources.annexure_parser import AnnexureParser


class TitleOutranksBodyTests(unittest.TestCase):
    def setUp(self):
        self.engine = FilterEngine(config_path="/nonexistent/rules.json")

    def classify(self, title, desc=""):
        return self.engine._classify_category(title, desc)

    def test_buyback_title_not_acquisition(self):
        self.assertEqual(self.classify("Buyback", "Communication to Shareholders- BuyBack"),
                         "CORP_RESTRUCTURE")
        result = self.engine.evaluate_stage_2({
            "company": "Medplus Health Services Ltd", "source": "BSE",
            "category": "CORP_RESTRUCTURE", "title": "Buyback",
            "description": (
                "The Company has approved a buyback of equity shares. "
                "The buyback is an acquisition of shares from shareholders at Rs. 800 per share."
            ),
        })
        self.assertTrue(result["publish"])
        self.assertIn("BUYBACK", result["headline"])
        self.assertNotEqual(result.get("rejection_reason"),
                            "ACQUISITION_REQUIRES_TARGET_AND_STAKE_ACTION")

    def test_cfo_appointment_title_not_financing(self):
        title = "Announcement under Regulation 30 (LODR)-Change in Management"
        desc = (
            "Intimation regarding appointment of Chief Financial Officer (CFO). "
            "The Company also has a working capital loan facility in place."
        )
        self.assertEqual(self.classify(title, desc), "MANAGEMENT_CHANGE")

    def test_acquisition_title_not_results(self):
        title = "Announcement under Regulation 30 (LODR)-Acquisition"
        desc = (
            "Acquisition of 51% Shareholding in M/s. Example Hospitality Private Limited. "
            "Outcome of the board meeting also considered the financial statements."
        )
        self.assertEqual(self.classify(title, desc), "CORP_RESTRUCTURE")

    def test_stock_split_title_not_results(self):
        title = "Corporate Action-Outcome of Sub division / Stock Split"
        desc = "Outcome of Board Meeting for stock split of shares. Financial highlights attached."
        self.assertEqual(self.classify(title, desc), "CORP_RESTRUCTURE")

    def test_buyback_title_not_management_because_of_banker_appointment(self):
        title = "Board Resolution for Buy-Back"
        desc = "Appointment of merchant banker to the buy-back of equity shares."
        self.assertEqual(self.classify(title, desc), "CORP_RESTRUCTURE")

    def test_generic_title_still_uses_body_for_financing(self):
        self.assertEqual(self.classify(
            "Board meeting outcome",
            "Board of Directors approved a lease facility up to Rs.81 crore and working capital loan.",
        ), "FINANCING")

    def test_financial_results_title_still_wins(self):
        self.assertEqual(self.classify(
            "Unaudited financial results",
            "The board also approved a dividend and a buyback.",
        ), "FINANCIAL_RESULTS")


class NamedPersonTests(unittest.TestCase):
    def test_initials_and_as_role_count(self):
        text = "Appointment of Mr. J Rama Prasad as CFO of the Company with effect from December 1, 2026."
        self.assertTrue(named_person(text))
        result = evaluate_event("MANAGEMENT_CHANGE", "Anjani Foods Ltd",
                                "Change in Management", text)
        self.assertTrue(result["publish"])
        self.assertIn("J Rama Prasad", result["summary_points"][0])

    def test_role_phrase_is_not_a_person(self):
        self.assertFalse(named_person("Appointment of Chief Financial Officer of the Company"))
        self.assertFalse(evaluate_event(
            "MANAGEMENT_CHANGE", "Example", "Change in Management",
            "Intimation regarding appointment of Chief Financial Officer (CFO) and Key Managerial Personnel of the Company.",
        )["publish"])

    def test_signatory_without_action_still_rejected(self):
        self.assertFalse(evaluate_event(
            "MANAGEMENT_CHANGE", "Example", "Corporate update",
            "Signed by Mr. Example Person, Chief Financial Officer.",
        )["publish"])

    def test_two_word_honorific_name_still_accepted(self):
        text = "Mr. Example Person has resigned as Chief Financial Officer effective October 30, 2026."
        self.assertTrue(evaluate_event("MANAGEMENT_CHANGE", "Example", "", text)["publish"])


class ResultsFallbackTests(unittest.TestCase):
    def setUp(self):
        self.engine = FilterEngine(config_path="/nonexistent/rules.json")

    def classify(self, title, desc=""):
        return self.engine._classify_category(title, desc)

    def test_prose_results_letter_survives_failed_scorecard(self):
        with pymupdf.open() as doc:
            page = doc.new_page()
            page.insert_text(
                (50, 50),
                "The company recorded Revenue from operations of INR 123 crore in Q2 FY27. "
                "Profit after tax was INR 12 crore for the same quarter.",
                fontsize=11,
            )
            data = doc.tobytes()
        evidence = AnnexureParser.extract_evidence(data)
        self.assertTrue(any("123" in (e["text"] or "") for e in evidence))
        self.assertTrue(any("12" in (e["text"] or "") for e in evidence))

    def test_dense_numeric_grid_still_not_guessed(self):
        with pymupdf.open() as doc:
            page = doc.new_page()
            page.insert_text((50, 50), "Revenue from operations\n123.45\n112.34\n101.23", fontsize=10)
            evidence = AnnexureParser.extract_evidence(doc.tobytes())
        self.assertEqual(evidence, [])


    def test_acquisition_of_shareholding_percent_is_enough(self):
        title = "Announcement under Regulation 30 (LODR)-Acquisition"
        desc = "Acquisition of 51% Shareholding in M/s. Mudunuru Hospitality Private Limited, thereby making it a subsidiary company."
        self.assertEqual(self.classify(title, desc), "CORP_RESTRUCTURE")
        result = self.engine.evaluate_stage_2({
            "company": "Mudunuru Ltd", "source": "BSE", "category": "CORP_RESTRUCTURE",
            "title": title, "description": desc,
        })
        self.assertTrue(result["publish"], result)
        self.assertIn("51%", str(result["summary_points"]))
        self.assertIn("Mudunuru Hospitality", str(result["summary_points"]))

    def test_incorporation_title_is_not_an_acquisition_gate(self):
        title = "Incorporation Of Wholly Owned Subsidiary: Arkade Estates Private Limited"
        desc = "Incorporation of a Wholly Owned Subsidiary."
        self.assertEqual(self.classify(title, desc), "CORP_RESTRUCTURE")
        result = self.engine.evaluate_stage_2({
            "company": "Arkade Developers Ltd", "source": "BSE", "category": "CORP_RESTRUCTURE",
            "title": title, "description": desc,
        })
        self.assertTrue(result["publish"], result)
        self.assertNotEqual(result.get("rejection_reason"),
                            "ACQUISITION_REQUIRES_TARGET_AND_STAKE_ACTION")

    def test_resolution_plan_title_is_corporate_action(self):
        self.assertEqual(self.classify(
            "Intimation Regarding Approval Of Resolution Plan For Tork Motors Private Limited",
            "Intimation regarding approval of resolution plan for Tork Motors Private Limited",
        ), "CORP_RESTRUCTURE")

    def test_signatory_line_is_not_a_material_update(self):
        result = self.engine.evaluate_stage_2({
            "company": "Example Ltd", "source": "BSE", "category": "GENERAL_WIRE",
            "title": "Corporate update", "description": "Signed by Chief Financial Officer",
        })
        self.assertFalse(result["publish"])


class BulletFloorTests(unittest.TestCase):
    def test_two_factual_bullets_is_enough_for_a_company_update(self):
        ev = editorial.Event("UPDATE", "needs_number_and_decision")
        pending, why = composer.compose_filing(
            {"id": "x", "company": "Example Ltd", "source": "BSE"},
            {"headline": "Example Ltd – CORPORATE ACTION",
             "summary_points": [
                 "The board approved incorporation of a wholly owned subsidiary on 13 November 2026.",
                 "The investment in the subsidiary will be up to Rs. 10 crore.",
             ]},
            ev, datetime(2026, 11, 13, 12),
        )
        self.assertIsNotNone(pending, why)



class EditorialGateLoosenTests(unittest.TestCase):
    def setUp(self):
        from src import editorial
        self.editorial = editorial
        self.rules = editorial.load_rules()

    def test_appointment_word_counts_as_decision(self):
        ev = self.editorial.classify(
            {"title": "Announcement under Regulation 30 (LODR)-Change in Management",
             "description": "Intimation regarding appointment of Chief Financial Officer"},
            self.rules)
        self.assertEqual(ev.label, "MANAGEMENT CHANGE")
        keep, why = self.editorial.gate(
            {"title": "Change in Management",
             "description": "Intimation regarding appointment of Chief Financial Officer"},
            ev, self.rules)
        self.assertTrue(keep, why)

    def test_hyphenated_buyback_is_a_decision_event(self):
        ev = self.editorial.classify(
            {"title": "Board Resolution for Buy-Back",
             "description": "The Board approved a buy-back of equity shares."},
            self.rules)
        self.assertEqual(ev.label, "BUYBACK")
        keep, why = self.editorial.gate(
            {"title": "Board Resolution for Buy-Back",
             "description": "The Board approved a buy-back of equity shares."},
            ev, self.rules)
        self.assertTrue(keep, why)
