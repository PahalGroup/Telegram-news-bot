# One-day OCR shadow run (prepared, not started)

This is a plan only. Do not start it until Pahal confirms. Live `market-wire.yml` stays untouched. The 20-minute cutoff is unchanged. No posting, no backlog dump.

## Purpose
Run the scanned-results OCR path for one IST market day in **test mode** beside the live shift, log extracted figures and check outcomes, and compare them to the PDFs — without sending anything to Telegram.

## Preconditions
- PR `feat/results-ocr-tesseract` is on a branch, not merged, unless Pahal has approved a merge.
- Tesseract is installed locally (`tesseract --version`).
- `MARKET_WIRE_OCR_TEST=1` is set. `--ocr-test` implies `--dry-run` and never posts.
- No filings are sent to any AI service that trains on data.

## How to start (when approved)
1. Check out the OCR branch in a **separate** working copy, not the live runner.
2. `MARKET_WIRE_OCR_TEST=1 python -m src.daemon --ocr-test --duration 3240` at the usual 07:00 / 13:00 / 19:00 IST shift starts, or a single `--once` cycle first.
3. Collect `[OCR TEST]` log lines (company, period, scope, page, revenue, PAT, or the withhold reason).
4. Spot-check at least 20 passing cards against the statement page of the PDF. Any mismatch is a withhold, not a post.
5. Stop at 23:30 IST with the live window. Do not start a second concurrent live shift.

## What not to do
- Do not enable a scheduled GitHub Actions workflow for this.
- Do not point the shadow runner at `TELEGRAM_CHANNEL_ID`.
- Do not merge this PR as part of starting the shadow run.
