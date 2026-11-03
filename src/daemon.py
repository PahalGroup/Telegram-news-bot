"""
Production Daemon: Real-Time Continuous Polling Engine (src/daemon.py)
SINGLE source of truth for GitHub Actions production.

Concurrent polling loop (not a latency guarantee):
- Runs for configurable duration per workflow run (default: 54 min)
- Polls upstream endpoints concurrently, with a 12-second pause between cycles
- Stage 1: Deterministic Hard Filter (src/filter_engine.py)
- Stage 2: Few-Shot Calibrated AI Materiality Gate
- Pixel-Accurate Formatter (Telegram HTML)
- Persistent state management via src.state_store
- Workflow-owned handoff after state persistence; startup gaps remain possible
"""

import os
import html
import re
import sys
import time
import argparse
import logging
import tempfile
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
from multiprocessing import get_context
from src.quality import fingerprint
from decimal import Decimal, InvalidOperation
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
PERMANENT_PDF_REJECTIONS = frozenset({"PDF_SIZE_LIMIT_EXCEEDED", "PDF_INVALID_CONTENT", "PDF_URL_NOT_HTTPS"})
logger = logging.getLogger("RealtimeDaemon")

# Imports with graceful fallback
from src.filter_engine import FilterEngine
from src.formatter import Formatter
from src.state_store import StateStore, StateIntegrityError
from telegram_dispatcher import TelegramDispatcher

# Source fetchers
from sources.bse_fetcher import BSEFetcher
from sources.nse_fetcher import NSEFetcher, _side_session
from sources.rbi_fetcher import RBIFetcher
from sources.news_fetcher import NewsFetcher
from sources.liveblog_fetcher import LiveBlogFetcher
from sources.pib_fetcher import PIBFetcher
from sources.x_fetcher import XFetcher
from src import editorial, morning_pack
from src.composer import compose_filing, compose_prebuilt
from src.scheduler import IST, Outbox, fresh_enough, in_active_window, parse_time

# Seconds between polls for slower sources (exchange feeds poll every cycle).
SOURCE_INTERVALS = {"deals": 60, "pib": 120, "x": 120, "rbi_speeches": 300}


# RBI housekeeping notices: survey launches, auction calendars without amounts, office notices.
MACRO_PROCEDURAL = re.compile(r"\blaunches\b.*\bsurvey\b|\bround of\b.*\bsurvey\b|\bholiday\b|\brecruitment\b", re.I)


