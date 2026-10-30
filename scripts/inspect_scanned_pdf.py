"""Usage: python scripts/inspect_scanned_pdf.py INPUT.pdf --output REVIEW.json
Local review only; never imports the Telegram dispatcher or consumes dedup state.
Requires optional system executable tesseract with English data.
"""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sources.ocr_review import inspect_scans, MAX_BYTES


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pdf')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    source, output = Path(args.pdf), Path(args.output)
    if source.resolve() == output.resolve():
        parser.error('Output must not overwrite the input PDF')
    with source.open('rb') as stream:
        data = stream.read(MAX_BYTES+1)
    report = inspect_scans(data)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"OCR inspection saved; {len(report['pages'])} pages inspected. Review only; nothing published.")

if __name__ == '__main__':
    main()
