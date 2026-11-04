import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from src.pipeline_observer import PipelineObserver, publication_time, distribution


class ObserverTests(unittest.TestCase):
    def test_unique_queue_counts_and_first_discovery_survive_repeated_polls(self):
        with tempfile.TemporaryDirectory() as directory:
            now = [0]
            observer = PipelineObserver(directory, clock=lambda: now[0])
            item = {'id': 'A', 'source': 'BSE', '_warm_start': True}
            observer.record('discovered', item)
            for n in range(5):
                now[0] += 1
                observer.record('queue_deferred', item, 'PDF_QUEUE_FULL')
                observer.record('discovered', item)
            now[0] = 20
            observer.record('ready', item)
            observer.record('already_seen', item)
            result = observer.finish()
            self.assertEqual(result['unique_items_discovered'], 1)
            self.assertEqual(result['unique_deferred_items'], 1)
            self.assertEqual(result['unique_deferred_without_ready'], 0)
            self.assertEqual(result['attempt_counts']['queue_deferred'], 5)
            self.assertEqual(result['status_counts'], {'ready': 1})
            self.assertEqual(result['discovery_to_ready']['p50_seconds'], 20)
            self.assertEqual(result['non_warm_start_discovery_to_ready']['samples'], 0)
            self.assertEqual(result['publication_to_discovery']['samples'], 0)
            row = json.loads((Path(directory) / 'pipeline-items.jsonl').read_text())
            self.assertEqual(row['deferral_attempts'], 5)

    def test_missing_and_ambiguous_dates_are_not_latency_samples(self):
        self.assertIsNone(publication_time('', 'BSE'))
        self.assertIsNone(publication_time('2026-10-30', 'RBI'))
        self.assertIsNone(publication_time('garbage', 'NSE'))
        self.assertIsNone(publication_time('2026-10-30T09:30:00', 'NEWS'))
        self.assertEqual(publication_time('30-Oct-2026 09:30:00', 'NSE').hour, 4)
        self.assertEqual(distribution([3, 1, 2, 100])['p95_seconds'], 100)

    def test_existing_run_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            observer = PipelineObserver(directory)
            observer.source('BSE', 'partial_error', count=2)
            summary = observer.finish(fatal_error_type='ExampleError')
            self.assertEqual(summary['source_failure_counts'], {'BSE': 1})
            with self.assertRaises(FileExistsError):
                PipelineObserver(directory)

    def test_daemon_records_rejection_without_delivery(self):
        from src.daemon import RealtimeDaemon
        with tempfile.TemporaryDirectory() as directory, patch('src.daemon.TelegramDispatcher') as dispatcher:
            daemon = RealtimeDaemon(dry_run=True)
            observer = PipelineObserver(directory)
            daemon.observer = observer
            daemon.poll_all_sources = lambda: [{'id': 'tax', 'source': 'BSE', 'company': 'Example',
                'title': 'Tax on 2nd Interim Dividend', 'description': 'Amount of dividend and Form 15H.'}]
            daemon.check_fii_dii_routine = lambda: None
            try:
                daemon.process_cycle()
                daemon.process_cycle()
                summary = observer.finish()
                self.assertEqual(summary['status_counts'], {'rejected': 1})
                self.assertEqual(summary['reason_counts'], {'DIVIDEND_TAX_PAPERWORK': 1})
                dispatcher.return_value.send_message.assert_not_called()
            finally:
                daemon._dry_state_dir.cleanup()

    def test_shadow_cycle_failure_still_writes_evidence(self):
        from scripts import shadow_smoke
        with tempfile.TemporaryDirectory() as directory, \
                patch('sys.argv', ['shadow_smoke', '--duration', '1', '--output-dir', directory]), \
                patch.object(shadow_smoke.RealtimeDaemon, 'process_cycle', side_effect=RuntimeError('test failure')), \
                patch('builtins.print'):
            with self.assertRaises(RuntimeError):
                shadow_smoke.main()
            summary = json.loads((Path(directory) / 'pipeline-summary.json').read_text())
            self.assertEqual(summary['status'], 'failed')
            self.assertEqual(summary['fatal_error_type'], 'RuntimeError')
            self.assertEqual(summary['telegram_attempts'], 0)



    def test_final_source_drain_collects_late_failure_without_new_requests(self):
        from concurrent.futures import Future
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        from src.daemon import RealtimeDaemon
        from scripts.shadow_smoke import run_status
        daemon = RealtimeDaemon.__new__(RealtimeDaemon)
        __import__('tests.support').support.install(daemon)
        for name in ('bse', 'nse', 'rbi', 'news', 'liveblog'):
            setattr(daemon, name, MagicMock())
        daemon.news.last_health = {}
        daemon.total_errors = 0
        daemon._source_pool = MagicMock()
        future = Future()
        future.set_exception(RuntimeError('late RSS failure'))
        daemon._source_pending = {'news': future}
        self.assertEqual(daemon.poll_all_sources(submit_sources=False), [])
        daemon._source_pool.submit.assert_not_called()
        self.assertEqual(daemon._source_pending, {})
        self.assertEqual(daemon.total_errors, 1)
        self.assertEqual(run_status(None, daemon.total_errors, 0, 0), 'degraded')

    def test_pending_work_cannot_be_a_completed_smoke(self):
        from scripts.shadow_smoke import run_status
        self.assertEqual(run_status(None, 0, 1, 0), 'incomplete')
        self.assertEqual(run_status(None, 0, 0, 1), 'incomplete')
        self.assertEqual(run_status(None, 0, 0, 0), 'completed')


class SmokeBacklogStatus(unittest.TestCase):
    def test_blocked_pdfs_do_not_make_the_smoke_incomplete(self):
        from types import SimpleNamespace
        from scripts.shadow_smoke import open_backlog, run_status
        d = SimpleNamespace(_pdf_backlog={"a": {}, "b": {}}, _pdf_blocked={"a": "PDF_SIZE_LIMIT_EXCEEDED", "b": "NO_RELIABLE_PDF_EVIDENCE"})
        self.assertEqual(open_backlog(d), 0)
        self.assertEqual(run_status(None, 0, 0, 0, open_backlog(d)), "completed")
        d._pdf_backlog["c"] = {}
        self.assertEqual(run_status(None, 0, 0, 0, open_backlog(d)), "incomplete")


class ManagementRedesignationEvidence(unittest.TestCase):
    def test_change_in_designation_is_publishable(self):
        from src.event_evidence import evaluate_event
        text = ("The Board on Tuesday, November 3, 2026 has, inter- alia, considered and approved the change in designation of "
                "Mr. Rajesh Sharma (DIN: 00515486) from Executive Chairman to Non- Executive Non-Independent Chairman of the "
                "Company with effect from January 1, 2027.")
        result = evaluate_event('MANAGEMENT_CHANGE', 'Ion Exchange', 'Change in Management', text)
        self.assertTrue(result['publish'], result['rejection_reason'])

    def test_unnamed_designation_change_still_withheld(self):
        from src.event_evidence import evaluate_event
        result = evaluate_event('MANAGEMENT_CHANGE', 'X', 'Change in Management',
                                'The company informed about change in designation of a director.')
        self.assertFalse(result['publish'])

    def test_postal_ballot_mention_does_not_drop_redesignation(self):
        from src.filter_engine import FilterEngine
        text = ("considered and approved the change in designation of Mr. Rajesh Sharma from Executive Chairman. "
                "The Company will seek shareholders' approval through postal ballot.")
        self.assertFalse(FilterEngine.should_drop(text))
        self.assertTrue(FilterEngine.should_drop("Notice of postal ballot for shareholders"))
