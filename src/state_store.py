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
    def __init__(self, db_path: str = None, json_state_path: str = None, allow_pending: bool = False):
        self.base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.db_path = db_path or os.path.join(self.base_dir, "data", "market_wire.db")
        self.json_state_path = json_state_path or os.path.join(self.base_dir, "state", "daemon_state.json")
        
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        os.makedirs(os.path.dirname(self.json_state_path), exist_ok=True)
        
        self.seen_ids: Set[str] = set()
        self.last_cycle_at = None          # ISO time of the last completed cycle (backlog guard)
        self.semantic_hashes: Set[str] = set()
        self.published_records: List[Dict[str, Any]] = []
        self.event_cooldowns: Dict[str, float] = {}
        self.pending_deliveries: Dict[str, Dict[str, str]] = {}
        self.pending_items: Dict[str, Dict[str, Any]] = {}
        self.morning_done: Dict[str, str] = {}
        self.overnight_items: List[Dict[str, Any]] = []
        self.overnight_pending_parts: List[str] = []
        self.overnight_window: Dict[str, str] = {}

        self._failed = False
        self._init_sqlite()
        self.load_state()
        if self.pending_deliveries and not allow_pending:
            raise StateIntegrityError("Unresolved Telegram delivery intent; reconcile before restarting delivery")

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
                    self.pending_deliveries = data.get("pending_deliveries", {})
                    self.last_cycle_at = data.get("last_cycle_at")
                    self.pending_items = {k: v for k, v in data.get("pending_items", {}).items()
                                          if k not in self.seen_ids}
                    self.morning_done = {
                        k: v for k, v in (data.get("morning_done") or {}).items()
                        if isinstance(k, str) and isinstance(v, str)
                    }
                    items = data.get("overnight_items") or []
                    self.overnight_items = [v for v in items if isinstance(v, dict) and v.get("id")]
                    parts = data.get("overnight_pending_parts") or []
                    self.overnight_pending_parts = [v for v in parts if isinstance(v, str) and v]
                    window = data.get("overnight_window") or {}
                    self.overnight_window = {
                        k: v for k, v in window.items()
                        if isinstance(k, str) and isinstance(v, str)
                    } if isinstance(window, dict) else {}
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
        pending = data.get("pending_deliveries", {})
        if not isinstance(pending, dict) or any(
                not isinstance(k, str) or not isinstance(v, dict)
                or not isinstance(v.get("post_fingerprint"), str) for k, v in pending.items()):
            raise ValueError("Invalid pending_deliveries")
        records = data.get("published_records", [])
        work = data.get("pending_items", {})
        if not isinstance(work, dict) or len(work) > 1024:
            raise ValueError("Invalid pending_items")
        for key, entry in work.items():
            if (not isinstance(key, str) or not isinstance(entry, dict)
                    or not isinstance(entry.get("item"), dict)
                    or entry["item"].get("id") != key):
                raise ValueError("Invalid pending item identity")
            if StateStore._work_item(entry["item"]) != entry["item"]:
                raise ValueError("Unknown pending item fields")
            retry = entry.get("retry_after", 0)
            if (not isinstance(retry, (int, float)) or isinstance(retry, bool)
                    or not math.isfinite(retry) or retry < 0
                    or not isinstance(entry.get("blocked_reason", ""), str)):
                raise ValueError("Invalid pending item retry state")
        if not isinstance(records, list) or any(not isinstance(v, dict) for v in records):
            raise ValueError("Invalid published_records")
        from src.market_quotes import validate_event
        for record in records:
            if "market_quote_event" in record:
                validate_event(record["market_quote_event"])
            if "result_event" in record:
                from src.result_events import validate_result_event
                validate_result_event(record["result_event"])

    def save_state(self, sync_git: bool = False):
        """Save state to SQLite and JSON snapshot. Optionally sync to git in cloud."""
        if self._failed:
            raise StateIntegrityError("State is unhealthy; refusing to overwrite its snapshot")
        # Keep the complete ledger: slicing an unordered set discards arbitrary recent IDs.
        payload = {
            "pending_items": self.pending_items,
            "pending_deliveries": self.pending_deliveries,
            "seen_ids": sorted(self.seen_ids),
            "semantic_hashes": sorted(self.semantic_hashes),
            "event_cooldowns": dict(sorted(self.event_cooldowns.items())),
            "published_records": self.published_records[-200:],
            "last_cycle_at": self.last_cycle_at,
            "morning_done": dict(sorted(self.morning_done.items())),
            "overnight_items": self.overnight_items[-200:],
            "overnight_pending_parts": list(self.overnight_pending_parts),
            "overnight_window": dict(self.overnight_window),
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
        except Exception as exc:
            self._fail("Cannot persist recovery snapshot; delivery stopped", exc)
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

    @staticmethod
    def _work_item(item):
        # Only public source fields belong in a recovery snapshot, never clients,
        # credentials, futures or arbitrary caller metadata.
        fields = ("id", "source", "company", "title", "description", "pdf_url",
                  "raw_subject", "source_url", "published_at", "subcat")
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
            raise ValueError("Pending item needs a string ID")
        result = {k: item[k] for k in fields if k in item and item[k] is not None}
        if any(not isinstance(v, str) or len(v) > 65536 for v in result.values()):
            raise ValueError("Invalid public source field")
        if len(json.dumps(result)) > 262144:
            raise ValueError("Pending item too large")
        return result

    def prune_pending(self, stale_ids=None):
        """Free journal slots held by already-decided or abandoned work.

        Terminal withheld PDFs and fresh-start backlog must not sit in the
        retry journal forever: once it fills, every later shift dies on start.
        """
        stale_ids = set(stale_ids or [])
        dropped = 0
        for key, entry in list(self.pending_items.items()):
            reason = (entry or {}).get("blocked_reason") or ""
            if key in self.seen_ids or key in stale_ids:
                self.mark_seen(key, (entry or {}).get("item", {}).get("source") or "BACKLOG")
                dropped += 1
            elif reason:
                # Held for review: keep while there is room, evict when stale_ids
                # asked us to (caller only puts blocked keys here when reclaiming).
                pass
        return dropped

    def evict_terminal_pending(self, needed=0):
        """Drop withheld items until needed slots are free, or none are left."""
        dropped = 0
        for key, entry in list(self.pending_items.items()):
            if len(self.pending_items) + needed <= 1024:
                break
            if (entry or {}).get("blocked_reason"):
                self.mark_seen(key, "PDF_REVIEW")
                dropped += 1
        return dropped

    def stage_items(self, items):
        """Checkpoint discovery before processing; failed sends stay retryable."""
        self.prune_pending()
        additions = {}
        for raw in items:
            item = self._work_item(raw)
            key = item["id"]
            if key not in self.seen_ids and key not in self.pending_items:
                additions[key] = {"item": item, "retry_after": 0, "blocked_reason": ""}
        self.evict_terminal_pending(needed=len(additions))
        room = max(0, 1024 - len(self.pending_items))
        if len(additions) > room:
            overflow = list(additions.keys())[room:]
            logger.warning("Pending journal at capacity; deferring %s new items", len(overflow))
            for key in overflow:
                additions.pop(key, None)
        if additions:
            self.pending_items.update(additions)
            self.save_state(sync_git=False)

    def defer_item(self, item_id, retry_after=0, blocked_reason=""):
        entry = self.pending_items.get(str(item_id))
        if entry is not None:
            entry.update(retry_after=retry_after, blocked_reason=blocked_reason)

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

    def begin_delivery(self, item_id, post_fingerprint):
        """Persist intent before sending; an interrupted send must not retry blindly."""
        if self.pending_deliveries:
            raise StateIntegrityError("Unresolved Telegram delivery intent; delivery stopped")
        self.pending_deliveries[str(item_id)] = {"post_fingerprint": str(post_fingerprint)}
        self.save_state(sync_git=False)

    def reconcile_delivery(self, item_id, delivered, message_id=None):
        """Operator-only recovery after checking the destination channel."""
        intent = self.pending_deliveries.get(str(item_id))
        if intent is None:
            raise ValueError("No pending delivery for this item")
        if delivered:
            if not isinstance(message_id, int) or isinstance(message_id, bool) or message_id <= 0:
                raise ValueError("Verified Telegram message ID is required")
            self.mark_seen(item_id, "RECONCILED", fingerprint=intent["post_fingerprint"])
            self.record_published(item_id, f"Reconciled Telegram message {message_id}", "RECOVERY")
        self.complete_delivery(item_id)

    def complete_delivery(self, item_id):
        """Clear intent only after definitive rejection or persisted acknowledgement."""
        self.pending_deliveries.pop(str(item_id), None)
        self.save_state(sync_git=False)

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
        self.pending_items.pop(str(item_id), None)
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


    def note_overnight(self, item, start=None, end=None):
        """Remember a closed-window filing for the morning roundup. Idempotent by item id."""
        if not isinstance(item, dict) or not item.get("id"):
            return False
        from src.overnight_roundup import in_closed_window, classify_kind
        published = item.get("published_at") or item.get("_backlog_at")
        if start is not None and end is not None and not in_closed_window(published, start, end):
            return False
        if classify_kind(item) is None:
            return False
        existing = {row.get("id") for row in self.overnight_items}
        if item["id"] in existing:
            # Prefer a later snapshot that carries scorecard evidence.
            for i, row in enumerate(self.overnight_items):
                if row.get("id") == item["id"]:
                    if item.get("_prebuilt") and not row.get("_prebuilt"):
                        self.overnight_items[i] = self._overnight_record(item)
                    break
            return False
        self.overnight_items.append(self._overnight_record(item))
        return True

    @staticmethod
    def _overnight_record(item):
        fields = ("id", "source", "company", "title", "description", "subcat",
                  "raw_subject", "published_at", "category")
        record = {k: item[k] for k in fields if k in item and isinstance(item[k], str)}
        record["id"] = item["id"]
        pre = item.get("_prebuilt")
        if isinstance(pre, dict) and pre.get("template") == "A" and isinstance(pre.get("card"), dict):
            card = pre["card"]
            metrics = card.get("metrics") or {}
            if "revenue" in metrics and "pat" in metrics:
                record["_prebuilt"] = {"template": "A", "card": {
                    "period": card.get("period", ""),
                    "scope": card.get("scope", ""),
                    "metrics": {
                        "revenue": {"cur": metrics["revenue"]["cur"]},
                        "pat": {"cur": metrics["pat"]["cur"]},
                    },
                }}
        return record

    def begin_overnight_window(self, start, end, today: str):
        """Freeze the closed window for this morning so later chunks do not widen it."""
        if self.overnight_window.get("date") == today:
            return
        self.overnight_window = {
            "date": today,
            "start": start.isoformat(),
            "end": end.isoformat(),
        }
        # Drop leftovers from a previous morning before we start collecting today.
        self.overnight_items = []
        self.overnight_pending_parts = []

    def record_published(self, item_id: str, headline: str, category: str, market_quote_event=None, result_event=None,
                         **extra):
        """Log a successfully dispatched Telegram announcement.

        `extra` (ts, company, event, lead) feeds the next morning's Stocks in Focus post.
        """
        rec = {
            "item_id": str(item_id),
            "headline": headline,
            "category": category
        }
        rec.update({k: v for k, v in extra.items() if v is not None})
        if market_quote_event is not None:
            from src.market_quotes import validate_event
            validate_event(market_quote_event)
            rec["market_quote_event"] = market_quote_event
        if result_event is not None:
            from src.result_events import validate_result_event
            validate_result_event(result_event)
            rec["result_event"] = result_event
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

