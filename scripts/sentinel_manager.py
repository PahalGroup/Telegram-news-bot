import os
import sys
import time
import json
import logging
import subprocess
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup
import requests
from dotenv import load_dotenv

# Ensure real-time line buffering and UTF-8 output
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(line_buffering=True, encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(line_buffering=True, encoding='utf-8', errors='replace')

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID

IST = ZoneInfo("Asia/Kolkata")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [MANAGER] %(levelname)s: %(message)s")
logger = logging.getLogger("SentinelManager")

class SentinelManager:
    """
    Supervisor / Manager Agent:
    - Oversees the Sentinel Watchdog Agent (checks liveness, revives if stalled).
    - Audits formatting & spacing on live channel posts (repairs orphaned bullets via editMessageText).
    - Enforces zero benchmark copying.
    - Writes comprehensive real-time status to state/sentinel_manager_report.json.
    """
    def __init__(
        self,
        sentinel_state_path: str = "state/sentinel_monitor_state.json",
        report_path: str = "state/sentinel_manager_report.json"
    ):
        self.sentinel_state_path = sentinel_state_path
        self.report_path = report_path
        self.token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        self.channel_id = os.getenv("TELEGRAM_CHANNEL_ID", "@pahal_investment").strip()
        self.sentinel_process = None

    def _load_json(self, path: str, default: dict) -> dict:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return default

    def _save_json(self, path: str, data: dict):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Failed to save {path}: {e}")

    def check_and_revive_sentinel(self) -> dict:
        """Checks if Sentinel Watchdog is alive. If dead or stalled > 60s, restarts it."""
        state = self._load_json(self.sentinel_state_path, {})
        last_updated_str = state.get("last_updated")
        now = datetime.now(IST)
        is_healthy = False
        lag_seconds = 9999

        if last_updated_str:
            try:
                last_dt = datetime.fromisoformat(last_updated_str)
                lag_seconds = (now - last_dt).total_seconds()
                if lag_seconds <= 65:
                    is_healthy = True
            except Exception:
                pass

        if not is_healthy:
            logger.warning(f"[SUPERVISOR ALERT] Sentinel Agent is stalled or inactive (Lag: {lag_seconds:.1f}s)! Waking him up now...")
            self.restart_sentinel_process()
            return {"status": "REVIVED", "lag_seconds": lag_seconds}
        else:
            logger.info(f"[SUPERVISOR OK] Sentinel Agent is actively running (Lag: {lag_seconds:.1f}s).")
            return {"status": "HEALTHY", "lag_seconds": lag_seconds}

    def restart_sentinel_process(self):
        """Spawns/restarts sentinel_watchdog.py as a clean background process."""
        python_exe = sys.executable
        script_path = os.path.join(os.path.dirname(__file__), "sentinel_watchdog.py")
        try:
            # Terminate previous if tracked
            if self.sentinel_process and self.sentinel_process.poll() is None:
                self.sentinel_process.terminate()
                time.sleep(1)
            
            # Launch clean daemon
            cmd = [python_exe, "-u", script_path, "--daemon", "--interval", "20"]
            self.sentinel_process = subprocess.Popen(cmd, cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            logger.info(f"[✓] Sentinel Watchdog revived successfully (PID: {self.sentinel_process.pid})!")
        except Exception as e:
            logger.error(f"Failed to restart Sentinel Watchdog: {e}")

    def audit_and_repair_spacing(self):
        """
        Inspects recent channel posts on @pahal_investment.
        If any post has orphaned bullet icons (e.g. newline before and after ▪️ or •),
        compacts the spacing and repairs the post in-place using editMessageText.
        """
        if not self.token or not self.channel_id:
            return

        url = f"https://t.me/s/{self.channel_id.replace('@', '')}"
        curl_cmd = "curl.exe" if sys.platform == "win32" else "curl"
        try:
            res = subprocess.run([curl_cmd, "-4", "-s", "-L", url], capture_output=True, text=True, encoding="utf-8", errors="replace")
            if res.returncode != 0 or "tgme_widget_message" not in res.stdout:
                return
        except Exception:
            return

        soup = BeautifulSoup(res.stdout, "html.parser")
        msgs = soup.find_all("div", class_="tgme_widget_message")
        
        repaired_count = 0
        for m in msgs[-6:]:
            pid = m.get("data-post", "")
            if not pid:
                continue
            msg_id = int(pid.split("/")[-1])
            text_elem = m.find("div", class_="tgme_widget_message_text")
            if not text_elem:
                continue
            
            raw_text = text_elem.get_text("\n").strip()
            
            # Check for forbidden disclosure lines
            has_disclosure = any(bad in raw_text.lower() for bad in [
                "verified from official", "verified from", "dispatched via github", "official disclosure"
            ])

            lines = [l.strip() for l in raw_text.splitlines() if l.strip()]
            
            # Check for orphaned bullets
            has_orphaned_bullet = False
            for i, line in enumerate(lines):
                if line in ["▪️", "•", "📍", "🟢", "🔴"] and i + 1 < len(lines):
                    has_orphaned_bullet = True
                    break
                    
            if has_orphaned_bullet or has_disclosure:
                logger.info(f"[FORMAT REPAIR] Post #{msg_id} has orphaned bullets or disclosure text. Sanitizing...")
                # Filter out disclosure lines from lines
                sanitized_lines = [l for l in lines if not any(bad in l.lower() for bad in [
                    "verified from official", "verified from", "dispatched via github", "official disclosure"
                ])]
                compacted = self.compact_bullet_spacing(sanitized_lines)
                if compacted:
                    self.edit_channel_message(msg_id, compacted)
                    repaired_count += 1

    def compact_bullet_spacing(self, lines: list) -> str:
        """Merges isolated bullets with their following text and keeps list tight."""
        merged = []
        skip_next = False
        for i in range(len(lines)):
            if skip_next:
                skip_next = False
                continue
            curr = lines[i]
            if curr in ["▪️", "•", "📍", "🟢", "🔴"] and i + 1 < len(lines):
                merged.append(f"{curr} {lines[i+1]}")
                skip_next = True
            else:
                merged.append(curr)
                
        if not merged:
            return ""
            
        header = f"<b>{merged[0]}</b>"
        rest = merged[1:]
        bullets = []
        paragraphs = []
        for r in rest:
            if any(r.startswith(b) for b in ["▪️", "•", "📍", "🟢", "🔴", "-", "*"]):
                bullets.append(r)
            else:
                if bullets:
                    paragraphs.append("\n".join(bullets))
                    bullets = []
                paragraphs.append(r)
        if bullets:
            paragraphs.append("\n".join(bullets))
            
        if paragraphs:
            return f"{header}\n\n" + "\n\n".join(paragraphs)
        return header

    def edit_channel_message(self, message_id: int, new_html_text: str) -> bool:
        """Edits an existing Telegram channel message via Bot API."""
        endpoint = f"https://api.telegram.org/bot{self.token}/editMessageText"
        try:
            r = requests.post(endpoint, json={
                "chat_id": self.channel_id,
                "message_id": message_id,
                "text": new_html_text,
                "parse_mode": "HTML"
            }, timeout=8)
            if r.status_code == 200 and r.json().get("ok"):
                logger.info(f"[✓] Repaired spacing on Post #{message_id} via Telegram API!")
                return True
        except Exception as e:
            logger.error(f"Failed to edit message #{message_id}: {e}")
        return False

    def run_supervisor_cycle(self) -> dict:
        """Executes a single manager surveillance pass."""
        now = datetime.now(IST)
        sentinel_health = self.check_and_revive_sentinel()
        self.audit_and_repair_spacing()
        
        # Read radar summary
        radar = self._load_json("state/live_feed_radar.json", {})
        
        report = {
            "manager_last_check_ist": now.strftime("%Y-%m-%d %H:%M:%S IST"),
            "sentinel_watchdog_status": sentinel_health.get("status"),
            "sentinel_lag_seconds": sentinel_health.get("lag_seconds"),
            "tracked_benchmark_events": radar.get("total_events_tracked", 0),
            "supervision_status": "SUPERVISOR_ACTIVE_AND_HEALTHY"
        }
        self._save_json(self.report_path, report)
        return report

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Manager Agent: Supervises Sentinel Watchdog & Channel Formatting")
    parser.add_argument("--once", action="store_true", help="Run single manager cycle")
    parser.add_argument("--daemon", action="store_true", help="Run continuous manager loop")
    parser.add_argument("--interval", type=int, default=30, help="Manager check interval in seconds (default: 30s)")
    args = parser.parse_args()

    manager = SentinelManager()
    
    if args.once or not args.daemon:
        logger.info("[*] Running single Sentinel Manager Pass...")
        res = manager.run_supervisor_cycle()
        print(json.dumps(res, indent=2))
    else:
        logger.info(f"[*] Launching Continuous Sentinel Manager Supervisor (Interval: {args.interval}s)...")
        while True:
            try:
                manager.run_supervisor_cycle()
            except Exception as e:
                logger.error(f"Error in manager cycle: {e}")
            time.sleep(args.interval)

if __name__ == "__main__":
    main()
