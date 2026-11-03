import json
import pickle
import tempfile
import unittest
from pathlib import Path
from concurrent.futures import Future
from unittest.mock import MagicMock, patch
from src.state_store import StateStore, StateIntegrityError
from src.daemon import RealtimeDaemon
from sources.annexure_parser import AnnexureParser, PDFExtractionError
from sources.pdf_download import PDFDownloadError


class WorkRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.snapshot = str(Path(self.tmp.name) / 'state.json')
        self.state = StateStore(str(Path(self.tmp.name) / 'first.db'), self.snapshot)
        self.item = {'id': 'filing', 'source': 'BSE', 'company': 'Example',
                     'title': 'Financial results', 'description': '',
                     'pdf_url': 'https://example.com/filing.pdf'}

    def restored(self):
        return StateStore(str(Path(self.tmp.name) / 'fresh.db'), self.snapshot)

    def daemon(self, state):
        d = RealtimeDaemon.__new__(RealtimeDaemon)
        __import__('tests.support').support.install(d)
        d.state = state
        d.dry_run = False
        d.total_errors = d.total_dispatched = d.cycle_count = 0
        d.poll_all_sources = MagicMock(return_value=[])
        d.check_fii_dii_routine = MagicMock()
        d.dispatcher = MagicMock()
        d.filter_engine = MagicMock()
        d.filter_engine.should_drop.return_value = False
        d.filter_engine.evaluate_stage_1.return_value = (True, '', {'company': 'Example', 'category': 'GENERAL_WIRE'})
        d.filter_engine.normalize_entity_name.return_value = 'EXAMPLE'
        d.filter_engine.evaluate_stage_2.return_value = {'publish': True, 'materiality_score': 90,
            'category': 'GENERAL_WIRE', 'headline': 'EXAMPLE UPDATE',
            'summary_points': ['The company approved an investment of INR 100 crore.']}
        return d

    def test_discovery_survives_new_database_and_drops_unknown_metadata(self):
        self.state.stage_items([dict(self.item, client_token='do-not-store')])
        self.assertEqual(self.restored().pending_items['filing']['item'], self.item)
        self.assertNotIn('do-not-store', Path(self.snapshot).read_text())

    def test_definitive_send_failure_is_recovered_without_source_repetition(self):
        item = dict(self.item, source='NEWS', pdf_url='')
        self.state.stage_items([item])
        d = self.daemon(self.restored())
        d.dispatcher.send_message.return_value = False
        self.assertEqual(d.process_cycle(), 0)
        self.assertIn('filing', self.restored().pending_items)
        d.dispatcher.send_message.return_value = 123
        self.assertEqual(d.process_cycle(), 1)
        self.assertNotIn('filing', self.restored().pending_items)

    def test_failed_checkpoint_stops_before_delivery_and_remains_unhealthy(self):
        d = self.daemon(self.state)
        d.poll_all_sources.return_value = [dict(self.item, source='NEWS', pdf_url='')]
        with patch('src.state_store.os.replace', side_effect=OSError('disk full')):
            with self.assertRaises(StateIntegrityError):
                d.process_cycle()
        d.dispatcher.send_message.assert_not_called()
        with self.assertRaises(StateIntegrityError):
            self.state.save_state()

    def test_processing_preserves_raw_journal_and_observation_origin(self):
        d = self.daemon(self.state)
        d.poll_all_sources.return_value = [dict(self.item, _warm_start=False)]
        def prepare(items):
            self.assertFalse(items[0]['_warm_start'])
            items[0]['description'] = 'extracted evidence'
            return []
        d.prepare_items = prepare
        d.process_cycle()
        self.assertEqual(self.state.pending_items['filing']['item']['description'], '')
        self.assertNotIn('_warm_start', self.state.pending_items['filing']['item'])

    def test_shift_shutdown_checkpoints_late_sources_without_delivery(self):
        d = self.daemon(self.state)
        d.start_time = 0
        d.shift_seconds = 0
        d._source_pool = MagicMock()
        d.poll_all_sources.return_value = [self.item]
        d.run_daemon()
        d.poll_all_sources.assert_called_once_with(submit_sources=False)
        d.dispatcher.send_message.assert_not_called()
        self.assertIn('filing', self.restored().pending_items)

    def test_blocked_work_survives_restart_without_automatic_redownload(self):
        self.state.stage_items([self.item])
        self.state.defer_item('filing', blocked_reason='PDF_SIZE_LIMIT_EXCEEDED')
        self.state.save_state()
        d = self.daemon(self.restored())
        with patch('src.daemon.ProcessPoolExecutor') as pool:
            self.assertEqual(d.process_cycle(), 0)
            pool.assert_not_called()
        self.assertIn('filing', d._pdf_backlog)
        self.assertIn('filing', self.restored().pending_items)

    def test_transient_backoff_survives_restart(self):
        self.state.stage_items([self.item])
        self.state.defer_item('filing', retry_after=500)
        self.state.save_state()
        d = self.daemon(self.restored())
        with patch('src.daemon.time.time', return_value=499), patch('src.daemon.ProcessPoolExecutor') as pool:
            d.process_cycle()
            pool.assert_not_called()
        with patch('src.daemon.time.time', return_value=501), patch('src.daemon.ProcessPoolExecutor') as pool:
            pool.return_value.submit.return_value = Future()
            d.process_cycle()
            pool.return_value.submit.assert_called_once()

    def test_corrupt_pending_identity_stops_startup(self):
        self.state.stage_items([self.item])
        data = json.loads(Path(self.snapshot).read_text())
        data['pending_items']['filing']['item']['id'] = 'different'
        Path(self.snapshot).write_text(json.dumps(data))
        with self.assertRaises(StateIntegrityError):
            self.restored()

    def test_pdf_failure_classification_survives_process_serialization(self):
        for reason, retryable in [('PDF_SIZE_LIMIT_EXCEEDED', False), ('PDF_TRANSPORT_28', True)]:
            with patch('sources.pdf_download.download_pdf', side_effect=PDFDownloadError(reason, 1, retryable)):
                with self.assertRaises(PDFExtractionError) as raised:
                    AnnexureParser.extract_annexure_bullets(self.item['pdf_url'])
            restored = pickle.loads(pickle.dumps(raised.exception))
            self.assertEqual(restored.reason, reason)
            self.assertEqual(restored.retryable, retryable)
