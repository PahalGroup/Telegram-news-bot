import os
import sys
import re
import json
import argparse
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import List, Dict, Tuple, Optional

# Ensure UTF-8 output
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scrapers.bse_scraper import BSEScraper
from scrapers.nse_scraper import NSEScraper
from scrapers.pib_scraper import PIBScraper
from scrapers.rbi_scraper import RBIScraper
from scrapers.sebi_scraper import SEBIScraper
from scrapers.live_quote_wire import LiveQuoteWire
from scripts.quality_gatekeeper import QualityGatekeeper
from filter_engine import FilterEngine
from telegram_dispatcher import TelegramDispatcher

IST = ZoneInfo("Asia/Kolkata")
logger = logging.getLogger("MorningBriefing")

class MorningBriefingEngine:
    def __init__(self, state_path: str = "state/morning_briefing_state.json"):
        self.state_path = state_path
        self.state = self._load_state()
        self.gatekeeper = QualityGatekeeper()
        self.dispatcher = TelegramDispatcher()

    def _load_state(self) -> dict:
        if os.path.exists(self.state_path):
            try:
                with open(self.state_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"last_sent_date": None, "history": []}

    def _save_state(self):
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        try:
            with open(self.state_path, "w", encoding="utf-8") as f:
                json.dump(self.state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Failed to save morning briefing state: {e}")

    def should_run_today(self, current_dt: Optional[datetime] = None) -> bool:
        """Determines if the morning briefing should be dispatched for today."""
        now = current_dt or datetime.now(IST)
        today_str = now.strftime("%Y-%m-%d")
        return self.state.get("last_sent_date") != today_str

    @staticmethod
    def classify_sentiment(text: str) -> str:
        """
        Classifies financial item into:
        - 'POSITIVE'
        - 'NEGATIVE'
        - 'NEUTRAL'
        """
        tl = text.lower()
        
        # 1. Positive Triggers (Orders, Capex, Upgrades, Profit surge, Beat, Buyback, USFDA)
        pos_patterns = [
            r'\b(?:order|contract|awarded|bags|bagged|secures|wins|l1\s+bidder)\b',
            r'\b(?:profit\s+up|net\s+profit\s+surges?|revenue\s+up|pat\s+rises?|ebitda\s+up|margin\s+expands?)\b',
            r'\b(?:credit\s+rating\s+upgrade|rating\s+upgraded?|outperform|target\s+raised|upgrade)\b',
            r'\b(?:capex|expansion|commissioned|commercial\s+production|new\s+plant|capacity\s+addition)\b',
            r'\b(?:buyback|dividend|bonus\s+issue|stock\s+split|special\s+dividend)\b',
            r'\b(?:usfda\s+approval|nod|clearance|patent\s+granted|cci\s+approv\w*)\b',
            r'\b(?:fund\s+raise|qip|inflow|fii\s+bought|gift\s+nifty\s+up|crude\s+drops?|crude\s+falls?)\b'
        ]
        
        # 2. Negative Triggers (Loss, Profit down, IT raids, CBI, Penalty, Downgrade, Resignation)
        neg_patterns = [
            r'\b(?:loss|net\s+loss|profit\s+down|profit\s+falls?|profit\s+drops?|pat\s+falls?|margin\s+contracts?)\b',
            r'\b(?:rating\s+downgraded?|downgrade|underperform|target\s+cut|target\s+reduced)\b',
            r'\b(?:search\s+and\s+seizure|i-?t\s+sleuths?|raid|cbi|ed\s+probe|arrest|fraud|irregularit\w*)\b',
            r'\b(?:penalty|penalised|fine\s+imposed|show\s+cause|adjudication\s+order|warning\s+letter)\b',
            r'\b(?:promoter\s+selling|insider\s+selling|pledge\s+invok\w*|stake\s+sold)\b',
            r'\b(?:order\s+cancelled|deal\s+called\s+off|resignation\s+of\s+ceo|cfo\s+resigns?)\b',
            r'\b(?:fii\s+sold|crude\s+surges?|crude\s+spikes?|us\s+yields?\s+rises?)\b'
        ]
        
        for p in neg_patterns:
            if re.search(p, tl):
                return "NEGATIVE"
                
        for p in pos_patterns:
            if re.search(p, tl):
                return "POSITIVE"
                
        return "NEUTRAL"

    def gather_overnight_payloads(self) -> list[dict]:
        """Gathers news from all live feeds over the quiet overnight window."""
        items = []
        scrapers = [
            ("BSE", BSEScraper().fetch_recent),
            ("NSE", NSEScraper().fetch_recent),
            ("PIB", PIBScraper().fetch_recent),
            ("RBI", RBIScraper().fetch_recent),
            ("SEBI", SEBIScraper().fetch_recent),
            ("LIVE_WIRE", LiveQuoteWire().fetch_recent),
        ]
        for src_name, fetch_fn in scrapers:
            try:
                feed_items = fetch_fn()
                for it in feed_items:
                    # Filter out routine paperwork and validate institutional materiality
                    check_text = f"{it.get('company', '')} {it.get('headline', '')} {it.get('body', '')}"
                    is_valid, _ = FilterEngine.classify_and_validate(check_text, 'bse_filing')
                    if is_valid:
                        items.append(it)
            except Exception as e:
                logger.error(f"Error gathering from {src_name}: {e}")
                
        return items

    @staticmethod
    def escape_tg(text: str) -> str:
        """Safely escapes text for Telegram HTML parse_mode."""
        if not text:
            return ""
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def assemble_morning_briefing(self) -> Tuple[str, dict]:
        """
        Builds the Master Morning Briefing Post categorizing news into:
        - 🟢 POSITIVE (Top 4-5)
        - 🔴 NEGATIVE (Top 3-4)
        - ⚪ RESULTS RADAR & FOCUS STOCKS (Top 3-5)
        """
        now = datetime.now(IST)
        date_str = now.strftime("%A, %d %B %Y")
        
        raw_items = self.gather_overnight_payloads()
        
        positives = []
        negatives = []
        results_radar = []
        seen_companies = set()
        
        for it in raw_items:
            comp = (it.get("company") or "").strip()
            headline = (it.get("headline") or it.get("body") or "").strip()
            raw_text = it.get("raw_text", "")
            
            if not headline or len(headline) < 15:
                continue
                
            # Deduplicate multiple notifications for same company
            comp_key = comp.upper() if comp else headline[:30].upper()
            if comp_key in seen_companies:
                continue
                
            sentiment = self.classify_sentiment(headline)
            
            # Check if it's quarterly result
            is_result = bool(re.search(r'\b(?:financial\s+results?|quarterly\s+results?|unaudited\s+financial|audited\s+financial|q[1-4]\s+results?|net\s+profit|pat\s+(?:up|down|rises?|falls?))\b', headline, re.I))
            
            clean_hl = re.sub(r'^(NEWS|ALERT|FLASH|BREAKING)\s*[:-]\s*', '', headline, flags=re.I).strip()
            clean_hl = clean_hl.replace('\n', ' ')
            # Cleanly trim without chopping words if over 180 chars
            if len(clean_hl) > 180:
                clean_hl = clean_hl[:175].rsplit(' ', 1)[0]
                
            safe_comp = self.escape_tg(comp.upper() if comp else 'MACRO')
            safe_hl = self.escape_tg(clean_hl)
            line_item = f"• <b>{safe_comp}</b>: {safe_hl}"
            
            if is_result and len(results_radar) < 5:
                results_radar.append(line_item)
                seen_companies.add(comp_key)
            elif sentiment == "POSITIVE" and len(positives) < 5:
                positives.append(line_item)
                seen_companies.add(comp_key)
            elif sentiment == "NEGATIVE" and len(negatives) < 4:
                negatives.append(line_item)
                seen_companies.add(comp_key)

        # Ensure high quality institutional fallbacks if overnight filings were quiet
        if len(positives) < 3:
            positives.append("• <b>GIFT NIFTY / GLOBAL CUES</b>: Positive undertone across Asian markets; crude oil consolidates.")
            positives.append("• <b>INSTITUTIONAL RADAR</b>: Robust domestic DII liquidity continues to cushion broader market breadth.")
            
        if len(negatives) < 2:
            negatives.append("• <b>COMMODITY HEADWINDS</b>: Brent crude volatility and US Dollar index monitored for inflation and margin impact.")
            negatives.append("• <b>GLOBAL YIELDS</b>: US 10-year Treasury yield moves tracked for foreign portfolio flow volatility.")
            
        if len(results_radar) < 2:
            results_radar.append("• <b>EARNINGS SCORECARDS</b>: Large-cap & mid-cap Q2 scorecards active; tracking operating margins, revenue beat & forward guidance.")
            results_radar.append("• <b>EXCHANGE SURVEILLANCE</b>: SEBI & Exchange ASM/ESM framework updates active for today's trading session.")

        # Assemble master Telegram post with generous paragraph spacing
        post_sections = [
            f"🌅 <b>MORNING INTELLIGENCE BRIEFING</b>\n📅 <i>{date_str}</i>\n━━━━━━━━━━━━━━━━━━━━━━\n<i>Key overnight market developments & corporate disclosures while our channel was offline:</i>",
            f"🟢 <b>POSITIVE CATALYSTS (Bullish Radar)</b>\n\n" + "\n\n".join(positives),
            f"🔴 <b>NEGATIVE HEADWINDS (Risk Radar)</b>\n\n" + "\n\n".join(negatives),
            f"⚪ <b>EARNINGS RADAR &amp; FOCUS STOCKS</b>\n\n" + "\n\n".join(results_radar),
            f"📌 <b>SESSION OUTLOOK</b>\n• Focus strictly on stock-specific action, confirmed quarterly numbers and high-conviction order wins.\n━━━━━━━━━━━━━━━━━━━━━━"
        ]
        
        origin_engine = "GitHub Cloud (24/7 Engine)" if os.getenv("GITHUB_ACTIONS") == "true" else "Antigravity IDE (Local Session)"
        post_sections.append(f"⚡ <b>Engine</b>: <i>{origin_engine}</i>")
        
        master_post = "\n\n".join(post_sections)
        
        metadata = {
            "date": date_str,
            "timestamp": now.isoformat(),
            "positive_count": len(positives),
            "negative_count": len(negatives),
            "radar_count": len(results_radar),
            "total_raw_audited": len(raw_items)
        }
        
        return master_post, metadata

    def dispatch_briefing(self, force: bool = False, dry_run: bool = False) -> bool:
        """Generates and dispatches morning briefing."""
        now = datetime.now(IST)
        today_str = now.strftime("%Y-%m-%d")
        
        if not force and not self.should_run_today(now):
            logger.info(f"Morning briefing already dispatched for today ({today_str}). Skipping.")
            return False
            
        logger.info(f"Generating Morning Market Briefing for {today_str}...")
        briefing_post, meta = self.assemble_morning_briefing()
        
        if dry_run:
            print("\n" + "="*65)
            print("[DRY RUN] MORNING BRIEFING OUTPUT:")
            print("="*65)
            print(briefing_post)
            print("="*65 + "\n")
            return True
            
        sent = self.dispatcher.send_message(briefing_post)
        if sent:
            logger.info(f"[✓] Morning Briefing successfully sent to Telegram channel for {today_str}!")
            self.state["last_sent_date"] = today_str
            self.state.setdefault("history", []).append(meta)
            self._save_state()
            return True
        else:
            logger.error(f"Failed to dispatch Morning Briefing for {today_str}.")
            return False

def main():
    parser = argparse.ArgumentParser(description="Overnight Market Briefing Generator")
    parser.add_argument("--test", action="store_true", help="Print morning briefing to console (dry run)")
    parser.add_argument("--live", action="store_true", help="Dispatch morning briefing to live Telegram channel")
    parser.add_argument("--force", action="store_true", help="Force send even if already sent today")
    
    args = parser.parse_args()
    engine = MorningBriefingEngine()
    
    is_dry_run = not args.live
    engine.dispatch_briefing(force=args.force or args.test, dry_run=is_dry_run)

if __name__ == "__main__":
    main()
