"""Backpressure must not depend on a source repeating a discovered item."""
import unittest
from concurrent.futures import Future
from unittest.mock import MagicMock, patch
from src.daemon import RealtimeDaemon
from scripts.shadow_smoke import run_status

def filing(i):
    return {'id': str(i), 'source': 'BSE', 'title': 'Financial Results',
            'pdf_url': f'https://example.com/{i}.pdf'}

class DeferredPDFTests(unittest.TestCase):
    def daemon(self):
        d = RealtimeDaemon.__new__(RealtimeDaemon)
        d.state = MagicMock()
        d.state.is_seen.return_value = False
        d.filter_engine = MagicMock()
        d.filter_engine.should_drop.return_value = False
        d._pdf_pending, d._pdf_retry = {}, {}
        d._pdf_pool = MagicMock()
        d._pdf_pool.submit.side_effect = lambda *args: Future()
        d.total_errors = 0
        return d

    def finish(self, d):
        for item, future in d._pdf_pending.values():
            future.set_result(('', ['Revenue was INR 100 crore in Q2 FY27.']))

    def test_single_source_batch_survives_full_queue_and_text_is_not_blocked(self):
        d = self.daemon()
        flash = {'id': 'flash', 'source': 'NEWS', 'title': 'Policy update'}
        self.assertEqual(d.prepare_items([filing(i) for i in range(35)] + [flash]), [flash])
        self.finish(d)
        first = d.prepare_items([])  # Source never repeats its original batch.
        self.finish(d)
        second = d.prepare_items([])
        self.assertEqual({p['id'] for p in first + second}, {str(i) for i in range(35)})
        self.assertEqual(d._pdf_pool.submit.call_count, 35)
        self.assertFalse(d._pdf_backlog)

    def test_old_deferred_items_take_available_slots_before_new_arrivals(self):
        d = self.daemon()
        d.prepare_items([filing(i) for i in range(34)])
        self.finish(d)
        d.prepare_items([filing(i) for i in range(34, 70)])
        self.assertIn('32', d._pdf_pending)
        self.assertIn('33', d._pdf_pending)

    def test_duplicate_input_does_not_schedule_same_pdf_twice(self):
        d = self.daemon()
        d.prepare_items([filing(i) for i in range(34)])
        d.prepare_items([filing(33), filing(33)])
        self.finish(d)
        d.prepare_items([])
        self.assertEqual(d._pdf_pool.submit.call_count, 34)

    def test_failed_item_retries_after_backoff_without_source_repetition(self):
        d = self.daemon()
        with patch('src.daemon.time.time', return_value=100):
            d.prepare_items([filing('retry')])
            d._pdf_pending['retry'][1].set_exception(RuntimeError('network timeout'))
            self.assertEqual(d.prepare_items([]), [])
        with patch('src.daemon.time.time', return_value=399):
            d.prepare_items([])
            self.assertEqual(d._pdf_pool.submit.call_count, 1)
        with patch('src.daemon.time.time', return_value=401):
            d.prepare_items([])
            self.assertEqual(d._pdf_pool.submit.call_count, 2)
        d.state.mark_seen.assert_not_called()

    def test_overload_is_visible_and_not_marked_consumed(self):
        d = self.daemon()
        with self.assertRaisesRegex(RuntimeError, 'backlog limit'):
            d.prepare_items([filing(i) for i in range(545)])
        self.assertEqual(len(d._pdf_pending), 32)
        self.assertEqual(len(d._pdf_backlog), 512)
        self.assertEqual(d.total_errors, 1)
        d.state.mark_seen.assert_not_called()

    def test_retained_work_prevents_false_complete_status(self):
        self.assertEqual(run_status(None, 0, 0, 0, pending_backlog=1), 'incomplete')
