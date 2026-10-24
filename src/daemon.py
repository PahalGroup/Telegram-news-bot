"""
Production Daemon: Real-Time Continuous Polling Engine (src/daemon.py)
SINGLE source of truth for GitHub Actions production.

Sub-15-Second Continuous Polling Loop:
- Runs for configurable duration per workflow run (default: 54 min)
- Polls upstream endpoints concurrently (BSE, NSE, RBI, News RSS, Liveblog) every 12s
- Stage 1: Deterministic Hard Filter (src/filter_engine.py)
- Stage 2: Few-Shot Calibrated AI Materiality Gate
- Pixel-Accurate Formatter (Telegram HTML)
- Persistent state management via src.state_store
- Zero-gap handoff: Triggers workflow_dispatch before exiting cleanly
"""

import os
import sys
import time
import argparse
import logging
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
from src.state_store import StateStore
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

        self.state = StateStore()
        self.filter_engine = FilterEngine()
        self.dispatcher = TelegramDispatcher()

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

    def trigger_github_workflow_dispatch(self):
        """Zero-gap handoff: Dispatch next runner via GitHub REST API."""
        gh_token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_PAT") or os.getenv("BOT_PAT")
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
        """Fetch raw announcements from ALL upstream sources with correct method names."""
        raw_items = []

        # 1. BSE Corporate Announcements
        try:
            bse_items = self.bse.fetch_announcements(skip_pdf=True) or []
            for it in bse_items:
                raw_items.append({
                    "id": f"BSE_{it.get('newsid', it.get('raw_subject', ''))}",
                    "source": "BSE",
                    "company": it.get("company", ""),
                    "title": it.get("subject", "") or it.get("raw_subject", ""),
                    "description": f"{it.get('headline', '')} {it.get('more', '')}".strip(),
                    "pdf_url": it.get("pdf_url"),
                    "raw_subject": it.get("raw_subject", ""),
                })
            logger.info(f"[BSE] Fetched {len(bse_items)} items")
        except Exception as e:
            logger.warning(f"BSE poll error: {e}")
            self.total_errors += 1

        # 2. NSE Corporate Filings
        try:
            nse_items = self.nse.fetch_announcements() or []
            for it in nse_items:
                symbol = it.get("symbol", "")
                subject = it.get("desc", "") or it.get("subject", "")
                company_name = it.get("sm_name", "") or symbol
                attchmnt_text = it.get("attchmntText", "") or it.get("details", "")
                title = attchmnt_text or subject
                pdf_url = it.get("attchmntFile")
                seq_id = it.get("dt") or it.get("seq_id") or subject[:40]

                raw_items.append({
                    "id": f"NSE_{symbol}_{seq_id}",
                    "source": "NSE",
                    "company": company_name,
                    "title": title,
                    "description": attchmnt_text,
                    "pdf_url": pdf_url,
                })
            logger.info(f"[NSE] Fetched {len(nse_items)} items")
        except Exception as e:
            logger.warning(f"NSE poll error: {e}")
            self.total_errors += 1

        # 3. RBI Press Releases
        try:
            rbi_items = self.rbi.fetch_press_releases() or []
            for it in rbi_items:
                raw_items.append({
                    "id": f"RBI_{it.get('title', '')[:50]}",
                    "source": "RBI",
                    "company": "RESERVE BANK OF INDIA",
                    "title": it.get("title", ""),
                    "description": it.get("description", ""),
                    "pdf_url": it.get("link"),
                })
            logger.info(f"[RBI] Fetched {len(rbi_items)} items")
        except Exception as e:
            logger.warning(f"RBI poll error: {e}")
            self.total_errors += 1

        # 4. News RSS Wires (Economic Times, Moneycontrol, Mint, BusinessLine)
        try:
            news_items = self.news.fetch_all_feeds() or []
            for it in news_items:
                raw_items.append({
                    "id": f"NEWS_{it.get('link', it.get('title', '')[:40])}",
                    "source": "NEWS",
                    "company": "",  # Extracted by filter
                    "title": it.get("title", ""),
                    "description": it.get("description", ""),
                })
            logger.info(f"[NEWS] Fetched {len(news_items)} items")
        except Exception as e:
            logger.warning(f"News poll error: {e}")
            self.total_errors += 1

        # 5. LiveBlog Breaking (Moneycontrol real-time flashes)
        try:
            lb_items = self.liveblog.fetch_live_flashes() or []
            for it in lb_items:
                raw_items.append({
                    "id": f"LIVE_{it.get('id', it.get('title', '')[:30])}",
                    "source": "LIVEBLOG",
                    "company": "",
                    "title": it.get("title", ""),
                    "description": it.get("description", ""),
                })
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

        if self.fii_posted_today:
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
                    data_date = fii_data[0].get("date", "").upper()
                    fii_net = 0.0
                    dii_net = 0.0
                    for entry in fii_data:
                        cat = entry.get("category", "").upper()
                        raw_net = str(entry.get("netValue", 0)).replace(",", "")
                        net = float(raw_net) if raw_net else 0.0
                        if "FII" in cat or "FPI" in cat:
                            fii_net = net
                        elif "DII" in cat:
                            dii_net = net

                    trade_date = data_date or today_str
                    formatted_msg = Formatter.format_fii_dii(trade_date, fii_net, dii_net)
                    logger.info(f"Posting daily FII/DII flows for {trade_date}...")
                    if not self.dry_run:
                        self.dispatcher.send_message(formatted_msg, parse_mode="HTML")
                    else:
                        print(f"\n[DRY RUN FII/DII DISPATCH]:\n{formatted_msg}\n")
                    self.state.mark_seen(fii_key, "NSE_FLOWS")
                    self.state.record_published(fii_key, f"FII/DII ACTIVITY – {trade_date}", "FII_DII_FLOWS")
                    self.fii_posted_today = True
            except Exception as e:
                logger.debug(f"FII/DII fetch routine: {e}")

    def process_cycle(self) -> int:
        """Executes a single end-to-end polling, filtration, formatting, and dispatch pass."""
        self.cycle_count += 1
        items = self.poll_all_sources()
        dispatched_count = 0

        for it in items:
            item_id = it.get("id")
            if not item_id or self.state.is_seen(item_id):
                continue

            # Lazy PDF Extraction: Only for unseen corporate filings matching target catalysts
            pdf_url = it.get("pdf_url")
            if pdf_url and str(pdf_url).lower().endswith(".pdf") and it.get("source") in ("BSE", "NSE"):
                title_context = f"{it.get('company', '')} {it.get('title', '')} {it.get('raw_subject', '')}".lower()
                is_target_filing = any(k in title_context for k in [
                    "order", "contract", "results", "directorate", "board meeting", "outcome",
                    "mou", "acquisition", "expansion", "dividend", "qip", "ipo",
                    "buyback", "financial", "tender", "rating", "press release", "investor", "clarification"
                ])
                if is_target_filing and not self.filter_engine.should_drop(title_context):
                    try:
                        from sources.annexure_parser import AnnexureParser
                        lead_sum, pdf_bullets = AnnexureParser.extract_annexure_bullets(pdf_url)
                        if pdf_bullets:
                            formatted_b = [f"• {b}" if not b.startswith(('•', '-', '▪️')) else b for b in pdf_bullets]
                            if lead_sum:
                                it["description"] = f"{lead_sum}\n" + "\n".join(formatted_b)
                            else:
                                it["description"] = "\n".join(formatted_b)
                    except Exception as pe:
                        logger.debug(f"Lazy PDF extract error for {item_id}: {pe}")

            # Stage 1: Deterministic Hard Filter
            try:
                passed_s1, reason_s1, candidate = self.filter_engine.evaluate_stage_1(it)
            except Exception as e:
                logger.debug(f"Stage 1 error for {item_id}: {e}")
                self.state.mark_seen(item_id, it.get("source", "UNKNOWN"))
                continue

            if not passed_s1:
                self.state.mark_seen(item_id, it.get("source", "UNKNOWN"))
                continue

            # Rolling 6-Hour Entity Event Cooldown Gate
            norm_company = self.filter_engine.normalize_entity_name(candidate.get("company", ""))
            category = candidate.get("category", "")
            if self.state.is_in_cooldown(norm_company, category, cooldown_seconds=21600):
                logger.info(f"Skipping {norm_company}:{category} due to 6h rolling cooldown.")
                self.state.mark_seen(item_id, it.get("source", "UNKNOWN"))
                continue

            # Stage 2: Few-Shot Calibrated AI Materiality Gate
            try:
                eval_data = self.filter_engine.evaluate_stage_2(candidate)
            except Exception as e:
                logger.warning(f"Stage 2 error for {item_id}: {e}")
                self.state.mark_seen(item_id, it.get("source", "UNKNOWN"))
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

        self.total_dispatched += dispatched_count
        return dispatched_count

    def run_daemon(self):
        """Main continuous daemon loop."""
        logger.info(f"Starting Production Daemon (Duration: {self.shift_seconds}s, Poll: {self.poll_interval}s, DryRun: {self.dry_run})...")
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
                count = self.process_cycle()
                if count > 0:
                    logger.info(f"Cycle {self.cycle_count}: dispatched {count} items (total: {self.total_dispatched})")
            except Exception as e:
                logger.error(f"Error during daemon cycle {self.cycle_count}: {e}")
                self.total_errors += 1

            time.sleep(self.poll_interval)

        # Trigger relay at end if not already done
        if not relay_triggered:
            logger.info("End-of-shift relay trigger...")
            self.trigger_github_workflow_dispatch()

        # Save and persist state upon exit
        self.state.save_state(sync_git=True)
        logger.info(f"Daemon finished. Cycles: {self.cycle_count}, Dispatched: {self.total_dispatched}, Errors: {self.total_errors}")


def main():
    parser = argparse.ArgumentParser(description="Pahal Market Wire Production Daemon")
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
