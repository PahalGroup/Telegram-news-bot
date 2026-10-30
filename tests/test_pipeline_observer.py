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

