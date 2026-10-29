"""
Phase 4: Persistent Deduplication State (src/state_store.py)
Maintains deduplication ledger (seen_ids and entity_event_hashes) across ephemeral GitHub runners:
- SQLite backing store (data/market_wire.db)
- Flat JSON persistent state snapshot (state/daemon_state.json)
- Automatic sync with Git repository on published posts and clean shutdown
"""

import os
import json
import sqlite3
import subprocess
import tempfile
import logging
import math
from typing import Set, Dict, Any, List

logger = logging.getLogger("StateStore")

class StateIntegrityError(RuntimeError):
    """Stop delivery when the deduplication ledger cannot be trusted."""


class StateStore:
    def __init__(self, db_path: str = None, json_state_path: str = None):
        self.base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.db_path = db_path or os.path.join(self.base_dir, "data", "market_wire.db")
        self.json_state_path = json_state_path or os.path.join(self.base_dir, "state", "daemon_state.json")
        
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        os.makedirs(os.path.dirname(self.json_state_path), exist_ok=True)
        
        self.seen_ids: Set[str] = set()
        self.semantic_hashes: Set[str] = set()
        self.published_records: List[Dict[str, Any]] = []
        self.event_cooldowns: Dict[str, float] = {}
        
        self._failed = False
        self._init_sqlite()
        self.load_state()

    def _fail(self, message, cause):
        self._failed = True
        raise StateIntegrityError(message) from cause

    def _init_sqlite(self):
        """Initialize SQLite schema if needed."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS seen_items (
                        item_id TEXT PRIMARY KEY,
                        source TEXT,
                        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS semantic_hashes (
                        fingerprint TEXT PRIMARY KEY,
                        entity TEXT,
                        category TEXT,
                        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS published_posts (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        item_id TEXT,
                        headline TEXT,
                        category TEXT,
                        published_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS event_cooldowns (
                        entity_key TEXT PRIMARY KEY,
                        last_ts REAL,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                conn.commit()
        except Exception as e:
            self._fail("Cannot initialize deduplication database; delivery stopped", e)

    def load_state(self):
        """Load state from SQLite, falling back to JSON state snapshot."""
        # 1. Load from SQLite
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT item_id FROM seen_items")
                for (item_id,) in cursor.fetchall():
                    self.seen_ids.add(str(item_id))
                
                cursor.execute("SELECT fingerprint FROM semantic_hashes")
                for (fp,) in cursor.fetchall():
                    self.semantic_hashes.add(str(fp))

                cursor.execute("SELECT entity_key, last_ts FROM event_cooldowns")
                for (ek, lts) in cursor.fetchall():
                    if ek and lts:
                        self.event_cooldowns[str(ek)] = float(lts)
        except Exception as e:
            self._fail("Cannot read deduplication database; delivery stopped", e)

        # 2. Merge from persistent JSON state snapshot
        if os.path.exists(self.json_state_path):
            try:
                with open(self.json_state_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._validate_snapshot(data)
                    self.seen_ids.update(data["seen_ids"])
                    self.semantic_hashes.update(data.get("semantic_hashes", []))
                    for k, v in data.get("event_cooldowns", {}).items():
                        self.event_cooldowns[k] = float(v)
                    self.published_records = data.get("published_records", [])[-200:]
                    logger.info(f"Loaded persistent state: {len(self.seen_ids)} IDs, {len(self.semantic_hashes)} semantic hashes, {len(self.event_cooldowns)} cooldown keys.")
            except Exception as e:
                self._fail("Cannot trust deduplication snapshot; restore or reconcile it before delivery", e)

    @staticmethod
    def _validate_snapshot(data):
        if not isinstance(data, dict):
            raise ValueError("Snapshot must be an object")
        for field in ("seen_ids", "semantic_hashes"):
            values = data.get(field)
            if not isinstance(values, list) or any(not isinstance(v, str) or not v for v in values):
                raise ValueError(f"Invalid or missing {field}")
        cooldowns = data.get("event_cooldowns", {})
        if not isinstance(cooldowns, dict) or any(
                not isinstance(k, str) or not isinstance(v, (int, float)) or isinstance(v, bool)
                or not math.isfinite(v) or v < 0 for k, v in cooldowns.items()):
            raise ValueError("Invalid event_cooldowns")
        records = data.get("published_records", [])
        if not isinstance(records, list) or any(not isinstance(v, dict) for v in records):
            raise ValueError("Invalid published_records")

    def save_state(self, sync_git: bool = False):
        """Save state to SQLite and JSON snapshot. Optionally sync to git in cloud."""
        if self._failed:
            raise StateIntegrityError("State is unhealthy; refusing to overwrite its snapshot")
        # Keep the complete ledger: slicing an unordered set discards arbitrary recent IDs.
        payload = {
            "seen_ids": sorted(self.seen_ids),
            "semantic_hashes": sorted(self.semantic_hashes),
            "event_cooldowns": dict(sorted(self.event_cooldowns.items())),
            "published_records": self.published_records[-200:],
        }
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=os.path.dirname(self.json_state_path),
                prefix=".state-", suffix=".json", delete=False
            ) as handle:
                temp_path = handle.name
                json.dump(payload, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.json_state_path)
        finally:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)

        if sync_git and os.getenv("GITHUB_ACTIONS") == "true":
            self._sync_git_state()

    def _sync_git_state(self):
        """Persist state across ephemeral GitHub Action runner VMs."""
        try:
            subprocess.run(["git", "config", "user.name", "PahalMarketWireDaemon"], check=False)
            subprocess.run(["git", "config", "user.email", "daemon@pahal.local"], check=False)
            subprocess.run(["git", "add", self.json_state_path], check=False)
            subprocess.run(["git", "commit", "-m", "chore: persist daemon state ledger [skip ci]"], check=False)
            subprocess.run(["git", "pull", "--rebase", "origin", "main"], check=False)
            subprocess.run(["git", "push", "origin", "main"], check=False)
        except Exception as e:
            logger.warning(f"Git state sync non-fatal error: {e}")

    def is_in_cooldown(self, entity: str, category: str, cooldown_seconds: int = 21600) -> bool:
        """Check if this (entity, category) event has already been published in the last 6 hours."""
        if not entity or not category:
            return False
        import time
        key = f"{entity.strip().upper()}:{category.strip().upper()}"
        now_ts = time.time()
        if key in self.event_cooldowns:
            last_ts = self.event_cooldowns[key]
            if (now_ts - last_ts) < cooldown_seconds:
                return True
        return False

    def record_cooldown(self, entity: str, category: str):
        """Record an event to start its 6-hour cooldown window."""
        if not entity or not category:
            return
        import time
        key = f"{entity.strip().upper()}:{category.strip().upper()}"
        now_ts = time.time()
        self.event_cooldowns[key] = now_ts
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT OR REPLACE INTO event_cooldowns (entity_key, last_ts, updated_at)
                    VALUES (?, ?, CURRENT_TIMESTAMP)
                """, (key, now_ts))
                conn.commit()
        except Exception as e:
            self._fail("Cannot persist delivery cooldown; delivery stopped", e)

    def is_seen(self, item_id: str, fingerprint: str = None) -> bool:
        """Check if an item ID or its semantic fingerprint has already been processed."""
        if str(item_id) in self.seen_ids:
            return True
        if fingerprint and fingerprint in self.semantic_hashes:
            return True
        return False

    def mark_seen(self, item_id: str, source: str = "UNKNOWN", fingerprint: str = None):
        """Record an item ID and optional semantic fingerprint."""
        self.seen_ids.add(str(item_id))
        if fingerprint:
            self.semantic_hashes.add(str(fingerprint))
        
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("INSERT OR IGNORE INTO seen_items (item_id, source) VALUES (?, ?)", (str(item_id), source))
                if fingerprint:
                    cursor.execute("INSERT OR IGNORE INTO semantic_hashes (fingerprint) VALUES (?)", (str(fingerprint),))
                conn.commit()
        except Exception as e:
            self._fail("Cannot persist deduplication ledger; delivery stopped", e)

    def record_published(self, item_id: str, headline: str, category: str):
        """Log a successfully dispatched Telegram announcement."""
        rec = {
            "item_id": str(item_id),
            "headline": headline,
            "category": category
        }
        self.published_records.append(rec)
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("INSERT INTO published_posts (item_id, headline, category) VALUES (?, ?, ?)",
                               (str(item_id), headline, category))
                conn.commit()
        except Exception as e:
            self._fail("Cannot persist published record; delivery stopped", e)
        self.save_state(sync_git=False)
