"""Regressions from prepared public posts. No network or Telegram calls."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock
from src.filter_engine import FilterEngine
from src.daemon import RealtimeDaemon
from src.state_store import StateStore


class ResultAssociationTests(unittest.TestCase):
    def result(self, text):
        return FilterEngine(config_path='/nonexistent/rules.json').evaluate_stage_2({
            'company': 'Example', 'category': 'FINANCIAL_RESULTS',
            'title': 'Q2 FY27 financial results', 'description': text})

    def test_retention_rate_is_not_revenue_growth(self):
        text = ('Revenue uptick in H1 as we built the book ahead of it: new client wins in India, '
                'the UK and Thailand, 94% retention, and our platform live in three Southeast Asian markets')
        self.assertFalse(self.result(text)['publish'])

    def test_capex_and_potential_revenue_are_not_reported_results(self):
        text = ('The board has approved expansion of Organolithium facility at Patancheru, Telangana '
                'from 300 MTPA to 600 MTPA with proposed estimated investment of upto INR 45 crore '
                'with additional revenue potential of INR 150 to INR 200 crore based on lithium prices')
        self.assertFalse(self.result(text)['publish'])

    def test_claim_amount_cannot_borrow_sales_word(self):
        text = ('Total on-account claim received/ receivable stands at Rs. 316 crore, inclusive '
                'of above accepted claim and salvage scrap sales to date.')
        self.assertFalse(self.result(text)['publish'])

    def test_other_metrics_cannot_supply_revenue_value(self):
        for text in ['Revenue grew with customer retention at 94%.',
                     'Revenue for 15 hospitals improved with capacity at 300 beds.',
                     'Revenue from operations grew; premium retention was at INR 250 crore.']:
            with self.subTest(text=text):
                self.assertFalse(self.result(text)['publish'])

    def test_bound_values_signs_scopes_and_periods_stay_verbatim(self):
        for text in ['Operating revenue rose 13.0% YoY to ₹478.0 Cr.',
                     'EBITDA for Q2 FY27 (consolidated) stood at INR 45 crore, higher by 51% Y-o-Y.',
                     'Net profit was INR -1.23 crore, down 54.40% YoY.',
                     'Q2 Revenue from Operations grew 22.9% YoY to INR 6,629 Mn.',
                     'EBITDA margins expanded 103 bps to 20.3%.',
                     'Consolidated net profit: INR (12.50) crore; Quarter ended 30-09-2026.']:
            with self.subTest(text=text):
                self.assertEqual(self.result(text)['summary_points'], [text])

    def test_inline_premium_heading_is_not_joined_to_revenue(self):
        fact = 'Consolidated Operating Revenue: Q2 ₹241.5 Cr (+3.9%) · H1 ₹478.0 Cr (+13.0%)'
        text = fact + ' Premiums on our infrastructure, H1: ₹24,752 Cr, about a third of India’s health'
        self.assertEqual(self.result(text)['summary_points'], [fact])


class PublicationIdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = str(Path(self.tmp.name) / 'wire.db')
        self.snapshot = str(Path(self.tmp.name) / 'state.json')
        self.daemon = RealtimeDaemon.__new__(RealtimeDaemon)
        __import__('tests.support').support.install(self.daemon)
        d = self.daemon
        d.state = StateStore(self.db, self.snapshot)
        d.dry_run = False
        d.cycle_count = d.total_dispatched = d.total_errors = 0
        d.dispatcher = MagicMock()
        d.dispatcher.send_message.return_value = 123
        d.filter_engine = MagicMock()
        d.filter_engine.normalize_entity_name.return_value = 'EXAMPLE'
        d.filter_engine.evaluate_stage_1.return_value = (True, '', {
            'company': 'Example', 'category': 'FINANCIAL_RESULTS'})
        self.evaluation = {'publish': True, 'materiality_score': 90,
            'category': 'FINANCIAL_RESULTS', 'headline': 'EXAMPLE – FINANCIAL RESULTS',
            'summary_points': ['Consolidated net profit was INR 100 crore for Q2 FY27.']}
        d.filter_engine.evaluate_stage_2.return_value = self.evaluation
        d.check_fii_dii_routine = MagicMock()
        d.poll_all_sources = MagicMock()

    def cycle(self, number):
        self.daemon.poll_all_sources.return_value = [{
            'id': str(number), 'source': 'NEWS', 'title': 'Separate source item ' + str(number),
            'description': 'Separate source wording ' + str(number),
            'source_url': 'https://example.com/source/' + str(number)}]
        return self.daemon.process_cycle()

    def test_identical_content_with_different_source_links_is_sent_once(self):
        self.assertEqual(self.cycle(1), 1)
        self.assertEqual(self.cycle(2), 0)
        self.daemon.dispatcher.send_message.assert_called_once()

    def test_changed_amount_is_not_suppressed(self):
        self.assertEqual(self.cycle(1), 1)
        self.evaluation['summary_points'] = ['Consolidated net profit was INR 120 crore for Q2 FY27.']
        self.assertEqual(self.cycle(2), 1)

    def test_changed_sign_and_period_remain_eligible(self):
        self.assertEqual(self.cycle(1), 1)
        self.evaluation['summary_points'] = ['Consolidated net profit was INR -100 crore for Q2 FY27.']
        self.assertEqual(self.cycle(2), 1)
        self.evaluation['summary_points'] = ['Consolidated net profit was INR 100 crore for Q3 FY27.']
        self.assertEqual(self.cycle(3), 1)

    def test_failed_send_does_not_suppress_another_source(self):
        self.daemon.dispatcher.send_message.return_value = False
        self.assertEqual(self.cycle(1), 0)
        self.daemon.dispatcher.send_message.return_value = 123
        self.assertEqual(self.cycle(2), 1)

    def test_identity_survives_snapshot_restore(self):
        self.assertEqual(self.cycle(1), 1)
        self.daemon.state = StateStore(str(Path(self.tmp.name) / 'restored.db'), self.snapshot)
        self.assertEqual(self.cycle(2), 0)

    def test_new_fact_in_same_headline_is_not_suppressed(self):
        self.assertEqual(self.cycle(1), 1)
        self.evaluation['summary_points'].append('Consolidated revenue was INR 900 crore for Q2 FY27.')
        self.assertEqual(self.cycle(2), 1)
