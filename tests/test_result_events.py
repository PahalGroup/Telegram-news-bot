import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock
from src.daemon import RealtimeDaemon
from src.state_store import StateStore, StateIntegrityError
from src.result_events import select_new_result_points

A = 'Consolidated net profit was INR 100 crore for Q2 FY27.'
B = 'Q2 FY27 Consolidated net profit stood at Rs.100 crores.'
NEW = 'Consolidated revenue was INR 900 crore for Q2 FY27.'

class ResultEventTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.snapshot = str(Path(self.tmp.name) / 'state.json')
        d = self.d = RealtimeDaemon.__new__(RealtimeDaemon)
        __import__('tests.support').support.install(self.d)
        d.state = StateStore(str(Path(self.tmp.name) / 'wire.db'), self.snapshot)
        d.dry_run = False
        d.cycle_count = d.total_dispatched = d.total_errors = 0
        d.dispatcher = MagicMock()
        d.dispatcher.send_message.return_value = 123
        d.filter_engine = MagicMock()
        d.filter_engine.normalize_entity_name.return_value = 'EXAMPLE'
        d.filter_engine.evaluate_stage_1.return_value = (True, '', {'company': 'Example', 'category': 'FINANCIAL_RESULTS'})
        d.poll_all_sources = MagicMock()
        d.check_fii_dii_routine = MagicMock()
        self.serial = 0

    def cycle(self, points, minute=0, company='Example Limited', timestamp=True):
        self.serial += 1
        self.d.poll_all_sources.return_value = [{'id': str(self.serial), 'source': 'NEWS',
            'company': company, 'title': 'Financial results', 'description': '\n'.join(points),
            'published_at': f'2026-11-02T07:{minute:02d}:00+00:00' if timestamp else '',
            'source_url': 'https://example.com/' + str(self.serial)}]
        self.d.filter_engine.evaluate_stage_2.return_value = {'publish': True, 'materiality_score': 90,
            'headline': 'EXAMPLE – FINANCIAL RESULTS', 'category': 'FINANCIAL_RESULTS',
            'summary_points': points}
        return self.d.process_cycle()

    def test_equivalent_cross_filing_fact_is_sent_once(self):
        self.assertEqual(self.cycle([A]), 1)
        self.assertEqual(self.cycle([B], 1, company='Example Ltd.'), 0)

    def test_only_incremental_fact_is_sent(self):
        self.cycle([A])
        self.assertEqual(self.cycle([B, NEW], 1), 1)
        message = self.d.dispatcher.send_message.call_args.args[0]
        self.assertIn('900', message)
        self.assertNotIn('100', message)

    def test_changed_value_and_reversion_are_updates(self):
        self.assertEqual(self.cycle([A]), 1)
        self.assertEqual(self.cycle([A.replace('100', '120')], 1), 1)
        self.assertEqual(self.cycle([A], 2), 1)
        self.assertIn('FINANCIAL RESULTS UPDATE', self.d.dispatcher.send_message.call_args.args[0])

    def test_older_value_cannot_replace_later_report(self):
        self.cycle([A.replace('100', '120')], 2)
        self.assertEqual(self.cycle([A], 1), 0)

    def test_different_company_scope_period_and_sign_are_preserved(self):
        self.cycle([A])
        for i, value in enumerate([A.replace('Consolidated', 'Standalone'),
                                   A.replace('Q2', 'Q3'), A.replace('100', '-100')], 1):
            self.assertEqual(self.cycle([value], i), 1)
        self.assertEqual(self.cycle([A], 4, company='Example Industries Limited'), 1)
        self.assertEqual(self.cycle([A], 5, company='Example Services Limited'), 1)

    def test_failed_send_is_not_recorded_as_a_published_fact(self):
        self.d.dispatcher.send_message.return_value = False
        self.assertEqual(self.cycle([A]), 0)
        self.assertEqual(self.d.state.published_records, [])
        self.d.dispatcher.send_message.return_value = 123
        self.assertEqual(self.cycle([B], 1), 1)

    def test_snapshot_restores_cross_filing_identity(self):
        self.cycle([A])
        self.d.state = StateStore(str(Path(self.tmp.name) / 'fresh.db'), self.snapshot)
        self.assertEqual(self.cycle([B], 1), 0)

    def test_missing_timestamp_does_not_enable_semantic_dedup(self):
        self.cycle([A], timestamp=False)
        self.assertEqual(self.cycle([B], timestamp=False), 1)

    def test_unknown_qualifiers_are_not_silently_discarded(self):
        self.cycle([A])
        point = B.rstrip('.') + ' including a one-time gain of INR 10 crore.'
        self.assertEqual(self.cycle([point], 1), 1)

    def test_malformed_event_stops_restart(self):
        self.cycle([A])
        data = json.loads(Path(self.snapshot).read_text())
        data['published_records'][0]['result_event']['facts'] = {'wrong': 'value'}
        Path(self.snapshot).write_text(json.dumps(data))
        with self.assertRaises(StateIntegrityError):
            StateStore(str(Path(self.tmp.name) / 'fresh.db'), self.snapshot)
