"""Public oil-flash regressions: explicit prices, incremental facts, no live sends."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock
from src.daemon import RealtimeDaemon
from src.filter_engine import FilterEngine
from src.market_quotes import extract_quotes, select_new_quotes
from src.state_store import StateStore, StateIntegrityError

BRENT = 'Brent crude futures fell to $111.84 per barrel, alongside a drop in US West Texas Intermediate crude.'
BOTH = ('Brent crude futures fell $1.59, or 1.40%, to $111.84 a barrel while '
        'US West Texas Intermediate crude was at $102.56 a barrel, down $1.56, or 1.50%.')


class QuoteExtractionTests(unittest.TestCase):
    def test_actual_rss_and_liveblog_share_brent_price(self):
        first, second = extract_quotes(BRENT), extract_quotes(BOTH)
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 2)
        self.assertEqual(first[0], second[0])
        self.assertEqual(second[1]['text'], 'WTI crude: $102.56 per barrel.')

    def test_change_amount_is_not_the_current_price(self):
        self.assertEqual(extract_quotes(BOTH)[0]['price'], '111.84')

    def test_per_barrel_change_is_not_mistaken_for_price(self):
        self.assertEqual(extract_quotes('Brent crude fell by $1.59 per barrel.'), [])

    def test_absolute_price_after_per_barrel_change_is_retained(self):
        quotes = extract_quotes('Brent crude fell by $1.59 per barrel to $111.84 per barrel.')
        self.assertEqual(quotes[0]['price'], '111.84')

    def test_unlabelled_title_price_cannot_establish_benchmark(self):
        engine = FilterEngine(config_path='/nonexistent/rules.json')
        output = engine.evaluate_stage_2({'source': 'NEWS', 'company': '', 'category': 'POLICY_MACRO',
            'title': 'Oil price today: crude oil falls to $111. What are experts saying?',
            'description': 'Oil prices fell, impacting Brent and WTI crude significantly.'})
        self.assertFalse(output['publish'])

    def test_forecasts_history_wrong_units_and_conflicting_quotes_are_withheld(self):
        for text in [
            'Analysts expect Brent crude to $120 per barrel.',
            'Yesterday Brent crude was at $111.84 per barrel.',
            'Brent crude futures for December were at $111.84 per barrel.',
            'Brent crude fell by $1.59.',
            'Brent crude was at $111.84 per tonne.',
            'Brent crude was at $111.84 per barrel. Brent crude was at $112 per barrel.',
        ]:
            with self.subTest(text=text):
                self.assertEqual(extract_quotes(text), [])

    def test_company_headline_without_extracted_entity_is_not_an_oil_quote(self):
        from src.market_quotes import is_quote_story
        self.assertFalse(is_quote_story({'source': 'NEWS', 'company': '',
            'title': 'Oil India falls as crude prices decline'}))

    def test_missing_publication_time_does_not_enable_price_deduplication(self):
        quotes = extract_quotes(BRENT)
        self.assertEqual(select_new_quotes(quotes, {'source': 'NEWS'}, []), (quotes, None))

    def test_oil_company_result_is_not_a_market_quote_story(self):
        from src.market_quotes import is_quote_story
        self.assertFalse(is_quote_story({'source': 'NEWS', 'company': 'Oil India',
                                        'title': 'Oil India profit falls'}))


class QuoteDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = str(Path(self.tmp.name) / 'wire.db')
        self.snapshot = str(Path(self.tmp.name) / 'state.json')
        d = self.daemon = RealtimeDaemon.__new__(RealtimeDaemon)
        d.state = StateStore(self.db, self.snapshot)
        d.dry_run = False
        d.cycle_count = d.total_dispatched = d.total_errors = 0
        d.dispatcher = MagicMock()
        d.dispatcher.send_message.return_value = 123
        d.filter_engine = FilterEngine(config_path='/nonexistent/rules.json')
        d.check_fii_dii_routine = MagicMock()
        d.poll_all_sources = MagicMock()
        self.number = 0

    def cycle(self, description, minute=0, date='2026-11-02', source='NEWS'):
        self.number += 1
        self.daemon.poll_all_sources.return_value = [{
            'id': 'quote-' + str(self.number), 'source': source, 'company': '',
            'title': 'Oil slips more than 1% as talks continue on ending Iran war',
            'description': description, 'published_at': f'{date}T07:{minute:02d}:00+00:00',
            'source_url': 'https://example.com/story/' + str(self.number)}]
        return self.daemon.process_cycle()

    def test_reworded_same_price_is_skipped(self):
        self.assertEqual(self.cycle(BRENT), 1)
        self.assertEqual(self.cycle('Brent crude traded at $111.84 a barrel.', 1, source='LIVEBLOG'), 0)

    def test_added_wti_quote_sends_only_the_new_benchmark(self):
        self.assertEqual(self.cycle(BRENT), 1)
        self.assertEqual(self.cycle(BOTH, 1, source='LIVEBLOG'), 1)
        post = self.daemon.dispatcher.send_message.call_args.args[0]
        self.assertIn('WTI crude: $102.56', post)
        self.assertNotIn('Brent crude:', post)

    def test_changed_price_and_return_to_earlier_price_both_survive(self):
        self.assertEqual(self.cycle(BRENT), 1)
        self.assertEqual(self.cycle('Brent crude traded at $112 a barrel.', 1), 1)
        self.assertEqual(self.cycle(BRENT, 2), 1)
        self.assertEqual(self.daemon.dispatcher.send_message.call_count, 3)

    def test_older_quote_cannot_overwrite_newer_price(self):
        self.assertEqual(self.cycle('Brent crude traded at $112 a barrel.', 2), 1)
        self.assertEqual(self.cycle(BRENT, 1), 0)

    def test_new_day_quote_remains_eligible(self):
        self.assertEqual(self.cycle(BRENT), 1)
        self.assertEqual(self.cycle(BRENT, date='2026-11-03'), 1)

    def test_failed_delivery_does_not_claim_price_was_reported(self):
        self.daemon.dispatcher.send_message.return_value = False
        self.assertEqual(self.cycle(BRENT), 0)
        self.daemon.dispatcher.send_message.return_value = 123
        self.assertEqual(self.cycle(BRENT, 1), 1)

    def test_quote_state_survives_snapshot_restore(self):
        self.assertEqual(self.cycle(BRENT), 1)
        self.daemon.state = StateStore(str(Path(self.tmp.name) / 'restored.db'), self.snapshot)
        self.assertEqual(self.cycle(BRENT, 1, source='LIVEBLOG'), 0)

    def test_malformed_quote_state_stops_startup(self):
        self.assertEqual(self.cycle(BRENT), 1)
        data = json.loads(Path(self.snapshot).read_text())
        data['published_records'][-1]['market_quote_event']['quotes'][0]['price'] = 'not a price'
        Path(self.snapshot).write_text(json.dumps(data))
        with self.assertRaises(StateIntegrityError):
            StateStore(str(Path(self.tmp.name) / 'restored.db'), self.snapshot)
