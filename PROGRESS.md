# Channel-quality upgrade — progress notes (local only, never pushed)

Branch: feat/channel-quality-upgrade (base: PR #1 fix/market-wire-reliability-20261026)

## Done (committed)
- bbe2150 results scorecard (template A)
- 53c423d editorial gate, outbox, concall (C), deals (E), PIB/RBI/X, morning pack, IPO
- c228d4a daemon wiring, worker.py, Actions = tests+probes only, legacy root modules removed, replay harness
- b14d7b4 bullet hygiene, period-aware results label, tighter PIB/RBI filters, stale deal snapshot guard
- 7d21e8b PDF glyph repair (₹ as X/%/z, 02→Q2, ti ligatures)
- 2201a29 rating-grade regex bug (lower-case a/b/d counted as figures), pre-open ignores yesterday's close,
  macro posts need a fact, Stocks in Focus escaping, IPO listing session fix
- Tests: 304 passed, 97 subtests. Probes: all sources OK, x_official disabled (no token).

## Dry run
`python scripts/dry_run_replay.py --ann /workspace/dry/bse_ann.json --pdf-cache /workspace/dry/pdf --snapshots /workspace/dry/snapshots.json --start 2026-11-02T06:00 --end 2026-11-04T09:30 --out /workspace/dry/replayN`
(~36 min). Replay 1 (pre-fix): 216 posts, median 17.9 s, p90 23.9 s, 0 procedural, 0 visible junk, 0 posts 23:00–07:00, min gap 24 s.
Replay 4 = current code (in progress).

## Remaining
- Read replay4 metrics; 15 samples; 20-post manual number check; final report.
- REPORT.md written; all units done.
- Scheduler moved to GitHub Actions (market-wire.yml, 3 chained shifts, 15 s polling, cache-persisted state); worker.py removed.

## 2026-11-03 follow-ups (PR #5, not merged)
- PR: https://github.com/PahalGroup/Telegram-news-bot/pull/5 (branch fix/smoke-and-mgmt)
- Smoke check exit 1 with 0 errors: blocked PDFs (terminal, held for review) were counted in pending_pdf_backlog, so status=incomplete. Fix: open_backlog() excludes _pdf_blocked keys. On PR #5 the smoke is still red, but now for a real reason: errors=2 / degraded, at the same time probes show an RBI RSS TimeoutError.
- ION Exchange 22:11 IST Change in Management (re-designation of Mr. Rajesh Sharma): its PDF mentions "postal ballot", so FilterEngine.should_drop marked it BLACK_LISTED_BOILERPLATE. Also, "change in designation" wasn't an ACTION/MANAGEMENT_ACTION term. Fix: exempt re-designation from the drop, and add the terms. Dry replay now produces "Ion Exchange India Ltd – MANAGEMENT CHANGE". 330 tests pass.
- Live run 41677351580: failed at 23:00 IST with "hosted runner lost communication" during Run shift. State was not saved. Only 2 posts (5932, 5933 at 22:15 IST).
