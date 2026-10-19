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
import logging
from typing import Set, Dict, Any, List

logger = logging.getLogger("StateStore")

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
        
        self._init_sqlite()
        self.load_state()

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
                conn.commit()
        except Exception as e:
            logger.error(f"Error initializing SQLite: {e}")

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
        except Exception as e:
            logger.warning(f"Could not read from SQLite: {e}")

        # 2. Merge from persistent JSON state snapshot
        if os.path.exists(self.json_state_path):
            try:
                with open(self.json_state_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.seen_ids.update(data.get("seen_ids", []))
                    self.semantic_hashes.update(data.get("semantic_hashes", []))
                    self.published_records = data.get("published_records", [])[-200:]
                    logger.info(f"Loaded persistent state: {len(self.seen_ids)} IDs, {len(self.semantic_hashes)} semantic hashes.")
            except Exception as e:
                logger.error(f"Error reading {self.json_state_path}: {e}")

    def save_state(self, sync_git: bool = False):
        """Save state to SQLite and JSON snapshot. Optionally sync to git in cloud."""
        try:
            with open(self.json_state_path, "w", encoding="utf-8") as f:
                json.dump({
                    "seen_ids": list(self.seen_ids)[-2000:],
                    "semantic_hashes": list(self.semantic_hashes)[-1000:],
                    "published_records": self.published_records[-200:]
                }, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save JSON state: {e}")

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
            logger.debug(f"SQLite insert: {e}")

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
            logger.debug(f"SQLite insert published: {e}")
        self.save_state(sync_git=False)
