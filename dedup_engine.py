import os
import sqlite3
import hashlib
import re
from datetime import datetime, timezone
from config import DB_PATH

class DedupEngine:
    def __init__(self, db_path=DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        dir_name = os.path.dirname(self.db_path)
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sent_posts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content_hash TEXT UNIQUE,
                    fact_key TEXT UNIQUE,
                    event_key TEXT,
                    source TEXT,
                    raw_text TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            try:
                cursor.execute("ALTER TABLE sent_posts ADD COLUMN event_key TEXT")
            except sqlite3.OperationalError:
                pass
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_content_hash ON sent_posts(content_hash)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_fact_key ON sent_posts(fact_key)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_event_key ON sent_posts(event_key)")
            conn.commit()

    def normalize_text(self, text: str) -> str:
        """Strip punctuation, excessive whitespace, casing, currency symbols for fuzzy key comparison."""
        t = text.lower()
        t = re.sub(r'[^\w\s]', ' ', t)
        t = re.sub(r'\s+', ' ', t).strip()
        return t

    def compute_hash(self, text: str) -> str:
        norm = self.normalize_text(text)
        return hashlib.sha256(norm.encode('utf-8')).hexdigest()

    def extract_company_event_key(self, raw_text: str) -> str:
        """
        Extracts a unified company+event key (e.g. 'nazaratechnologies_allotment_20261012')
        to permanently prevent duplicate filings across BSE and NSE.
        """
        first_line = raw_text.splitlines()[0].strip()
        if "–" in first_line or "-" in first_line:
            parts = re.split(r'\s+[–—-]\s+', first_line)
            if len(parts) >= 2:
                raw_company = parts[0].strip().lower()
                subj = parts[1].strip().lower()
                comp = re.sub(r'\b(?:limited|ltd|corporation|corp|private|pvt)\b', '', raw_company).strip()
                comp = re.sub(r'[^a-z0-9]', '', comp)

                event_roots = [
                    'allotment', 'management', 'director', 'resignation', 'appointment',
                    'order', 'contract', 'merger', 'amalgamation', 'acquisition', 'stake',
                    'qip', 'dividend', 'warrant', 'rating', 'restructuring', 'result', 'bonus', 'split'
                ]
                matched_event = ""
                full_lower = raw_text.lower()
                for ev in event_roots:
                    if ev in subj or ev in full_lower:
                        matched_event = ev
                        break

                if comp and matched_event:
                    today = datetime.now().strftime("%Y%m%d")
                    return f"{comp}_{matched_event}_{today}"
        return ""

    def generate_fact_key(self, source: str, raw_text: str) -> str:
        clean = self.normalize_text(raw_text)
        numbers = re.findall(r'\b\d+(?:\.\d+)?\b', raw_text)
        num_str = "_".join(numbers[:4]) if numbers else ""
        
        words = [w for w in clean.split() if len(w) > 3 and w not in ('limited', 'ltd', 'shares', 'company', 'india', 'quarter', 'ended', 'announces', 'reports', 'informed')]
        sig_words = "_".join(sorted(words[:8]))
        
        key = f"{sig_words}_{num_str}" if num_str else sig_words
        return hashlib.md5(key.encode('utf-8')).hexdigest()

    def is_duplicate(self, text: str, source: str = "") -> bool:
        chash = self.compute_hash(text)
        fkey = self.generate_fact_key(source, text)
        ekey = self.extract_company_event_key(text)
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            if ekey:
                cursor.execute(
                    "SELECT id FROM sent_posts WHERE content_hash = ? OR fact_key = ? OR event_key = ?",
                    (chash, fkey, ekey)
                )
            else:
                cursor.execute(
                    "SELECT id FROM sent_posts WHERE content_hash = ? OR fact_key = ?",
                    (chash, fkey)
                )
            row = cursor.fetchone()
            return row is not None

    def record_post(self, text: str, source: str = ""):
        chash = self.compute_hash(text)
        fkey = self.generate_fact_key(source, text)
        ekey = self.extract_company_event_key(text)
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    "INSERT INTO sent_posts (content_hash, fact_key, event_key, source, raw_text) VALUES (?, ?, ?, ?, ?)",
                    (chash, fkey, ekey, source, text)
                )
                conn.commit()
            except sqlite3.IntegrityError:
                pass
