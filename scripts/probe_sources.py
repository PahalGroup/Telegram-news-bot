"""Read-only source probe. No Telegram dispatcher, secrets, or news text output."""
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sources.bse_fetcher import BSEFetcher
from sources.nse_fetcher import NSEFetcher
from sources.rbi_fetcher import RBIFetcher
from sources.news_fetcher import NewsFetcher
from sources.liveblog_fetcher import LiveBlogFetcher

def main():
    news = NewsFetcher()
    calls = {
        'bse': lambda: BSEFetcher().fetch_announcements(skip_pdf=True),
        'nse': lambda: NSEFetcher().fetch_announcements(),
        'rbi': lambda: RBIFetcher().fetch_press_releases(),
        'rss': news.fetch_all_feeds,
        'liveblog': lambda: LiveBlogFetcher().fetch_live_flashes(),
    }
    def probe(pair):
        name, call = pair
        started = time.monotonic()
        try:
            count = len(call())
            result = {'status': 'items' if count else 'empty_unverified', 'items': count}
            if name == 'rss':
                result['feeds'] = news.last_health
                if any(h['status'] == 'error' for h in news.last_health.values()):
                    result['status'] = 'degraded'
        except Exception as exc:
            result = {'status': 'error', 'error': str(exc)}
        return name, dict(result, elapsed_seconds=round(time.monotonic()-started, 3))
    with ThreadPoolExecutor(max_workers=5) as pool:
        results = dict(pool.map(probe, calls.items()))
    report = {'checked_at_utc': datetime.now(timezone.utc).isoformat(), 'sources': results,
              'note': 'Empty is not proof of health. This is connectivity, not coverage or latency validation.'}
    print(json.dumps(report, indent=2))
    Path('source-probe.json').write_text(json.dumps(report, indent=2))
    return int(any(r['status'] in ('error', 'degraded') for r in results.values()))

if __name__ == '__main__':
    sys.exit(main())
