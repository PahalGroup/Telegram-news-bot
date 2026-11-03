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
