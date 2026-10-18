import os
import sys
import json
import re
import time
from typing import Optional, Tuple, List, Dict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup
from curl_cffi import requests as cureq

IST = ZoneInfo("Asia/Kolkata")

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.quality_gatekeeper import QualityGatekeeper

class LiveDeltaMonitor:
    def __init__(
        self,
        rules_path: str = "config/filter_rules.json",
        missed_deltas_path: str = "state/missed_deltas.json"
    ):
        self.rules_path = rules_path
        self.missed_deltas_path = missed_deltas_path
        self.gatekeeper = QualityGatekeeper()
        self.rules = self._load_rules()

    def _load_rules(self) -> dict:
        if os.path.exists(self.rules_path):
            try:
                with open(self.rules_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"keep_keywords": {"auto_learned_keywords": []}}

    def _save_rules(self):
        try:
            with open(self.rules_path, "w", encoding="utf-8") as f:
                json.dump(self.rules, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[-] Failed to update filter rules: {e}")

    def fetch_channel_recent(self, channel_handle: str) -> list[dict]:
        """Fetches the latest ~20 posts from Telegram web channel preview with retries."""
        import urllib.request
        clean_handle = channel_handle.replace("https://t.me/s/", "").replace("https://t.me/", "").replace("@", "").strip("/")
        url = f"https://t.me/s/{clean_handle}"
        posts = []
        for attempt in range(3):
            try:
                req = urllib.request.Request(
                    url,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                        "Accept-Language": "en-US,en;q=0.9"
                    }
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    html = resp.read().decode("utf-8", errors="replace")
                    soup = BeautifulSoup(html, "html.parser")
                    msg_divs = soup.find_all("div", class_="tgme_widget_message")
                    for m in msg_divs:
                        data_post = m.get("data-post", "")
                        if not data_post:
                            continue
                        msg_id = int(data_post.split("/")[-1])
                        time_elem = m.find("time")
                        dt_str = time_elem.get("datetime") if time_elem else ""
                        dt = None
                        if dt_str:
                            try:
                                dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00")).astimezone(IST)
                            except Exception:
                                pass
                        text_div = m.find("div", class_="tgme_widget_message_text")
                        raw_text = text_div.get_text(separator="\n").strip() if text_div else ""
                        
                        posts.append({
                            "msg_id": msg_id,
                            "dt": dt,
                            "raw_text": raw_text
                        })
                    break
            except Exception as e:
                time.sleep(1.0 + attempt * 0.5)
        return posts

    def diagnose_and_heal(self, benchmark_post: dict, our_posts: list[dict]) -> Optional[dict]:
        """
        Diagnoses why a Benchmark post was missed, auto-updates filter rules,
        and prepares it for Gatekeeper processing.
        """
        text = benchmark_post.get("raw_text", "")
        if not text or len(text) < 30:
            return None
            
        b_dt = benchmark_post.get("dt")
        # Check against our posts within last 2 hours
        now = datetime.now(IST)
        cutoff = now - timedelta(hours=2)
        
        # Tokenize benchmark post
        b_tokens = set(re.findall(r'\b[a-zA-Z]{3,}\b', text.lower()))
        matched = False
        
        for op in our_posts:
            if op.get("dt") and op["dt"] < cutoff:
                continue
            op_tokens = set(re.findall(r'\b[a-zA-Z]{3,}\b', op.get("raw_text", "").lower()))
            intersection = b_tokens & op_tokens
            if len(intersection) >= 5 or (len(b_tokens) > 0 and len(intersection) / len(b_tokens) > 0.4):
                matched = True
                break
                
        if matched:
            return None  # Not missed!
            
        # If missed, diagnose root cause
        drop_patterns = self.rules.get("drop_patterns", [])
        matched_drop_pattern = None
        for dp in drop_patterns:
            if re.search(dp, text, re.IGNORECASE):
                matched_drop_pattern = dp
                break
                
        if matched_drop_pattern:
            root_cause = f"Overly strict drop pattern '{matched_drop_pattern}' filtered out the story"
        else:
            root_cause = "Missing keyword in Stage-1 filter whitelist or upstream feed delay"
            
        # Extract candidate keywords to auto-heal
        potential_keywords = [w for w in re.findall(r'\b[A-Z]{3,12}\b', text) if w not in {"THE", "FOR", "AND", "BUY", "SELL", "NEW"}]
        learned_kw = None
        if potential_keywords:
            learned_kw = potential_keywords[0].lower()
            auto_learned = self.rules.setdefault("keep_keywords", {}).setdefault("auto_learned_keywords", [])
            if learned_kw not in auto_learned:
                auto_learned.append(learned_kw)
                self._save_rules()
                print(f"[Auto-Heal] Added new trigger keyword '{learned_kw}' to config/filter_rules.json")

        missed_record = {
            "timestamp": now.isoformat(),
            "benchmark_msg_id": benchmark_post.get("msg_id"),
            "benchmark_text": text,
            "root_cause": root_cause,
            "auto_healed_keyword": learned_kw
        }
        
        return missed_record

    def run_monitor_cycle(self) -> list[dict]:
        """Runs single pass of Live Delta Monitor."""
        print("[LiveDeltaMonitor] Checking benchmark feed against our channel's recent posts...")
        benchmark_posts = self.fetch_channel_recent("stock_aaj_or_kal")
        our_posts = self.fetch_channel_recent("pahal_investment")
        
        missed_records = []
        for bp in benchmark_posts[-10:]: # Check last 10 benchmark stories
            diagnosis = self.diagnose_and_heal(bp, our_posts)
            if diagnosis:
                missed_records.append(diagnosis)
                
        if missed_records:
            print(f"[LiveDeltaMonitor] Detected {len(missed_records)} missed stories. Logging to state/missed_deltas.json")
            # Update state/missed_deltas.json
            history = {"last_audit_timestamp": datetime.now(IST).isoformat(), "missed_deltas": []}
            if os.path.exists(self.missed_deltas_path):
                try:
                    with open(self.missed_deltas_path, "r", encoding="utf-8") as f:
                        history = json.load(f)
                except Exception:
                    pass
            history["missed_deltas"].extend(missed_records)
            with open(self.missed_deltas_path, "w", encoding="utf-8") as f:
                json.dump(history, f, ensure_ascii=False, indent=2)
        else:
            print("[LiveDeltaMonitor] Zero missed deltas detected in current live window.")
            
        return missed_records

if __name__ == "__main__":
    monitor = LiveDeltaMonitor()
    monitor.run_monitor_cycle()
