"""
Production Daemon: Real-Time Continuous Polling Engine (src/daemon.py)
SINGLE source of truth for GitHub Actions production.

Concurrent polling loop (not a latency guarantee):
- Runs for configurable duration per workflow run (default: 54 min)
- Polls upstream endpoints concurrently, with a 12-second pause between cycles
- Stage 1: Deterministic Hard Filter (src/filter_engine.py)
- Stage 2: Few-Shot Calibrated AI Materiality Gate
- Pixel-Accurate Formatter (Telegram HTML)
- Persistent state management via src.state_store
- Workflow-owned handoff after state persistence; startup gaps remain possible
"""

import os
import sys
import time
import argparse
import logging
import tempfile
from concurrent.futures import ThreadPoolExecutor
from src.quality import fingerprint
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List

# Ensure console supports UTF-8
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("RealtimeDaemon")

# Imports with graceful fallback
from src.filter_engine import FilterEngine
from src.formatter import Formatter
from src.state_store import StateStore, StateIntegrityError
from telegram_dispatcher import TelegramDispatcher

# Source fetchers
from sources.bse_fetcher import BSEFetcher
from sources.nse_fetcher import NSEFetcher
from sources.rbi_fetcher import RBIFetcher
from sources.news_fetcher import NewsFetcher
from sources.liveblog_fetcher import LiveBlogFetcher