class RealtimeDaemon:
    # Upgrade state is created on first use so partially constructed daemons
    # (tests build them with __new__) behave like fully initialised ones.
    _LAZY = {
        "pib": PIBFetcher, "x": XFetcher, "rules": editorial.load_rules, "outbox": Outbox,
        "clock": lambda: (lambda: datetime.now(IST)), "dry_posts": list, "morning_done": dict,
        "ipo_done": set, "_last_submit": dict, "_pib_desc": dict,
    }

    def __getattr__(self, name):
        factory = RealtimeDaemon._LAZY.get(name)
        if factory is None:
            raise AttributeError(name)
        value = factory()
        setattr(self, name, value)
        return value

    def __init__(self, dry_run: bool = False, shift_seconds: int = 3240):
        self.dry_run = dry_run
        self.shift_seconds = shift_seconds
        self.start_time = time.time()

        self.dispatcher = TelegramDispatcher()
        if not dry_run and not self.dispatcher.is_live:
            raise RuntimeError("Live mode requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHANNEL_ID.")
        # Dry runs must never consume the production dedup ledger.
        self._dry_state_dir = tempfile.TemporaryDirectory(prefix="market-wire-dry-") if dry_run else None
        if self._dry_state_dir:
            self.state = StateStore(
                db_path=os.path.join(self._dry_state_dir.name, "market_wire.db"),
                json_state_path=os.path.join(self._dry_state_dir.name, "daemon_state.json"),
            )
        else:
            self.state = StateStore()
        self.filter_engine = FilterEngine()

        # Ingest Sources
        self.bse = BSEFetcher()
        self.nse = NSEFetcher()
        self.rbi = RBIFetcher()
        self.news = NewsFetcher()
        self.liveblog = LiveBlogFetcher()

        self.poll_interval = int(os.getenv("POLL_INTERVAL", "15"))  # seconds between cycles
        self.fii_posted_today = False
        self.cycle_count = 0
        self.total_dispatched = 0
        self.total_errors = 0

    def _trace(self, stage, item, reason='', **details):
        observer = getattr(self, "observer", None)
        if observer is not None:
            observer.record(stage, item, reason, **details)

    def poll_all_sources(self, wait_sources=False, submit_sources=True) -> List[Dict[str, Any]]:
        """Fetch raw announcements from ALL upstream sources with correct method names."""
        raw_items = []
        # Keep one request per adapter in flight. Drain only completed requests;
        # a slow feed must not hold up already available exchange announcements.
        if not hasattr(self, "_source_pool"):
            self._source_pool = ThreadPoolExecutor(max_workers=5)
            self._source_pending = {}
        calls = {
            "bse": lambda: self.bse.fetch_announcements(skip_pdf=True),
            "nse": self.nse.fetch_announcements,
            "rbi": self.rbi.fetch_press_releases,
            "news": self.news.fetch_all_feeds,
            "liveblog": self.liveblog.fetch_live_flashes,
            "deals": self.nse.fetch_large_deals,
            "pib": self._fetch_pib,
            "rbi_speeches": self.rbi.fetch_speeches,
        }
        if self.x.enabled:
            calls["x"] = self.x.fetch_recent
        for name, call in calls.items():
            due = time.time() - self._last_submit.get(name, 0) >= SOURCE_INTERVALS.get(name, 0)
            if submit_sources and name not in self._source_pending and due:
                self._last_submit[name] = time.time()
                self._source_pending[name] = self._source_pool.submit(call)
        futures = self._source_pending.copy()
        if wait_sources:
            from concurrent.futures import wait
            wait(futures.values())
        completed = {name for name, future in futures.items() if future.done()}
        for name in completed:
            del self._source_pending[name]

        # 1. BSE Corporate Announcements
        try:
            bse_items = (futures["bse"].result() or []) if "bse" in completed else []
            for it in bse_items:
                raw_items.append({
                    "id": f"BSE_{it.get('newsid', it.get('raw_subject', ''))}",
                    "source": "BSE",
                    "company": it.get("company", ""),
                    "title": it.get("subject", "") or it.get("raw_subject", ""),
                    "description": f"{it.get('headline', '')} {it.get('more', '')}".strip(),
                    "pdf_url": it.get("pdf_url"),
                    "raw_subject": it.get("raw_subject", ""),
                    "source_url": it.get("pdf_url"),
                    "published_at": it.get("news_dt"),
                    "subcat": it.get("subcat", ""),
                })
            if "bse" in completed:
                page_errors = getattr(self.bse, "last_health", {}).get("page_errors", {})
                if page_errors:
                    self.total_errors += len(page_errors)
                    logger.warning("BSE page failures: %s", page_errors)
                logger.info(f"[BSE] Fetched {len(bse_items)} items")
        except Exception as e:
            logger.warning(f"BSE poll error: {e}")
            self.total_errors += 1

        # 2. NSE Corporate Filings
        try:
            nse_items = (futures["nse"].result() or []) if "nse" in completed else []
            for it in nse_items:
                symbol = it.get("symbol", "")
                subject = it.get("desc", "") or it.get("subject", "")
                company_name = it.get("sm_name", "") or symbol
                attchmnt_text = it.get("attchmntText", "") or it.get("details", "")
                title = attchmnt_text or subject
                pdf_url = it.get("attchmntFile")
                seq_id = it.get("seq_id") or fingerprint(symbol, str(pdf_url or "") + str(it.get("dt", "")) + title)

                raw_items.append({
                    "id": f"NSE_{symbol}_{seq_id}",
                    "source": "NSE",
                    "company": company_name,
                    "title": title,
                    "description": attchmnt_text,
                    "pdf_url": pdf_url,
                    "source_url": pdf_url,
                    "published_at": it.get("an_dt") or it.get("dt"),
                })
            if "nse" in completed:
                logger.info(f"[NSE] Fetched {len(nse_items)} items")
        except Exception as e:
            logger.warning(f"NSE poll error: {e}")
            self.total_errors += 1

        # 3. RBI Press Releases
        try:
            rbi_items = (futures["rbi"].result() or []) if "rbi" in completed else []
            for it in rbi_items:
                raw_items.append({
                    "id": "RBI_" + fingerprint("", it.get("link") or it.get("title", "")),
                    "source": "RBI",
                    "company": "RESERVE BANK OF INDIA",
                    "title": it.get("title", ""),
                    "description": it.get("description", ""),
                    "source_url": it.get("link"),
                    "published_at": it.get("pub_date"),
                })
            if "rbi" in completed:
                logger.info(f"[RBI] Fetched {len(rbi_items)} items")
        except Exception as e:
            logger.warning(f"RBI poll error: {e}")
            self.total_errors += 1

        # 4. News RSS Wires (Economic Times, Moneycontrol, Mint, BusinessLine)
        try:
            news_items = (futures["news"].result() or []) if "news" in completed else []
            for it in news_items:
                raw_items.append({
                    "id": f"NEWS_{it.get('link', it.get('title', '')[:40])}",
                    "source": "NEWS",
                    "company": "",  # Extracted by filter
                    "title": it.get("title", ""),
                    "description": it.get("description", ""),
                    "source_url": it.get("link", ""),
                    "published_at": it.get("pub_date"),
                })
            if "news" in completed:
                failures = [url for url, health in getattr(self.news, "last_health", {}).items()
                            if health.get("status") == "error"]
                if failures:
                    self.total_errors += sum(health.get("status") == "error" and health.get("attempted", True)
                                             for health in self.news.last_health.values())
                    logger.warning("RSS sources failed: %s", failures)
                logger.info(f"[NEWS] Fetched {len(news_items)} items")
        except Exception as e:
            logger.warning(f"News poll error: {e}")
            self.total_errors += 1

        # 5. LiveBlog Breaking (Moneycontrol real-time flashes)
        try:
            lb_items = (futures["liveblog"].result() or []) if "liveblog" in completed else []
            for it in lb_items:
                raw_items.append({
                    "id": f"LIVE_{it.get('id', it.get('title', '')[:30])}",
                    "source": "LIVEBLOG",
                    "company": "",
                    "title": it.get("title", ""),
                    "description": it.get("description", ""),
                    "source_url": it.get("link", ""),
                    "published_at": it.get("pub_date"),
                })
            if "liveblog" in completed:
                logger.info(f"[LIVEBLOG] Fetched {len(lb_items)} items")
        except Exception as e:
            logger.warning(f"LiveBlog poll error: {e}")
            self.total_errors += 1

        raw_items.extend(self._map_new_sources(futures, completed))

        observer = getattr(self, "observer", None)
        if observer is not None:
            if not hasattr(self, "_observed_sources"):
                self._observed_sources = set()
            source_names = {"bse": "BSE", "nse": "NSE", "rbi": "RBI", "news": "NEWS", "liveblog": "LIVEBLOG",
                            "deals": "NSE_DEALS", "pib": "PIB", "rbi_speeches": "RBI_SPEECH", "x": "X"}
            for name in completed:
                batch = [it for it in raw_items if it["source"] == source_names[name]]
                for it in batch:
                    it["_warm_start"] = name not in self._observed_sources
                status = "error" if futures[name].exception() else "ok"
                if name == "bse" and getattr(self.bse, "last_health", {}).get("page_errors"):
                    status = "partial_error"
                if name == "news" and any(h.get("status") == "error" for h in getattr(self.news, "last_health", {}).values()):
                    status = "partial_error"
                observer.source(source_names[name], status, count=len(batch))
                if status == "ok":
                    self._observed_sources.add(name)
        return raw_items

    def check_fii_dii_routine(self):
        """Check and dispatch daily FII/DII activity on trading weekdays between 18:00 and 22:00 IST."""
        now_utc = datetime.now(timezone.utc)
        ist_now = now_utc + timedelta(hours=5, minutes=30)

        # Do not dispatch on weekends (Saturday / Sunday) when cash markets are closed
        if ist_now.weekday() in (5, 6):
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
                    flows = {}
                    for entry in fii_data:
                        if str(entry.get("date", "")).upper() != today_str:
                            continue
                        cat = str(entry.get("category", "")).upper()
                        raw_net = str(entry.get("netValue", "")).replace(",", "").strip()
                        try:
                            net = Decimal(raw_net)
                        except InvalidOperation:
                            continue
                        if not net.is_finite():
                            continue
                        key = "FII" if ("FII" in cat or "FPI" in cat) else ("DII" if "DII" in cat else None)
                        if key:
                            flows[key] = net
                    if set(flows) != {"FII", "DII"}:
                        logger.warning("FII/DII data missing, invalid, or stale; waiting for today's figures.")
                        return
                    fii_net, dii_net = flows["FII"], flows["DII"]
                    trade_date = today_str
                    formatted_msg = Formatter.format_fii_dii(trade_date, fii_net, dii_net)
                    logger.info(f"Posting daily FII/DII flows for {trade_date}...")
                    if not self.dry_run:
                        self.state.begin_delivery(fii_key, fingerprint("", formatted_msg))
                        try:
                            delivery = self.dispatcher.send_message(formatted_msg, parse_mode="HTML")
                        except Exception as exc:
                            raise StateIntegrityError("FII/DII delivery is uncertain; reconcile the saved intent") from exc
                        if not delivery:
                            self.state.complete_delivery(fii_key)
                            self.total_errors += 1
                            logger.error("FII/DII delivery failed; leaving it eligible for retry.")
                            return
                    else:
                        print(f"\n[DRY RUN FII/DII DISPATCH]:\n{formatted_msg}\n")
                    self.state.mark_seen(fii_key, "NSE_FLOWS")
                    self.state.record_published(fii_key, f"FII/DII ACTIVITY – {trade_date}", "FII_DII_FLOWS")
                    if not self.dry_run:
                        self.state.complete_delivery(fii_key)
                    self.fii_posted_today = True
            except StateIntegrityError:
                raise
            except Exception as e:
                self.total_errors += 1
                logger.warning(f"FII/DII fetch routine failed: {e}")

    def prepare_items(self, items, wait_pdfs=False):
        """Bounded PDF queue: slow filings never block text-only flashes.
        Deferred and failed PDFs stay in a bounded in-memory backlog; failures back off.
        """
        items = list(items)
        if not hasattr(self, "_pdf_pending"):
            self._pdf_pending = {}
            journal = getattr(self.state, "pending_items", {})
            self._pdf_retry = {key: entry.get("retry_after", 0) for key, entry in journal.items()} if isinstance(journal, dict) else {}
            self._pdf_pool = None
        if not hasattr(self, "_pdf_backlog"):
            self._pdf_backlog = {}
        if not hasattr(self, "_pdf_blocked"):
            journal = getattr(self.state, "pending_items", {})
            self._pdf_blocked = {key: entry["blocked_reason"] for key, entry in journal.items()
                                 if entry.get("blocked_reason")} if isinstance(journal, dict) else {}
        if not hasattr(self, "_pdf_outcomes"):
            self._pdf_outcomes = {"extracted": 0, "unsupported": 0, "errors": 0, "queue_full_deferrals": 0}
        ready = []
        for key, (item, future) in list(self._pdf_pending.items()):
            if not future.done():
                continue
            del self._pdf_pending[key]
            failed = False
            blocked_reason = ""
            lead = ""
            try:
                lead, bullets = future.result()
            except Exception as exc:
                failed = True
                bullets = []
                reason = getattr(exc, "reason", "")
                if reason in PERMANENT_PDF_REJECTIONS:
                    # The exchange published a file we refuse by policy (oversized, not a
                    # PDF, not https). That is a property of the filing, not a fault in the
                    # shift: withhold it for review without failing the run.
                    logger.warning("Withheld %s: %s", key, reason)
                    self._pdf_outcomes["unsupported"] += 1
                    self._trace("pdf_blocked", item, reason)
                else:
                    logger.exception("PDF worker failed: %s", key)
                    self.total_errors += 1
                    self._pdf_outcomes["errors"] += 1
                    self._trace("pdf_error", item, "EXTRACTION_ERROR")
                if getattr(exc, "retryable", True) is False:
                    blocked_reason = reason or "PDF_REQUIRES_REVIEW"
            if isinstance(lead, dict) and bullets:
                item["_prebuilt"] = lead
            if bullets:
                self._pdf_outcomes["extracted"] += 1
                self._trace("pdf_extracted", item, evidence_points=len(bullets))
                item["description"] = "\n".join(bullets)
                ready.append(item)
            else:
                if not failed:
                    self._pdf_outcomes["unsupported"] += 1
                    self._trace("pdf_unsupported", item, "NO_RELIABLE_PDF_EVIDENCE")
                    blocked_reason = "NO_RELIABLE_PDF_EVIDENCE"
                self._pdf_retry[key] = 0 if blocked_reason else time.time() + 300
                if blocked_reason:
                    self._pdf_blocked[key] = blocked_reason
                if isinstance(getattr(self.state, "pending_items", None), dict):
                    self.state.defer_item(key, self._pdf_retry[key], blocked_reason)
                self._pdf_backlog[key] = item
                logger.warning("Withheld %s: no reliable PDF evidence", key)
        # Sources need not return an item again. Retain deferrals locally and
        # service them before new PDF arrivals; text items still pass immediately.
        for item in items:
            self._trace("discovered", item)
        work = list(self._pdf_backlog.values()) + list(items)
        considered = set()
        for item in work:
            key = item.get("id")
            if key in considered:
                continue
            considered.add(key)
            if not key or self.state.is_seen(key):
                self._trace("already_seen", item)
                self._pdf_backlog.pop(key, None)
                continue
            if key in self._pdf_blocked:
                self._pdf_backlog[key] = item
                continue
            if self._pdf_retry.get(key, 0) > time.time():
                continue
            url = str(item.get("pdf_url") or "")
            is_pdf = url.lower().split("?")[0].endswith(".pdf")
            if is_pdf and item.get("source") in ("BSE", "NSE"):
                context = f"{item.get('title', '')} {item.get('raw_subject', '')}"
                event = editorial.classify(item, self.rules)
                if event.rule == "always_drop" or self.filter_engine.should_drop(context):
                    self._trace("rejected", item, "TITLE_HARD_FILTER")
                    self.state.mark_seen(key, item.get("source", "UNKNOWN"))
                    self._pdf_backlog.pop(key, None)
                    continue
                if any(r.get("id") == key for r in ready):
                    continue
                if key in self._pdf_pending or self._pdf_retry.get(key, 0) > time.time():
                    continue
                if len(self._pdf_pending) >= 32:
                    if key not in self._pdf_backlog and len(self._pdf_backlog) >= 512:
                        self.total_errors += 1
                        self._trace("queue_overflow", item, "PDF_BACKLOG_LIMIT_EXCEEDED")
                        raise RuntimeError("PDF backlog limit exceeded; unprocessed filings require recovery")
                    self._pdf_backlog[key] = item
                    self._pdf_outcomes["queue_full_deferrals"] += 1
                    self._trace("queue_deferred", item, "PDF_QUEUE_FULL")
                    continue
                if self._pdf_pool is None:
                    # PyMuPDF is not thread-safe. Each worker owns its document state.
                    self._pdf_pool = ProcessPoolExecutor(max_workers=3, mp_context=get_context("spawn"))
                self._trace("pdf_queued", item)
                from src.pdf_router import extract
                self._pdf_pending[key] = (item, self._pdf_pool.submit(extract, url, event.label))
                self._pdf_backlog.pop(key, None)
                self._pdf_retry.pop(key, None)
            else:
                ready.append(item)
        if wait_pdfs and self._pdf_pending:
            from concurrent.futures import wait
            wait([future for _, future in self._pdf_pending.values()], timeout=60)
            ready.extend(self.prepare_items([]))
            if self._pdf_pending:
                self.total_errors += len(self._pdf_pending)
                logger.error("Single-pass PDF timeout: %s pending filings", len(self._pdf_pending))
        return ready

    # ------------------------------------------------------------ new sources
    def _fetch_pib(self):
        from sources.pib_fetcher import detail_summary, is_market_release
        out = []
        for rel in self.pib.fetch_releases():
            if not is_market_release(rel["title"]):
                continue
            if rel["id"] not in self._pib_desc:
                try:
                    self._pib_desc[rel["id"]] = detail_summary(self.pib.session.get(rel["link"], timeout=15).text)
                except Exception:
                    self._pib_desc[rel["id"]] = ""
            out.append(dict(rel, summary=self._pib_desc[rel["id"]]))
        return out

    def _map_new_sources(self, futures, completed):
        """Deals, PIB, RBI speeches and X posts arrive already in template form."""
        items = []
        for name in ("deals", "pib", "rbi_speeches", "x"):
            if name not in completed:
                continue
            try:
                batch = futures[name].result() or []
            except Exception as e:
                logger.warning("%s poll error: %s", name, e)
                self.total_errors += 1
                continue
            for it in batch:
                if name == "deals":
                    d = it
                    # The snapshot is the last session's; never replay yesterday's deals next morning.
                    try:
                        if datetime.strptime(str(d["date"]), "%d-%b-%Y").date() != self.clock().date():
                            continue
                    except ValueError:
                        continue
                    items.append({"id": f"DEAL_{d['kind']}_{d['symbol']}_{fingerprint(d['client'], d['side'])}_{d['date']}",
                                  "source": "NSE_DEALS", "company": d["company"], "title": d["kind"],
                                  "_prebuilt": {"template": "E", "deal": d}})
                elif name == "pib":
                    bullets = [it["summary"]] if it.get("summary") and it["summary"].rstrip(". ") not in it["title"] else []
                    items.append({"id": it["id"], "source": "PIB", "company": "", "title": it["title"],
                                  "source_url": it["link"],
                                  "_prebuilt": {"template": "MACRO", "label": "PIB", "title": it["title"], "bullets": bullets}})
                elif name == "rbi_speeches":
                    items.append({"id": "RBISP_" + fingerprint("", it.get("link") or it.get("title", "")),
                                  "source": "RBI_SPEECH", "company": "", "title": it.get("title", ""),
                                  "source_url": it.get("link"), "published_at": it.get("pub_date"),
                                  "_prebuilt": {"template": "MACRO", "label": "RBI SPEECH", "title": it.get("title", ""),
                                                "bullets": editorial.pick_bullets(it.get("description", ""), limit=4)}})
                else:
                    text = editorial.strip_junk(it["title"])
                    first, _, rest = text.partition("\n")
                    if not (editorial.NUMBER.search(text) or editorial.DECISION.search(text)):
                        continue
                    items.append({"id": it["id"], "source": "X", "company": "", "title": first,
                                  "source_url": it["link"], "published_at": it.get("published_at"),
                                  "_prebuilt": {"template": "MACRO", "label": "@" + it["handle"], "title": first,
                                                "bullets": [l for l in rest.splitlines() if l.strip()][:5]}})
        return items

    # ------------------------------------------------------------ cycle
    def _reject(self, it, reason):
        self._trace("rejected", it, reason)
        self.state.mark_seen(it["id"], it.get("source", "UNKNOWN"))

    def process_cycle(self, wait_sources=False) -> int:
        """Poll, gate, compose into the outbox, then release at most one paced post."""
        self.cycle_count += 1
        raw_items = self.poll_all_sources(wait_sources=True) if wait_sources else self.poll_all_sources()
        self._last_raw = raw_items
        now = self.clock()
        prebuilt = [it for it in raw_items if "_prebuilt" in it]
        raw_items = [it for it in raw_items if "_prebuilt" not in it]
        if isinstance(getattr(self.state, "pending_items", None), dict):
            warm_start = {item.get("id"): item.get("_warm_start", False) for item in raw_items}
            self.state.stage_items(raw_items)
            # Work on copies: PDF extraction must not rewrite the recovery source evidence.
            raw_items = [dict(entry["item"], _warm_start=warm_start.get(key, True))
                         for key, entry in self.state.pending_items.items()]
        items = self.prepare_items(raw_items, wait_pdfs=True) if wait_sources else self.prepare_items(raw_items)
        for it in prebuilt:
            self._trace("discovered", it)
        items = list(items) + prebuilt

        for it in items:
            item_id = it.get("id")
            if not item_id or self.state.is_seen(item_id) or self.outbox.has(item_id):
                continue
            try:
                self._compose(it, now)
            except StateIntegrityError:
                raise
            except Exception as e:
                self._trace("processing_error", it, "COMPOSE_ERROR")
                logger.warning("Compose error for %s: %s", item_id, e)
                self.total_errors += 1

        self.check_morning_pack(now)
        self.check_ipo_listings(now)
        self.check_fii_dii_routine()
        dispatched_count = self.release_due(now)
        self.total_dispatched += dispatched_count
        self.state.save_state(sync_git=False)
        return dispatched_count

    def _compose(self, it, now):
        item_id = it["id"]
        published = parse_time(it.get("published_at"))
        pre = it.get("_prebuilt")
        if pre:
            label = {"A": "RESULTS", "C": "EARNINGS CALL"}.get(pre.get("template"), "")
        else:
            event = editorial.classify(it, self.rules) if it.get("source") in ("BSE", "NSE") else None
            label = event.label if event else ""
        if published and not fresh_enough(published, now, label):
            return self._reject(it, "STALE")
        if it.get("source") == "RBI" and MACRO_PROCEDURAL.search(it.get("title", "")):
            return self._reject(it, "PROCEDURAL")

        result_event = market_quote_event = None
        category, norm_company, event_fp, headline = label, it.get("company", ""), None, ""
        if pre:
            pending, why = compose_prebuilt(it, now, published)
            headline = pending.header if pending else ""
        else:
            if event is not None and event.rule == "always_drop":
                return self._reject(it, event.reason)
            passed_s1, reason_s1, candidate = self.filter_engine.evaluate_stage_1(it)
            if not passed_s1:
                return self._reject(it, str(reason_s1))
            norm_company = self.filter_engine.normalize_entity_name(candidate.get("company", ""))
            category = candidate.get("category", "")
            event_fp = fingerprint(norm_company, it.get("title", "") + "\n" + it.get("description", ""))
            from src.market_quotes import is_quote_story
            if not is_quote_story(candidate) and category != "FINANCIAL_RESULTS" and self.state.is_seen(item_id, fingerprint=event_fp):
                self._trace("duplicate", it, "COMPLETE_CLAIM")
                self.state.mark_seen(item_id, it.get("source", "UNKNOWN"))
                return
            eval_data = self.filter_engine.evaluate_stage_2(candidate)
            self._trace("evaluated", it, category=category, evidence_rejections=eval_data.get("evidence_rejections", {}))
            if not eval_data.get("publish", False) or eval_data.get("materiality_score", 0) < 70:
                return self._reject(it, str(eval_data.get("rejection_reason") or "INSUFFICIENT_MATERIAL_EVIDENCE"))
            if category == "FINANCIAL_RESULTS":
                from src.result_events import select_new_result_points
                points, result_event, _updated = select_new_result_points(
                    eval_data.get("summary_points", []), it.get("company") or candidate.get("company", ""),
                    it, self.state.published_records)
                if not points:
                    self._trace("duplicate", it, "RESULT_FACTS_ALREADY_REPORTED_OR_OLDER")
                    self.state.mark_seen(item_id, it.get("source", "UNKNOWN"))
                    return
                eval_data = dict(eval_data, summary_points=points)
                if _updated:
                    eval_data["headline"] = eval_data.get("headline", "").replace(
                        "FINANCIAL RESULTS", "FINANCIAL RESULTS UPDATE")
                    if event is not None:
                        event = editorial.Event(event.label + " UPDATE", event.rule)
            if eval_data.get("market_quotes"):
                from src.market_quotes import select_new_quotes
                quotes, market_quote_event = select_new_quotes(eval_data["market_quotes"], it, self.state.published_records)
                if not quotes:
                    self._trace("duplicate", it, "MARKET_QUOTE_ALREADY_REPORTED_OR_OLDER")
                    self.state.mark_seen(item_id, it.get("source", "UNKNOWN"))
                    return
                eval_data = dict(eval_data, summary_points=[q["text"] for q in quotes])
            if event is not None:
                # Gate on the evidence that will be posted, not just the filing title.
                evidence = "\n".join([it.get("description") or ""] + list(eval_data.get("summary_points") or []))
                keep, why = editorial.gate(dict(it, description=evidence), event, self.rules)
                if not keep:
                    return self._reject(it, why)
            if not it.get("company") and candidate.get("company") and it.get("source") in ("BSE", "NSE"):
                it = dict(it, company=candidate["company"])
            pending, why = compose_filing(it, eval_data, event or editorial.Event("UPDATE", "macro"), now, published)
            headline = eval_data.get("headline", "")
        if pending is None:
            return self._reject(it, why)
        post_fp = fingerprint("", pending.text())
        if market_quote_event is None and result_event is None and self.state.is_seen(item_id, fingerprint=post_fp):
            self._trace("duplicate", it, "FORMATTED_POST")
            self.state.mark_seen(item_id, it.get("source", "UNKNOWN"))
            return
        pending.meta = {item_id: {"source": it.get("source", "UNKNOWN"), "event_fp": event_fp, "post_fp": post_fp,
                                  "company": norm_company, "category": category, "headline": headline,
                                  "result_event": result_event, "market_quote_event": market_quote_event}}
        status = self.outbox.put(pending)
        if status == "MERGED_ALREADY_SENT":
            self._trace("duplicate", it, status)
            self.state.mark_seen(item_id, it.get("source", "UNKNOWN"))
            return
        self._trace("queued", it, status, formatted_post=pending.text())

    def _send(self, text, key):
        """Deliver one post (or record it in a dry run). Returns True when delivered."""
        if self.dry_run:
            self.dry_posts.append({"key": key, "at": self.clock().isoformat(), "text": text})
            print(f"\n[DRY RUN DISPATCH {self.clock():%H:%M:%S} IST]:\n{text}\n{'-'*50}")
            return True
        delivery = Formatter.execute_with_retry(self.dispatcher.send_message, text, parse_mode="HTML")
        return bool(delivery)

    def release_due(self, now) -> int:
        for p in self.outbox.drop_overnight(now):
            for iid in p.ids:
                self._trace("rejected", {"id": iid}, "STALE_OVERNIGHT")
                self.state.mark_seen(iid, p.meta.get(iid, {}).get("source", "UNKNOWN"))
        p = self.outbox.due(now)
        if p is None:
            return 0
        text = p.text()
        if len(text.encode("utf-16-le")) // 2 > Formatter.MAX_TELEGRAM_LEN or not Formatter.validate_completeness(text):
            for iid in p.ids:
                self._trace("rejected", {"id": iid}, "FORMATTING_INCOMPLETE")
                self.state.mark_seen(iid, p.meta.get(iid, {}).get("source", "UNKNOWN"))
            return 0
        primary = p.ids[0]
        post_fp = fingerprint("", text)
        if not self.dry_run:
            self.state.begin_delivery(primary, post_fp)
            try:
                ok = self._send(text, primary)
            except Exception as e:
                self.total_errors += 1
                raise StateIntegrityError("Telegram delivery is uncertain; reconcile the saved intent") from e
            if not ok:
                self.state.complete_delivery(primary)
                self.total_errors += 1
                logger.error("Telegram delivery failed for %s; leaving it eligible for retry.", primary)
                # Nothing was sent: undo the pacing/merge bookkeeping so the next
                # cycle recomposes the items from source and tries again.
                self.outbox.recent.pop(self.outbox.merge_key(p.company, p.event), None)
                self.outbox.last_sent = None
                return 0
        else:
            self._send(text, primary)
        # Stocks in Focus quotes one line per company: keep its shortest complete bullet, as plain text.
        plain = [html.unescape(re.sub(r"<[^>]+>", "", b)) for b in p.bullets]
        lead = min((b for b in plain if len(b) >= 25), key=len, default=plain[0] if plain else None)
        for iid in p.ids:
            m = p.meta.get(iid, {})
            src = m.get("source", "UNKNOWN")
            if m.get("event_fp"):
                self.state.mark_seen(iid, src, fingerprint=m["event_fp"])
            self.state.mark_seen(iid, src, fingerprint=m.get("post_fp") or post_fp)
            if m.get("company"):
                self.state.record_cooldown(m["company"], m.get("category", ""))
        m = p.meta.get(primary, {})
        self.state.record_published(primary, m.get("headline") or re.sub(r"<[^>]+>", "", p.header),
                                    m.get("category", p.event), market_quote_event=m.get("market_quote_event"),
                                    result_event=m.get("result_event"), ts=now.isoformat(),
                                    company=p.company if m.get("source") in ("BSE", "NSE", "NSE_DEALS") else None,
                                    event=p.event.split("|")[0], lead=lead)
        if not self.dry_run:
            self.state.complete_delivery(primary)
        self._trace("dispatched", {"id": primary}, merged=len(p.ids))
        return 1

    # ------------------------------------------------------------ scheduled posts
    def check_morning_pack(self, now):
        slots = morning_pack.due_slots(now, self.morning_done)
        if not slots:
            return
        from sources.market_cues import fetch_cues
        cues = fetch_cues() if any(s != "stocks_in_focus" for s in slots) else {}
        for slot in slots[:1]:                       # one scheduled post per cycle, paced like the outbox
            text = morning_pack.build(slot, now, cues, self.state.published_records)
            if not text:
                self.morning_done[slot] = now.date().isoformat()
                continue
            if self._paced_send(text, f"MORNING_{slot}_{now.date()}", now):
                self.morning_done[slot] = now.date().isoformat()
                self.state.record_published(f"MORNING_{slot}_{now.date()}", slot, "MORNING_PACK", ts=now.isoformat())

    def _paced_send(self, text, key, now):
        """Scheduled posts share the outbox's 20 s gap and never go out in quiet hours."""
        from src.scheduler import in_active_window
        if not in_active_window(now):
            return False
        last = self.outbox.last_sent
        if last and (now - last).total_seconds() < self.outbox.min_gap:
            return False
        ok = self._send(text, key)
        if ok:
            self.outbox.last_sent = now
        return ok

    def check_ipo_listings(self, now):
        """Listing-day post once the opening price prints (10:00–10:45 IST, weekdays)."""
        if now.weekday() >= 5 or not (now.hour == 10 and now.minute <= 45):
            return
        if getattr(self, "_ipo_checked", 0) > time.time() - 120:
            return
        self._ipo_checked = time.time()
        from src.ipo_evidence import parse_past_issues, listing_open, listing_post
        try:
            issues = [i for i in parse_past_issues(self.nse.fetch_past_issues()) if i["listing_date"] == now.date()]
        except Exception as e:
            logger.warning("IPO listing check failed: %s", e)
            return
        for issue in issues:
            if issue["symbol"] in self.ipo_done:
                continue
            try:
                chart = _side_session(self.nse).get(
                    f"https://query1.finance.yahoo.com/v8/finance/chart/{issue['symbol']}.NS?range=5d&interval=1d",
                    timeout=15).json()
            except Exception:
                continue
            price = listing_open(chart, issue["listing_date"])
            if price and self._paced_send(listing_post(issue, price), f"IPO_{issue['symbol']}", now):
                self.ipo_done.add(issue["symbol"])
            elif price:
                self._ipo_checked = 0                    # held by the pacing gap: retry next cycle
                return

    def run_daemon(self):
        """Run one shift. The workflow persists state before dispatching its successor."""
        logger.info("Starting daemon: duration=%ss dry_run=%s", self.shift_seconds, self.dry_run)
        try:
            while time.time() - self.start_time < self.shift_seconds:
                try:
                    count = self.process_cycle()
                    if count:
                        logger.info("Cycle %s: dispatched %s items", self.cycle_count, count)
                except StateIntegrityError:
                    raise
                except Exception:
                    logger.exception("Error during daemon cycle %s", self.cycle_count)
                    self.total_errors += 1
                self.state.save_state(sync_git=False)
                remaining = self.shift_seconds - (time.time() - self.start_time)
                if remaining > 0:
                    time.sleep(min(self.poll_interval, remaining))
        finally:
            if getattr(self, "_source_pool", None):
                self._source_pool.shutdown(wait=True, cancel_futures=True)
                # Completed end-of-shift requests must survive the next runner.
                # Only checkpoint here; shutdown never starts another delivery.
                self.state.stage_items(self.poll_all_sources(submit_sources=False))
            if getattr(self, "_pdf_pool", None):
                self._pdf_pool.shutdown(wait=True, cancel_futures=True)
            self.state.save_state(sync_git=False)
        logger.info("Daemon finished. Cycles: %s, Dispatched: %s, Errors: %s",
                    self.cycle_count, self.total_dispatched, self.total_errors)
        if self.total_errors:
            raise RuntimeError("Daemon encountered errors; inspect the run log.")


def main():
    parser = argparse.ArgumentParser(description="Pahal Market Wire Production Daemon")
    parser.add_argument("--once", action="store_true", help="Run a single polling cycle and exit")
    parser.add_argument("--dry-run", action="store_true", help="Evaluate and format without publishing to Telegram")
    parser.add_argument("--duration", type=int, default=3240, help="Shift duration in seconds (default: 54 min = 3240s)")
    args = parser.parse_args()

    daemon = RealtimeDaemon(dry_run=args.dry_run, shift_seconds=args.duration)
    if args.once:
        logger.info("Executing single pass (--once)...")
        count = daemon.process_cycle(wait_sources=True)
        daemon.state.save_state(sync_git=False)
        logger.info(f"Single pass complete. Dispatched {count} announcements.")
        if daemon.total_errors:
            raise RuntimeError("Single pass had errors; inspect the run log.")
    else:
        daemon.run_daemon()


if __name__ == "__main__":
    main()




