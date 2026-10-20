"""
Phase 4: Real-Time Continuous Polling Daemon (src/daemon.py)
Sub-15-Second Continuous Polling Loop:
- Runs for 54 minutes per workflow run (or specified duration / --once / --dry-run)
- Polls upstream endpoints concurrently (BSE, NSE, RBI, News, Liveblog) every 10-15 seconds
- Stage 1: Deterministic Hard Filter
- Stage 2: Few-Shot Calibrated AI Materiality Gate
- Pixel-Accurate Formatter (Telegram HTML)
- Persistent state management via src.state_store
- Zero-gap handoff: Triggers workflow_dispatch at minute 52-54 before exiting cleanly
"""

import os
import sys
import time
import argparse
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List

from src.filter_engine import FilterEngine
from src.formatter import Formatter
from src.state_store import StateStore
from telegram_dispatcher import TelegramDispatcher
from sources.nse_fetcher import NSEFetcher
from sources.bse_fetcher import BSEFetcher
from sources.rbi_fetcher import RBIFetcher
from sources.news_fetcher import NewsFetcher
from sources.liveblog_fetcher import LiveBlogFetcher

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

class RealtimeDaemon:
    def __init__(self, dry_run: bool = False, shift_seconds: int = 3240):
        self.dry_run = dry_run
        self.shift_seconds = shift_seconds
        self.start_time = time.time()
        
        self.state = StateStore()
        self.filter_engine = FilterEngine()
        self.dispatcher = TelegramDispatcher()
        
        # Ingest Sources
        self.bse = BSEFetcher()
        self.nse = NSEFetcher()
        self.rbi = RBIFetcher()
        self.news = NewsFetcher()
        self.liveblog = LiveBlogFetcher()
        
        self.poll_interval = 12 # Sub-15-second continuous polling
        self.fii_posted_today = False

    def trigger_github_workflow_dispatch(self):
        """Zero-gap handoff: Dispatch next runner via GitHub REST API."""
        gh_token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_PAT")
        repo = os.getenv("GITHUB_REPOSITORY", "PahalGroup/Telegram-news-bot")
        workflow_id = "corn.yml"
        
        if not gh_token:
            logger.info("No GITHUB_TOKEN present; skipping self-relay workflow dispatch.")
            return

        import urllib.request
        url = f"https://api.github.com/repos/{repo}/actions/workflows/{workflow_id}/dispatches"
        req = urllib.request.Request(
            url,
            data=b'{"ref":"main"}',
            headers={
                "Authorization": f"Bearer {gh_token}",
                "Accept": "application/vnd.github.v3+json",
                "User-Agent": "PahalMarketWireDaemon"
            },
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status in [200, 204]:
                    logger.info("Successfully triggered next GitHub workflow dispatch for zero-gap handoff!")
                else:
                    logger.warning(f"Workflow dispatch status: {resp.status}")
        except Exception as e:
            logger.warning(f"Self-relay trigger encountered: {e}")

    def poll_all_sources(self) -> List[Dict[str, Any]]:
        """Fetch raw announcements from all upstream sources concurrently."""
        raw_items = []
        
        # 1. BSE Announcements
        try:
            bse_items = self.bse.fetch_announcements()
            for it in bse_items:
                raw_items.append({
                    "id": f"BSE_{it.get('bse_id', it.get('raw_subject', ''))}",
                    "source": "BSE",
                    "company": it.get("company", ""),
                    "title": it.get("subject", "") or it.get("raw_subject", ""),
                    "description": f"{it.get('headline', '')} {it.get('more', '')}".strip(),
                    "pdf_url": it.get("pdf_url")
                })
        except Exception as e:
            logger.debug(f"BSE poll error: {e}")

        # 2. NSE Filings
        try:
            nse_items = self.nse.fetch_corporate_filings()
            for it in nse_items:
                raw_items.append({
                    "id": f"NSE_{it.get('symbol', '')}_{it.get('subject', '')}",
                    "source": "NSE",
                    "company": it.get("symbol", ""),
                    "title": it.get("subject", ""),
                    "description": it.get("details", "") or it.get("desc", ""),
                    "pdf_url": it.get("attachment")
                })
        except Exception as e:
            logger.debug(f"NSE poll error: {e}")

        # 3. RBI Circulars
        try:
            rbi_items = self.rbi.fetch_circulars()
            for it in rbi_items:
                raw_items.append({
                    "id": f"RBI_{it.get('title', '')[:40]}",
                    "source": "RBI",
                    "company": "RESERVE BANK OF INDIA",
                    "title": it.get("title", ""),
                    "description": it.get("description", ""),
                    "pdf_url": it.get("pdf_url")
                })
        except Exception as e:
            logger.debug(f"RBI poll error: {e}")

        # 4. News RSS Wires
        try:
            news_items = self.news.fetch_breaking()
            for it in news_items:
                raw_items.append({
                    "id": f"NEWS_{it.get('link', it.get('title', ''))}",
                    "source": "NEWS",
                    "company": it.get("company", ""),
                    "title": it.get("title", ""),
                    "description": it.get("summary", "")
                })
        except Exception as e:
            logger.debug(f"News poll error: {e}")

        # 5. LiveBlog Breaking
        try:
            lb_items = self.liveblog.fetch_latest_posts()
            for it in lb_items:
                raw_items.append({
                    "id": f"LIVE_{it.get('id', it.get('headline', ''))}",
                    "source": "LIVEBLOG",
                    "company": it.get("company", ""),
                    "title": it.get("headline", ""),
                    "description": it.get("body", "")
                })
        except Exception as e:
            logger.debug(f"LiveBlog poll error: {e}")

        return raw_items

    def check_fii_dii_routine(self):
        """Check and dispatch daily FII/DII activity between 18:00 and 22:00 IST."""
        if self.fii_posted_today:
            return
        
        # Check IST time
        now_utc = datetime.now(timezone.utc)
        ist_now = now_utc + timedelta(hours=5, minutes=30)
        today_str = ist_now.strftime("%d-%b-%Y").upper()

        fii_key = f"FII_DII_{today_str}"
        if self.state.is_seen(fii_key):
            self.fii_posted_today = True
            return

        if 18 <= ist_now.hour <= 22:
            try:
                from morning_evening_routines import fetch_nse_fii_dii_flows
                flows = fetch_nse_fii_dii_flows()
                if flows and flows.get("date"):
                    fii_val = flows.get("fii_net", 0.0)
                    dii_val = flows.get("dii_net", 0.0)
                    formatted_msg = Formatter.format_fii_dii(flows.get("date"), fii_val, dii_val)
                    logger.info(f"Posting daily FII/DII flows for {flows.get('date')}...")
                    if not self.dry_run:
                        self.dispatcher.send_message(formatted_msg, parse_mode="HTML")
                    else:
                        print(f"\n[DRY RUN FII/DII DISPATCH]:\n{formatted_msg}\n")
                    self.state.mark_seen(fii_key, "NSE_FLOWS")
                    self.state.record_published(fii_key, f"FII/DII ACTIVITY – {today_str}", "FII_DII_FLOWS")
                    self.fii_posted_today = True
            except Exception as e:
                logger.debug(f"FII/DII fetch routine: {e}")

    def process_cycle(self) -> int:
        """Executes a single end-to-end polling, filtration, formatting, and dispatch pass."""
        items = self.poll_all_sources()
        dispatched_count = 0

        for it in items:
            item_id = it.get("id")
            if not item_id or self.state.is_seen(item_id):
                continue

            # Stage 1: Deterministic Hard Filter
            passed_s1, reason_s1, candidate = self.filter_engine.evaluate_stage_1(it)
            if not passed_s1:
                self.state.mark_seen(item_id, it.get("source", "UNKNOWN"))
                continue

            # Rolling 6-Hour Entity Event Cooldown Gate
            norm_company = self.filter_engine.normalize_entity_name(candidate.get("company", ""))
            category = candidate.get("category", "")
            if self.state.is_in_cooldown(norm_company, category, cooldown_hours=6):
                logger.info(f"Skipping {norm_company}:{category} due to 6h rolling cooldown.")
                self.state.mark_seen(item_id, it.get("source", "UNKNOWN"))
                continue

            # Stage 2: Few-Shot Calibrated AI Materiality Gate
            eval_data = self.filter_engine.evaluate_stage_2(candidate)
            if not eval_data.get("publish", False) or eval_data.get("materiality_score", 0) < 70:
                self.state.mark_seen(item_id, it.get("source", "UNKNOWN"))
                continue

            # Format via pixel-accurate Telegram Formatter
            formatted_post = Formatter.format_post(eval_data, it)
            if not Formatter.validate_completeness(formatted_post):
                logger.info(f"Skipping incomplete/half post ({eval_data.get('headline')}): failed completeness validation.")
                self.state.mark_seen(item_id, it.get("source", "UNKNOWN"))
                continue

            # Publish to Telegram
            if not self.dry_run:
                try:
                    Formatter.execute_with_retry(
                        self.dispatcher.send_message,
                        formatted_post,
                        parse_mode="HTML"
                    )
                    logger.info(f"Published: {eval_data.get('headline')}")
                except Exception as e:
                    logger.error(f"Failed to dispatch to Telegram: {e}")
                    continue
            else:
                print(f"\n[DRY RUN DISPATCH - Score {eval_data.get('materiality_score')}]:\n{formatted_post}\n{'-'*50}")

            # Persist state and cooldown
            fingerprint = candidate.get("fingerprint")
            self.state.mark_seen(item_id, it.get("source", "UNKNOWN"), fingerprint=fingerprint)
            self.state.record_published(item_id, eval_data.get("headline", ""), eval_data.get("category", ""))
            self.state.record_cooldown(norm_company, category)
            dispatched_count += 1

        # Check FII/DII routine
        self.check_fii_dii_routine()

        return dispatched_count

    def run_daemon(self):
        """Main continuous daemon loop."""
        logger.info(f"Starting Realtime Daemon (Duration: {self.shift_seconds}s, Poll Interval: {self.poll_interval}s, DryRun: {self.dry_run})...")
        relay_triggered = False

        while True:
            elapsed = time.time() - self.start_time
            if elapsed >= self.shift_seconds:
                logger.info(f"Shift duration of {self.shift_seconds}s reached. Clean shutdown.")
                break

            # Zero-gap handoff: Trigger next workflow run 2 minutes before shift ends
            if elapsed >= (self.shift_seconds - 120) and not relay_triggered:
                logger.info("Zero-Gap Handoff Window: Triggering next GitHub workflow runner...")
                self.trigger_github_workflow_dispatch()
                relay_triggered = True

            try:
                self.process_cycle()
            except Exception as e:
                logger.error(f"Error during daemon cycle: {e}")

            time.sleep(self.poll_interval)

        # Save and persist state upon exit
        self.state.save_state(sync_git=True)
        logger.info("Daemon finished cleanly.")

def main():
    parser = argparse.ArgumentParser(description="Pahal Market Wire Real-Time Daemon")
    parser.add_argument("--once", action="store_true", help="Run a single polling cycle and exit")
    parser.add_argument("--dry-run", action="store_true", help="Evaluate and format without publishing to Telegram")
    parser.add_argument("--duration", type=int, default=3240, help="Shift duration in seconds (default: 54 min = 3240s)")
    args = parser.parse_args()

    daemon = RealtimeDaemon(dry_run=args.dry_run, shift_seconds=args.duration)
    if args.once:
        logger.info("Executing single pass (--once)...")
        count = daemon.process_cycle()
        daemon.state.save_state(sync_git=False)
        logger.info(f"Single pass complete. Dispatched {count} announcements.")
    else:
        daemon.run_daemon()

if __name__ == "__main__":
    main()
