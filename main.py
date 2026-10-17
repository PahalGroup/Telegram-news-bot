import os
import sys
import time
import argparse
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

# Ensure UTF-8 output on all platforms
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# Load local .env if present
load_dotenv()

from scrapers.bse_scraper import BSEScraper
from scrapers.nse_scraper import NSEScraper
from scrapers.pib_scraper import PIBScraper
from scrapers.rbi_scraper import RBIScraper
from scrapers.sebi_scraper import SEBIScraper
from scrapers.live_quote_wire import LiveQuoteWire
from scripts.quality_gatekeeper import QualityGatekeeper
from scripts.live_delta_monitor import LiveDeltaMonitor
from scripts.morning_briefing import MorningBriefingEngine
from telegram_dispatcher import TelegramDispatcher

IST = ZoneInfo("Asia/Kolkata")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("MarketWire")

def validate_environment():
    """Validates required environment secrets and logs status."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHANNEL_ID") or os.getenv("TELEGRAM_CHAT_ID", "").strip()
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    
    missing = []
    if not token or token == "YOUR_TELEGRAM_BOT_TOKEN":
        missing.append("TELEGRAM_BOT_TOKEN")
    if not chat_id or chat_id == "@your_channel_id":
        missing.append("TELEGRAM_CHANNEL_ID / TELEGRAM_CHAT_ID")
        
    if missing:
        logger.warning(f"[ENV WARNING] Missing credentials: {', '.join(missing)}. Running in console dry-run mode.")
    else:
        logger.info(f"[ENV OK] Telegram Bot Token configured. Target Channel: {chat_id}")
        
    if not gemini_key or gemini_key == "YOUR_GEMINI_API_KEY":
        logger.info("[ENV NOTE] GEMINI_API_KEY not set. Using deterministic zero-hallucination formatter (₹0/mo tier).")
    else:
        logger.info("[ENV OK] GEMINI_API_KEY configured for high-speed AI formatting.")

class MarketWireEngine:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.bse = BSEScraper()
        self.nse = NSEScraper()
        self.pib = PIBScraper()
        self.rbi = RBIScraper()
        self.sebi = SEBIScraper()
        self.wire = LiveQuoteWire()
        self.gatekeeper = QualityGatekeeper()
        self.delta_monitor = LiveDeltaMonitor()
        self.morning_briefing = MorningBriefingEngine()
        self.dispatcher = TelegramDispatcher()

    def run_all_scrapers(self) -> list[dict]:
        """Runs all 6 zero-cost public endpoints resiliently."""
        all_items = []
        scrapers = [
            ("BSE Corporate Filings", self.bse.fetch_recent),
            ("NSE Corporate Filings", self.nse.fetch_recent),
            ("PIB Gov / FinMin Releases", self.pib.fetch_recent),
            ("RBI Monetary / Bank Circulars", self.rbi.fetch_recent),
            ("SEBI Regulatory Updates", self.sebi.fetch_recent),
            ("Live Quotes Wire (FM & RBI)", self.wire.fetch_recent),
        ]
        
        for name, fetch_fn in scrapers:
            try:
                items = fetch_fn()
                logger.info(f"[{name}] Fetched {len(items)} items.")
                all_items.extend(items)
            except Exception as e:
                logger.error(f"[{name}] Polling error (resiliently caught): {e}")
                
        return all_items

    def process_and_dispatch(self, max_posts_per_cycle: int = 5) -> list[dict]:
        """
        Executes full pipeline:
        1. Check Morning Briefing trigger (first post between 07:00 and 08:45 AM IST).
        2. Scrape -> Agent 3 Gatekeeper Audit -> Dispatch -> State Recording.
        3. Live Delta Monitor check.
        """
        # Morning Briefing Trigger (First post of the day when channel wakes up)
        now_ist = datetime.now(IST)
        if 7 <= now_ist.hour < 9 and self.morning_briefing.should_run_today(now_ist):
            logger.info("[*] Morning Window: Generating and Dispatching Master Morning Briefing...")
            self.morning_briefing.dispatch_briefing(dry_run=self.dry_run)

        raw_items = self.run_all_scrapers()
        approved_dispatches = []
        
        for item in raw_items:
            if len(approved_dispatches) >= max_posts_per_cycle:
                break
                
            # Stage-1 Pre-Filtration Check: Drop routine paperwork (newspaper clippings, PIT closures, shareholding patterns)
            from filter_engine import FilterEngine
            pre_check_text = f"{item.get('company', '')} {item.get('headline', '')} {item.get('body', '')}"
            if FilterEngine.should_drop(pre_check_text):
                continue

            formatted_post, score, audit_meta = self.gatekeeper.generate_verified_post(item)
            
            if audit_meta.get("status") == "APPROVED" and formatted_post:
                logger.info(f"[Gatekeeper APPROVED] {item.get('company')} (Authenticity: {score*100:.0f}%)")
                
                # Dispatch to Telegram or log to console
                if self.dry_run:
                    print(f"\n{'='*60}\n[DRY RUN DISPATCH - AUTHENTICITY SCORE: {score*100:.0f}%]")
                    print(f"RAW LINK: {item.get('url')}")
                    print(f"FORMATTED TELEGRAM POST:\n{formatted_post}")
                    print(f"{'='*60}\n")
                else:
                    sent = self.dispatcher.send_message(formatted_post)
                    if sent:
                        self.gatekeeper.record_posted(
                            company=item.get("company", ""),
                            category=item.get("source", "GENERAL"),
                            final_post=formatted_post,
                            url=item.get("url", "")
                        )
                        
                approved_dispatches.append({
                    "item": item,
                    "post": formatted_post,
                    "score": score,
                    "audit": audit_meta
                })
            else:
                logger.debug(f"[Gatekeeper Skipped] {item.get('company')}: {audit_meta.get('reason', 'Duplicate or ungrounded')}")
                
        # Run Live Delta Monitor pass to audit against benchmark channel
        try:
            self.delta_monitor.run_monitor_cycle()
        except Exception as e:
            logger.warning(f"[DeltaMonitor Error]: {e}")
            
        return approved_dispatches

def main():
    parser = argparse.ArgumentParser(description="Autonomous Indian Market Wire Engine & Gatekeeper")
    parser.add_argument("--test", action="store_true", help="Run single pass in dry-run mode (outputs sample posts to console)")
    parser.add_argument("--live", action="store_true", help="Run single live pass and dispatch approved stories to Telegram")
    parser.add_argument("--morning-briefing", action="store_true", help="Force generate and dispatch Morning Briefing")
    parser.add_argument("--run-delta", action="store_true", help="Run Live Delta Monitor against benchmark channel")
    parser.add_argument("--daemon", action="store_true", help="Run in continuous polling loop")
    
    args = parser.parse_args()
    validate_environment()
    
    if args.morning_briefing:
        briefing = MorningBriefingEngine()
        briefing.dispatch_briefing(force=True, dry_run=not args.live)
        sys.exit(0)
        
    if args.run_delta:
        monitor = LiveDeltaMonitor()
        monitor.run_monitor_cycle()
        sys.exit(0)
        
    engine = MarketWireEngine(dry_run=not args.live)
    
    if args.test or not args.daemon:
        logger.info("[*] Executing Single Engine Pass across All Live Endpoints...")
        dispatches = engine.process_and_dispatch(max_posts_per_cycle=5)
        logger.info(f"[✓] Engine cycle complete. Approved dispatches: {len(dispatches)}")
    else:
        logger.info("[*] Starting Continuous 24/7 Market Wire Daemon...")
        while True:
            try:
                engine.process_and_dispatch(max_posts_per_cycle=3)
            except Exception as e:
                logger.error(f"Daemon cycle error: {e}")
            time.sleep(30)

if __name__ == "__main__":
    main()
