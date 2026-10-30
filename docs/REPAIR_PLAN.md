# Market wire repair and release plan

Status: draft repair branch; production has not been migrated. Each stage requires evidence, not just a passing unit test. No private channel exports belong in this repository.

| Order | Work | Acceptance evidence | Status |
| --- | --- | --- | --- |
| 1 | Source health and freshness: BSE, NSE, RBI, RSS, liveblog | HTTP/schema errors visible; empty/stale feeds distinguished; timezone and missing-date tests; compare live source items with normalized output | In progress |
| 2 | Complete event summaries | Typed required fields for orders, financing, management, results and policy; original evidence attached to fields; no invented units or facts; review original-document cases | In progress: premature evidence limits repaired; broader completeness validation remains |
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


## Adversarial review, 29 October 2026

The earlier four passing filing examples were insufficient release evidence. New negative tests at c79f33b produced 13 failing assertions across nine test methods, while the original four filings still passed.

Confirmed problems and repairs:
- Corrupt SQLite/JSON and malformed snapshot schemas could be silently ignored. Startup now stops; failed ledger writes propagate; an unhealthy ledger cannot overwrite its snapshot.
- PDF facts were cut to twelve and then six entries before event selection. Extraction now retains evidence within the existing forty-page bound. Financial-result filtering selects relevant metrics before applying the post limit.
- Distant negation and uncertain commissioning statements could pass event checks. Checks now account for event qualification, while distinguishing reference numbers and later delivery terms. Conservative mixed-clause rejection still affects recall.
- Auction formatting could convert an explicit million amount into crore. Auction evidence and broker target currencies now preserve the source wording instead of inventing units.
- One-shot runs queued PDFs and exited without draining their results. They now wait for bounded PDF completion and report errors.
- NSE, RBI and RSS errors could look like empty feeds. They now expose failures; RSS preserves healthy-source items during partial outages. BSE preserves successfully fetched pages when another page fails.
- RSS missing/invalid/future dates are withheld. RBI omits timezone offsets in its actual feed, so that adapter explicitly uses Asia/Kolkata for naive timestamps. BSE likewise uses India time only for naive timestamps and preserves explicit offsets.
- Two original management disclosures exposed line-per-block PDF layout failures. Full-width, adjacent aligned blocks are joined using PDF coordinates; column, blank, numbered-item and paragraph-gap boundaries are retained. Effective dates and tenure survive the reviewed examples.
- Signature-only company-secretary text no longer establishes a management event. Took/taken-over-charge wording and Smt. abbreviations are supported without relabelling the action as an appointment.
- A real no-delivery smoke run exposed PDF download failures hidden from the daemon error total. Download/parse errors now propagate and remain retryable; unsupported evidence is counted separately. The download timeout is thirty seconds and BSE's official archive is tried only after a live-attachment 404.

Validation boundaries:
- The original corpus now contains six pinned documents; selected facts and category checks do not establish completeness or general factual accuracy.
- The first ninety-second live-source smoke prepared seventeen posts and made zero Telegram attempts, but logged two PDF timeouts hidden by the old accounting. Its green status was not proof of error-free operation.
- Source probes have alternated between success and degraded results: BSE page errors and Moneycontrol RSS outages occurred. Such errors must remain visible; rerunning until green does not erase them.
- No full market-session comparison has been completed. No live Telegram acknowledgement, crash-after-send reconciliation or production-state remote-persistence failure drill has been completed.
- Telegram request exceptions now stop automatic resend. A delivery intent is saved before sending; unresolved intents block restart until manual reconciliation. Definitive rejection clears the intent and permits retry; acknowledgement clears it only after the published ledger is saved. Git-backed state can still be lost if an ephemeral runner disappears before upload/push; this remains a release blocker, not an exactly-once guarantee.
- Live speech capture, PIB/SEBI integrations, broad original-scan/table verification and a hosting plan satisfying the intended latency remain open. OCR remains review-only.
- Production has not been changed. Do not describe this draft as a completed rollout or lifetime-free real-time service.


