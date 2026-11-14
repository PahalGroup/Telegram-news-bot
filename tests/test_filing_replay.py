import unittest
from src.quality import sentences
from src.filter_engine import FilterEngine
from src.event_evidence import evaluate_event

class FilingReplayRegressions(unittest.TestCase):
    def test_tariff_in_project_terms_does_not_make_macro(self):
        e = FilterEngine(config_path="/nonexistent.json")
        self.assertEqual(e._classify_category("Receipt of Letter of Intent", "PPA follows adoption of tariff by regulator."), "ORDER_WIN")

    def test_legal_preface_preserves_factual_body(self):
        text = "Pursuant to Regulation 30, this is to inform you that the board approved a loan of INR 10 crore from Example Bank."
        self.assertEqual(sentences(text), ["the board approved a loan of INR 10 crore from Example Bank."])

    def test_commissioning_is_not_order_or_directorate(self):
        e = FilterEngine(config_path="/nonexistent.json")
        text = "Example Limited has fully commissioned its 150 MW wind project."
        category = e._classify_category("Update on commissioning of additional capacity", text)
        self.assertEqual(category, "CAPACITY_UPDATE")
        self.assertTrue(evaluate_event(category, "EXAMPLE", "", text)["publish"])
        self.assertFalse(evaluate_event(category, "EXAMPLE", "", "Example will commission a 150 MW project next year.")["publish"])

    def test_whole_letter_block_preserves_paragraphs(self):
        from src.quality import paragraph_sentences
        block = "Sub: Regulatory disclosure\n\nDear Sir/Madam,\n\nPursuant to Regulation 30, this is to inform you that the board approved a loan of INR 10 crore.\n\nYours faithfully"
        self.assertIn("the board approved a loan of INR 10 crore.", paragraph_sentences(block))

    def test_wish_to_inform_preface_preserves_order_value(self):
        # Live order letters often use "we wish to inform you that" after the
        # Regulation 30 preface, not "this is to inform you that".
        text = (
            "Pursuant to Regulation 30 of the Listing Regulations, we wish to inform you that "
            "the Company has received a new order amounting to Qatari Riyals ~50million "
            "(₹1,289.3 Million) from Example Telecom QPSC."
        )
        self.assertEqual(
            sentences(text),
            ["the Company has received a new order amounting to Qatari Riyals ~50million "
             "(₹1,289.3 Million) from Example Telecom QPSC."],
        )

    def test_wish_to_inform_letter_pdf_extracts_order_value(self):
        import pymupdf
        from sources.annexure_parser import AnnexureParser

        letter = (
            "Dear Sir/Madam,\n\n"
            "Pursuant to Regulation 30 of the Listing Regulations, we wish to inform you that "
            "the Company has received a new order amounting to Qatari Riyals ~50million "
            "(INR 1,289.3 Million) from Example Telecom QPSC, a QAR ~42billion Qatari Public "
            "Shareholding Company.\n\n"
            "Kindly take the above information on record."
        )
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_textbox(pymupdf.Rect(50, 50, 550, 400), letter, fontsize=10)
        evidence = AnnexureParser.extract_evidence(doc.tobytes())
        doc.close()
        texts = [item["text"] for item in evidence]
        self.assertTrue(
            any("INR 1,289.3 Million" in item and "received a new order" in item for item in texts),
            texts,
        )
        result = evaluate_event(
            "ORDER_WIN",
            "Example Infra Ltd",
            "Awarding of order(s) or contract(s)",
            "\n".join(texts),
        )
        self.assertTrue(result["publish"], result)
