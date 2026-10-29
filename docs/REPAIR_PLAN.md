# Market wire repair and release plan

Status: draft repair branch; production has not been migrated. Each stage requires evidence, not just a passing unit test. No private channel exports belong in this repository.

| Order | Work | Acceptance evidence | Status |
| --- | --- | --- | --- |
| 1 | Source health and freshness: BSE, NSE, RBI, RSS, liveblog | HTTP/schema errors visible; empty/stale feeds distinguished; timezone and missing-date tests; compare live source items with normalized output | In progress |
| 2 | Complete event summaries | Typed required fields for orders, financing, management, results and policy; original evidence attached to fields; no invented units or facts; review original-document cases | In progress: initial-sentence truncation remains |
| 3 | PDF tables and scans | Period, unit, standalone/consolidated scope and sign retained; ambiguous tables withheld with reason; OCR evaluated on real scan fixtures | In progress; explicit tables and geometry-backed merged headers; OCR review only |
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

## 29 October implementation checkpoint

- GitHub host probe from the preceding revision returned BSE 143, NSE 20, RBI 4 and RSS 3 items. Liveblog remained empty/unverified. Local HTTP 403 did not establish a production-host outage.
- Replaced order/financing/management fallback summaries with verbatim evidence gates. Orders require an award/receipt action and value or capacity. Financing requires action plus amount. Management requires an explicitly titled person's name, role and action. These conservative rules can withhold valid disclosures phrased differently; broader recall validation remains open.
- Removed automatic rupee conversion and inferred management-action headings from those paths. A tender submission alone does not qualify as an order win.
- Added bounded financial-table support: one explicit scope and unit, independently labelled period columns, recognized metric rows and unchanged numeric signs/parentheses. Merged headers, mixed scopes, scans and unlabelled tables remain unsupported. No financial arithmetic is inferred.
- Kept prose in disclosure-table cells separate from numeric tables. Replayed and visually reviewed the original Powerica disclosure to ensure its useful prose survives table detection.
- 52 offline tests pass, including table-to-PDF integration tests. This is limited table support, not completion of financial extraction or OCR. No production deployment and no Telegram messages.

- GitHub installed PyMuPDF 2.0 under the open version range; both table integration tests failed there. Pinned PyMuPDF 1.26.6, the local tested extractor, for reproducible behavior.


## Merged-header and scan checkpoint

- Merged period headers now use PDF cell rectangles to assign explicit labels to columns. Missing cells are never filled from neighboring text alone. Multiple dates/kinds in a column are rejected.
- Tests cover quarter/year groups, missing span geometry, conflicting dates and a generated PDF with a merged header.
- Added a local OCR inspection command (`python scripts/inspect_scanned_pdf.py input.pdf --output review.json`). It records page numbers, word confidence and failures; requires optional Tesseract English data. Output is explicitly non-publishable and never enters Telegram or dedup state.
- OCR is bounded to 20 MB, five pages, eight million pixels per page, twenty seconds per subprocess and a sixty-second processing budget. Recognition confidence is not a guarantee of accurate figures.
- 60 local regression tests pass. A rasterized version of an original disclosure is used as a smoke test, not as proof of real-world scan accuracy. Broad original-scan and financial-table validation remains outstanding; automatic publication of OCR output remains disabled.