### Delivery reconciliation

Run `python scripts/reconcile_delivery.py` to inspect pending IDs and fingerprints. Verify the actual destination channel before resolving any item. If delivered, use `--item-id ID --outcome delivered --message-id VERIFIED_ID --confirm-reviewed`; if verified absent, use `--item-id ID --outcome not-delivered --confirm-reviewed`. Optional `--db` and `--snapshot` select restored recovery files. The command does not contact Telegram or send a message. Preserve and commit both updated state files before restarting an ephemeral runner. If the outcome is still unknown, leave the intent unresolved.

## Shadow-output review, 30 October 2026

Reading the actual 26 prepared posts exposed errors that successful test counts and extraction totals concealed. Auditor scope paragraphs described subsidiary figures as results; chart labels and historic presentation milestones appeared as current results. A dividend-tax notice became a memorandum-of-understanding post because `mou` matched `amount`. Routine SEBI references became macro news, and rating rationales became earnings stories because their background mentioned EBITDA.

Targeted regressions reproduced nine failing assertions before repair. Result selection now requires an explicit numeric value/unit, excludes recognized audit-scope/background text and selected clipped fragments, and keeps negative values verbatim. Dividend-tax paperwork is rejected, MOU matching uses word boundaries, and explicit credit-rating titles take precedence over background earnings. These are conservative safeguards, not complete period, scope or numerical reconciliation; valid stories can still be withheld.

The previous PDF logs also contained PyMuPDF text-page/weak-reference errors despite a zero-error aggregate. PyMuPDF explicitly does not support multithreaded use (https://pymupdf.readthedocs.io/en/latest/recipes-multiprocessing.html). PDF parsing now uses three spawned processes, with independent documents; source HTTP polling remains threaded. A real spawned-process test checks six distinct documents for cross-document fact contamination.

`python scripts/shadow_smoke.py --duration 90 --output-dir fresh-run-directory` now creates per-item JSONL evidence and a summary, alongside prepared posts. Repeated queue deferrals are counted separately from unique deferred items. Each item retains its first discovery, final pipeline status, rejection reason and discovery-to-ready delay. Initial source batches are marked warm-start; missing, ambiguous and future publication timestamps are excluded from publication-delay statistics. Ready means prepared, not delivered or accurate. Fatal cycle errors still write evidence. The rejecting Telegram stub and temporary delivery ledger remain in place.

The default CI run remains a 90-second smoke. Longer observation is configurable but has not established a full market-session benchmark or reference-channel win rate. Remaining issues include incomplete RBI rate/data notices, broader result-period/scope validation, source coverage, source outages, durable production-state recovery, and actual delivery validation. No production rollout is implied by this checkpoint.

The first instrumented run at ddf726a observed 49 distinct items: 13 ready, 31 rejected, one duplicate and four unsupported PDFs, with zero sends and no outstanding PDF queue. All thirteen ready items were warm-start items: there were zero fresh-item latency samples. Its roughly twelve-second discovery-to-ready figures must not be presented as publication-to-Telegram latency. The separate source probe failed on all four Moneycontrol RSS URLs, while this smoke observed no source error; the differing samples demonstrate intermittent access, not permanent recovery.

Reviewing those thirteen posts exposed additional guidance/history, unlabelled numeric rows and clipped phrases passing the result gate. A second regression batch reproduced nine failing assertions. Added exclusions for these cases while retaining explicitly labelled table facts. RBI floating-bond rate notices now require an explicit announced coupon sentence: the older generic selector discarded the sentence when it repeated the title and retained calculation background instead. Spread-only notices are withheld. Acquisition summaries still contain excessive disclosure boilerplate, data-release notices do not extract their linked tables, and period/scope reconciliation remains incomplete.

