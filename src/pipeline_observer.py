"""Read-only item-level observation. It cannot publish or mutate the delivery ledger."""
import json
import math
import time
from collections import Counter
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo


def publication_time(value, source):
    if not value:
        return None
    # A date without a time cannot support second-level latency measurement.
    if len(str(value).strip()) <= 10:
        return None
    parsed = None
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(str(value))
        except (TypeError, ValueError):
            for fmt in ('%d-%b-%Y %H:%M:%S', '%d-%b-%Y %H:%M'):
                try:
                    parsed = datetime.strptime(str(value), fmt)
                    break
                except ValueError:
                    pass
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        if source not in ('BSE', 'NSE', 'RBI'):
            return None
        parsed = parsed.replace(tzinfo=ZoneInfo('Asia/Kolkata'))
    return parsed.astimezone(timezone.utc)


def distribution(values):
    values = sorted(values)
    if not values:
        return {'samples': 0, 'p50_seconds': None, 'p95_seconds': None}
    return {'samples': len(values),
            'p50_seconds': round(values[max(0, math.ceil(len(values)*0.5)-1)], 3),
            'p95_seconds': round(values[max(0, math.ceil(len(values)*0.95)-1)], 3)}


class PipelineObserver:
    def __init__(self, directory, clock=time.monotonic):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.started = clock()
        self.started_utc = datetime.now(timezone.utc).isoformat()
        self.items = {}
        self.attempts = Counter()
        self.source_polls = Counter()
        self.source_failures = Counter()
        self._emitted = set()
        self.events_path = self.directory / 'pipeline-events.jsonl'
        # A run must never silently overwrite or append to another run's evidence.
        self.events = self.events_path.open('x', encoding='utf-8')

    def _write(self, record):
        self.events.write(json.dumps(record, ensure_ascii=False) + '\n')
        self.events.flush()

    def source(self, name, status, count=None, elapsed_seconds=None):
        self.source_polls[name] += 1
        if status != 'ok':
            self.source_failures[name] += 1
        self._write({'event': 'source_poll', 'source': name, 'status': status, 'items': count,
                     'elapsed_seconds': elapsed_seconds, 'observed_at': datetime.now(timezone.utc).isoformat()})

    def record(self, stage, item, reason='', **details):
        key = str(item.get('id') or '')
        if not key:
            return
        now = self.clock()
        self.attempts[stage] += 1
        if key not in self.items:
            published = publication_time(item.get('published_at'), item.get('source'))
            wall = datetime.now(timezone.utc)
            age = (wall - published).total_seconds() if published else None
            self.items[key] = {
                'id': key, 'source': item.get('source'), 'company': item.get('company'),
                'title': item.get('title'), 'source_url': item.get('source_url') or item.get('pdf_url'),
                'published_at_raw': item.get('published_at'),
                'publication_to_discovery_seconds': age if age is not None and age >= 0 else None,
                'first_discovered_at': wall.isoformat(), '_first_monotonic': now,
                'warm_start': bool(item.get('_warm_start', False)), 'stages': {}, 'deferral_attempts': 0,
            }
        row = self.items[key]
        if stage == 'discovered' and stage in row['stages']:
            return
        if stage == 'queue_deferred':
            row['deferral_attempts'] += 1
        # Repeated IDs already in the ledger must not erase the original outcome.
        if stage not in ('discovered', 'already_seen') or 'status' not in row:
            row['status'], row['reason'] = stage, reason
        row['stages'].setdefault(stage, round(now - row['_first_monotonic'], 6))
        row.update(details)
        if stage == 'ready':
            row['discovery_to_ready_seconds'] = round(now - row['_first_monotonic'], 6)
        signature = (key, stage, reason)
        if signature not in self._emitted:
            self._write({'event': stage, 'id': key, 'reason': reason,
                         'elapsed_since_discovery_seconds': row['stages'][stage], **details})
            self._emitted.add(signature)

    def finish(self, **run_details):
        rows = [{k: v for k, v in row.items() if not k.startswith('_')} for row in self.items.values()]
        with (self.directory / 'pipeline-items.jsonl').open('w', encoding='utf-8') as out:
            for row in rows:
                out.write(json.dumps(row, ensure_ascii=False) + '\n')
        ready = [r for r in rows if 'discovery_to_ready_seconds' in r]
        summary = {
            'started_at_utc': self.started_utc, 'observation_seconds': round(self.clock()-self.started, 3),
            'unique_items_discovered': len(rows), 'unique_ready_items': len(ready),
            'status_counts': dict(Counter(r.get('status') for r in rows)),
            'reason_counts': dict(Counter(r['reason'] for r in rows if r.get('reason'))),
            'attempt_counts': dict(self.attempts),
            'unique_deferred_items': sum(r['deferral_attempts'] > 0 for r in rows),
            'unique_deferred_without_ready': sum(r['deferral_attempts'] > 0 and 'discovery_to_ready_seconds' not in r for r in rows),
            'source_poll_counts': dict(self.source_polls), 'source_failure_counts': dict(self.source_failures),
            'discovery_to_ready': distribution([r['discovery_to_ready_seconds'] for r in ready]),
            'non_warm_start_discovery_to_ready': distribution([r['discovery_to_ready_seconds'] for r in ready if not r['warm_start']]),
            'publication_to_discovery': distribution([r['publication_to_discovery_seconds'] for r in rows
                if not r['warm_start'] and r['publication_to_discovery_seconds'] is not None]),
            'measurement_limits': ['Ready is not Telegram delivery or verified accuracy.',
                'Unmatched reference stories are not automatically missed news.',
                'Discovery-to-ready includes queueing, processing and polling observation delay.',
                'Publication timestamps use source-supplied values; missing/future dates are excluded.',
                'Warm-start items are separated from subsequently discovered items.'],
            **run_details,
        }
        (self.directory / 'pipeline-summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
        self.events.close()
        return summary