class RealtimeDaemon:
    def __init__(self, dry_run: bool = False, shift_seconds: int = 3240):
        self.dry_run = dry_run
        self.shift_seconds = shift_seconds
        self.start_time = time.time()

        self.dispatcher = TelegramDispatcher()
        if not dry_run and not self.dispatcher.is_live:
            raise RuntimeError("Live mode requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHANNEL_ID.")
        # Dry runs must never consume the production dedup ledger.
        self._dry_state_dir = tempfile.TemporaryDirectory(prefix="market-wire-dry-") if dry_run else None
        if self._dry_state_dir:
            self.state = StateStore(
                db_path=os.path.join(self._dry_state_dir.name, "market_wire.db"),
                json_state_path=os.path.join(self._dry_state_dir.name, "daemon_state.json"),
            )
        else:
            self.state = StateStore()
        self.filter_engine = FilterEngine()

        # Ingest Sources
        self.bse = BSEFetcher()
        self.nse = NSEFetcher()
        self.rbi = RBIFetcher()
        self.news = NewsFetcher()
        self.liveblog = LiveBlogFetcher()

        self.poll_interval = 12  # Sub-15-second continuous polling
        self.fii_posted_today = False
        self.cycle_count = 0
        self.total_dispatched = 0
        self.total_errors = 0

    def poll_all_sources(self, wait_sources=False) -> List[Dict[str, Any]]:
        """Fetch raw announcements from ALL upstream sources with correct method names."""
        raw_items = []
        # Keep one request per adapter in flight. Drain only completed requests;
        # a slow feed must not hold up already available exchange announcements.
        if not hasattr(self, "_source_pool"):
            self._source_pool = ThreadPoolExecutor(max_workers=5)
            self._source_pending = {}
        calls = {
            "bse": lambda: self.bse.fetch_announcements(skip_pdf=True),
            "nse": self.nse.fetch_announcements,
            "rbi": self.rbi.fetch_press_releases,
            "news": self.news.fetch_all_feeds,
            "liveblog": self.liveblog.fetch_live_flashes,
        }
        for name, call in calls.items():
            if name not in self._source_pending:
                self._source_pending[name] = self._source_pool.submit(call)
        futures = self._source_pending.copy()
        if wait_sources:
            from concurrent.futures import wait
            wait(futures.values())
        completed = {name for name, future in futures.items() if future.done()}
        for name in completed:
            del self._source_pending[name]

        # 1. BSE Corporate Announcements
        try:
            bse_items = (futures["bse"].result() or []) if "bse" in completed else []
            for it in bse_items:
                raw_items.append({
                    "id": f"BSE_{it.get('newsid', it.get('raw_subject', ''))}",
                    "source": "BSE",
                    "company": it.get("company", ""),
                    "title": it.get("subject", "") or it.get("raw_subject", ""),
                    "description": f"{it.get('headline', '')} {it.get('more', '')}".strip(),
                    "pdf_url": it.get("pdf_url"),
                    "raw_subject": it.get("raw_subject", ""),
                    "source_url": it.get("pdf_url"),
                    "published_at": it.get("news_dt"),
                })
            if "bse" in completed:
                page_errors = getattr(self.bse, "last_health", {}).get("page_errors", {})
                if page_errors:
                    self.total_errors += len(page_errors)
                    logger.warning("BSE page failures: %s", page_errors)
                logger.info(f"[BSE] Fetched {len(bse_items)} items")
        except Exception as e:
            logger.warning(f"BSE poll error: {e}")
            self.total_errors += 1

        # 2. NSE Corporate Filings
        try:
            nse_items = (futures["nse"].result() or []) if "nse" in completed else []
            for it in nse_items:
                symbol = it.get("symbol", "")
                subject = it.get("desc", "") or it.get("subject", "")
                company_name = it.get("sm_name", "") or symbol
                attchmnt_text = it.get("attchmntText", "") or it.get("details", "")
                title = attchmnt_text or subject
                pdf_url = it.get("attchmntFile")
                seq_id = it.get("seq_id") or fingerprint(symbol, str(pdf_url or "") + str(it.get("dt", "")) + title)

                raw_items.append({
                    "id": f"NSE_{symbol}_{seq_id}",
                    "source": "NSE",
                    "company": company_name,
                    "title": title,
                    "description": attchmnt_text,
                    "pdf_url": pdf_url,
                })
            if "nse" in completed:
                logger.info(f"[NSE] Fetched {len(nse_items)} items")
        except Exception as e:
            logger.warning(f"NSE poll error: {e}")
            self.total_errors += 1

        # 3. RBI Press Releases
        try:
            rbi_items = (futures["rbi"].result() or []) if "rbi" in completed else []
            for it in rbi_items:
                raw_items.append({
                    "id": "RBI_" + fingerprint("", it.get("link") or it.get("title", "")),
                    "source": "RBI",
                    "company": "RESERVE BANK OF INDIA",
                    "title": it.get("title", ""),
                    "description": it.get("description", ""),
                    "source_url": it.get("link"),
                    "published_at": it.get("pub_date"),
                })
            if "rbi" in completed:
                logger.info(f"[RBI] Fetched {len(rbi_items)} items")
        except Exception as e:
            logger.warning(f"RBI poll error: {e}")
            self.total_errors += 1

        # 4. News RSS Wires (Economic Times, Moneycontrol, Mint, BusinessLine)
        try:
            news_items = (futures["news"].result() or []) if "news" in completed else []
            for it in news_items:
                raw_items.append({
                    "id": f"NEWS_{it.get('link', it.get('title', '')[:40])}",
                    "source": "NEWS",
                    "company": "",  # Extracted by filter
                    "title": it.get("title", ""),
                    "description": it.get("description", ""),
                    "source_url": it.get("link", ""),
                })
            if "news" in completed:
                failures = [url for url, health in getattr(self.news, "last_health", {}).items()
                            if health.get("status") == "error"]
                if failures:
                    self.total_errors += len(failures)
                    logger.warning("RSS sources failed: %s", failures)
                logger.info(f"[NEWS] Fetched {len(news_items)} items")
        except Exception as e:
            logger.warning(f"News poll error: {e}")
            self.total_errors += 1

        # 5. LiveBlog Breaking (Moneycontrol real-time flashes)
        try:
            lb_items = (futures["liveblog"].result() or []) if "liveblog" in completed else []
            for it in lb_items:
                raw_items.append({
                    "id": f"LIVE_{it.get('id', it.get('title', '')[:30])}",
                    "source": "LIVEBLOG",
                    "company": "",
                    "title": it.get("title", ""),
                    "description": it.get("description", ""),
                    "source_url": it.get("link", ""),
                })
            if "liveblog" in completed:
                logger.info(f"[LIVEBLOG] Fetched {len(lb_items)} items")
        except Exception as e:
            logger.warning(f"LiveBlog poll error: {e}")
            self.total_errors += 1

        return raw_items

    def check_fii_dii_routine(self):
        """Check and dispatch daily FII/DII activity on trading weekdays between 18:00 and 22:00 IST."""
        now_utc = datetime.now(timezone.utc)
        ist_now = now_utc + timedelta(hours=5, minutes=30)

        # Do not dispatch on weekends (Saturday / Sunday) when cash markets are closed
        if ist_now.weekday() in (5, 6):
            return

        today_str = ist_now.strftime("%d-%b-%Y").upper()
        fii_key = f"FII_DII_{today_str}"
        if self.state.is_seen(fii_key):
            self.fii_posted_today = True
            return

        if 18 <= ist_now.hour <= 22:
            try:
                fii_data = self.nse.fetch_fii_dii() or []
                if fii_data and isinstance(fii_data, list) and len(fii_data) >= 2:
                    flows = {}
                    for entry in fii_data:
                        if str(entry.get("date", "")).upper() != today_str:
                            continue
                        cat = str(entry.get("category", "")).upper()
                        raw_net = str(entry.get("netValue", "")).replace(",", "").strip()
                        try:
                            net = Decimal(raw_net)
                        except InvalidOperation:
                            continue
                        if not net.is_finite():
                            continue
                        key = "FII" if ("FII" in cat or "FPI" in cat) else ("DII" if "DII" in cat else None)
                        if key:
                            flows[key] = net
                    if set(flows) != {"FII", "DII"}:
                        logger.warning("FII/DII data missing, invalid, or stale; waiting for today's figures.")
                        return
                    fii_net, dii_net = flows["FII"], flows["DII"]
                    trade_date = today_str
                    formatted_msg = Formatter.format_fii_dii(trade_date, fii_net, dii_net)
                    logger.info(f"Posting daily FII/DII flows for {trade_date}...")
                    if not self.dry_run:
                        if not self.dispatcher.send_message(formatted_msg, parse_mode="HTML"):
                            self.total_errors += 1
                            logger.error("FII/DII delivery failed; leaving it eligible for retry.")
                            return
                    else:
                        print(f"\n[DRY RUN FII/DII DISPATCH]:\n{formatted_msg}\n")
                    self.state.mark_seen(fii_key, "NSE_FLOWS")
                    self.state.record_published(fii_key, f"FII/DII ACTIVITY – {trade_date}", "FII_DII_FLOWS")
                    self.fii_posted_today = True
            except StateIntegrityError:
                raise
            except Exception as e:
                self.total_errors += 1
                logger.warning(f"FII/DII fetch routine failed: {e}")

    def prepare_items(self, items, wait_pdfs=False):
        """Bounded PDF queue: slow filings never block text-only flashes.
        Failed/unsupported PDFs remain unconsumed and retry after a backoff.
        """
        if not hasattr(self, "_pdf_pending"):
            self._pdf_pending = {}
            self._pdf_retry = {}
            self._pdf_pool = None
        ready = []
        for key, (item, future) in list(self._pdf_pending.items()):
            if not future.done():
                continue
            del self._pdf_pending[key]
            try:
                lead, bullets = future.result()
            except Exception:
                logger.exception("PDF worker failed: %s", key)
                bullets = []
            if bullets:
                item["description"] = "\n".join(bullets)
                ready.append(item)
            else:
                self._pdf_retry[key] = time.time() + 300
                logger.warning("Withheld %s: no reliable PDF evidence", key)
        for item in items:
            key = item.get("id")
            if not key or self.state.is_seen(key):
                continue
            url = str(item.get("pdf_url") or "")
            is_pdf = url.lower().split("?")[0].endswith(".pdf")
            if is_pdf and item.get("source") in ("BSE", "NSE"):
                context = f"{item.get('title', '')} {item.get('raw_subject', '')}"
                if self.filter_engine.should_drop(context):
                    self.state.mark_seen(key, item.get("source", "UNKNOWN"))
                    continue
                if any(r.get("id") == key for r in ready):
                    continue
                if key in self._pdf_pending or self._pdf_retry.get(key, 0) > time.time():
                    continue
                if len(self._pdf_pending) >= 32:
                    continue
                if self._pdf_pool is None:
                    self._pdf_pool = ThreadPoolExecutor(max_workers=3)
                from sources.annexure_parser import AnnexureParser
                self._pdf_pending[key] = (item, self._pdf_pool.submit(
                    AnnexureParser.extract_annexure_bullets, url))
            else:
                ready.append(item)
        if wait_pdfs and self._pdf_pending:
            from concurrent.futures import wait
            wait([future for _, future in self._pdf_pending.values()], timeout=60)
            ready.extend(self.prepare_items([]))
            if self._pdf_pending:
                self.total_errors += len(self._pdf_pending)
                logger.error("Single-pass PDF timeout: %s pending filings", len(self._pdf_pending))
        return ready

    def process_cycle(self, wait_sources=False) -> int:
        """Executes a single end-to-end polling, filtration, formatting, and dispatch pass."""
        self.cycle_count += 1
        raw_items = self.poll_all_sources(wait_sources=True) if wait_sources else self.poll_all_sources()
        items = self.prepare_items(raw_items, wait_pdfs=True) if wait_sources else self.prepare_items(raw_items)
        dispatched_count = 0

        for it in items:
            item_id = it.get("id")
            if not item_id or self.state.is_seen(item_id):
                continue

            # Stage 1: Deterministic Hard Filter
            try:
                passed_s1, reason_s1, candidate = self.filter_engine.evaluate_stage_1(it)
            except Exception as e:
                logger.debug(f"Stage 1 error for {item_id}: {e}")
                self.total_errors += 1
                continue

            if not passed_s1:
                self.state.mark_seen(item_id, it.get("source", "UNKNOWN"))
                continue

            # Compare complete claims; distinct events for the same company remain eligible.
            norm_company = self.filter_engine.normalize_entity_name(candidate.get("company", ""))
            category = candidate.get("category", "")
            event_fp = fingerprint(norm_company, it.get("title", "") + "\n" + it.get("description", ""))
            if self.state.is_seen(item_id, fingerprint=event_fp):
                self.state.mark_seen(item_id, it.get("source", "UNKNOWN"))
                continue

            # Stage 2: Few-Shot Calibrated AI Materiality Gate
            try:
                eval_data = self.filter_engine.evaluate_stage_2(candidate)
            except Exception as e:
                logger.warning(f"Stage 2 error for {item_id}: {e}")
                self.total_errors += 1
                continue

            if not eval_data.get("publish", False) or eval_data.get("materiality_score", 0) < 70:
                self.state.mark_seen(item_id, it.get("source", "UNKNOWN"))
                continue

            # Format via pixel-accurate Telegram Formatter
            formatted_post = Formatter.format_post(eval_data, it)
            if not Formatter.validate_completeness(formatted_post):
                logger.info(f"Skipping incomplete/half post ({eval_data.get('headline')}): failed completeness validation.")
                self.state.mark_seen(item_id, it.get("source", "UNKNOWN"))
                continue

            post_fp = fingerprint("", formatted_post)
            if self.state.is_seen(item_id, fingerprint=post_fp):
                self.state.mark_seen(item_id, it.get("source", "UNKNOWN"))
                continue

            # Publish to Telegram
            if not self.dry_run:
                try:
                    delivery = Formatter.execute_with_retry(
                        self.dispatcher.send_message,
                        formatted_post,
                        parse_mode="HTML"
                    )
                    if not delivery:
                        self.total_errors += 1
                        logger.error("Telegram delivery failed for %s; leaving it eligible for retry.", item_id)
                        continue
                    logger.info(f"Published: {eval_data.get('headline')}")
                except Exception as e:
                    self.total_errors += 1
                    logger.error(f"Failed to dispatch to Telegram: {e}")
                    continue
            else:
                print(f"\n[DRY RUN DISPATCH - Score {eval_data.get('materiality_score')}]:\n{formatted_post}\n{'-'*50}")

            # Persist state and cooldown
            self.state.mark_seen(item_id, it.get("source", "UNKNOWN"), fingerprint=event_fp)
            self.state.mark_seen(item_id, it.get("source", "UNKNOWN"), fingerprint=post_fp)
            self.state.record_cooldown(norm_company, category)
            self.state.record_published(item_id, eval_data.get("headline", ""), eval_data.get("category", ""))
            dispatched_count += 1

        # Check FII/DII routine
        self.check_fii_dii_routine()

        self.total_dispatched += dispatched_count
        return dispatched_count

    def run_daemon(self):
        """Run one shift. The workflow persists state before dispatching its successor."""
        logger.info("Starting daemon: duration=%ss dry_run=%s", self.shift_seconds, self.dry_run)
        try:
            while time.time() - self.start_time < self.shift_seconds:
                try:
                    count = self.process_cycle()
                    if count:
                        logger.info("Cycle %s: dispatched %s items", self.cycle_count, count)
                except StateIntegrityError:
                    raise
                except Exception:
                    logger.exception("Error during daemon cycle %s", self.cycle_count)
                    self.total_errors += 1
                self.state.save_state(sync_git=False)
                remaining = self.shift_seconds - (time.time() - self.start_time)
                if remaining > 0:
                    time.sleep(min(self.poll_interval, remaining))
        finally:
            if getattr(self, "_source_pool", None):
                self._source_pool.shutdown(wait=True, cancel_futures=True)
            if getattr(self, "_pdf_pool", None):
                self._pdf_pool.shutdown(wait=True, cancel_futures=True)
            self.state.save_state(sync_git=False)
        logger.info("Daemon finished. Cycles: %s, Dispatched: %s, Errors: %s",
                    self.cycle_count, self.total_dispatched, self.total_errors)
        if self.total_errors:
            raise RuntimeError("Daemon encountered errors; inspect the run log.")


def main():
    parser = argparse.ArgumentParser(description="Pahal Market Wire Production Daemon")
    parser.add_argument("--once", action="store_true", help="Run a single polling cycle and exit")
    parser.add_argument("--dry-run", action="store_true", help="Evaluate and format without publishing to Telegram")
    parser.add_argument("--duration", type=int, default=3240, help="Shift duration in seconds (default: 54 min = 3240s)")
    args = parser.parse_args()

    daemon = RealtimeDaemon(dry_run=args.dry_run, shift_seconds=args.duration)
    if args.once:
        logger.info("Executing single pass (--once)...")
        count = daemon.process_cycle(wait_sources=True)
        daemon.state.save_state(sync_git=False)
        logger.info(f"Single pass complete. Dispatched {count} announcements.")
        if daemon.total_errors:
            raise RuntimeError("Single pass had errors; inspect the run log.")
    else:
        daemon.run_daemon()


if __name__ == "__main__":
    main()

