# Market wire repair and release plan

Status: draft repair branch; production has not been migrated. Each stage requires evidence, not just a passing unit test. No private channel exports belong in this repository.

| Order | Work | Acceptance evidence | Status |
| --- | --- | --- | --- |
| 1 | Source health and freshness: BSE, NSE, RBI, RSS, liveblog | HTTP/schema errors visible; empty/stale feeds distinguished; timezone and missing-date tests; compare live source items with normalized output | In progress |
| 2 | Complete event summaries | Typed required fields for orders, financing, management, results and policy; original evidence attached to fields; no invented units or facts; review original-document cases | In progress: initial-sentence truncation remains |
| 3 | PDF tables and scans | Period, unit, standalone/consolidated scope and sign retained; ambiguous tables withheld with reason; OCR evaluated on real scan fixtures | Pending; conservative prose extraction available |
| 4 | Delivery and restart recovery | Dedup ledger survives restart; failures retry; distinct updates pass; state-write failure is visible; cross-source duplicate evaluation | Basic regression tests pass; restart/live validation pending |
| 5 | Official event coverage | Verify accessible official RBI/PIB/SEBI feeds and licensing/availability; distinguish published text from live speech; document actual coverage | Pending |
| 6 | Market-session shadow validation | No Telegram sends; source health, discovery, extraction and ready timestamps; adjudicated errors and omissions; p50/p95 processing delay, source outages, backlog | Short smoke checks only; full session pending |
| 7 | Release and rollback | All critical factual/delivery defects resolved; explicit known coverage gaps; compare production configuration; deploy pinned revision and preserve prior revision/state | Not deployed |
| 8 | Maintenance | Persistent health summary, failure reasons, bounded retries and stale-source detection; operational checks after source changes | Pending |

## Measurement rules

- Source publication time, first discovery, ready-to-post time and Telegram acknowledgement are different measurements.
- A reference-channel text match is not proof of original source availability or total coverage.
- Empty sources are not automatically healthy. Unsupported PDFs are not processed successes.
- No scheduled platform can be represented as guaranteeing perpetual zero-cost, instantaneous delivery.
- Existing 37 offline regressions and GitHub checks passed before this plan. Re-run relevant tests after each substantive change.

## Current source diagnosis

Track exact HTTP status and parser outcome before adding alternative endpoints. Do not bypass access controls. A source unavailable from this runtime must be checked from the intended host before claiming it works in production.

## Work completed in this iteration

- BSE and Moneycontrol discovery both returned HTTP 403 from the local runtime. Previously both could silently appear empty. HTTP/schema failures now propagate to daemon error accounting.
- Added a read-only GitHub source probe with metadata-only artifact output; no Telegram credentials or messages. Host connectivity still needs its result reviewed.
- Liveblog freshness checks retain timezone offsets, reject missing/invalid/future dates, and also cover the HTML fallback.
- SGB redemption notices require the source's actual price and unit; legal background alone is withheld.
- 43 offline tests pass locally. This does not complete stages 1 or 2; broader source health and typed event coverage remain pending.
