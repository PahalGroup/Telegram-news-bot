"""Bounded local OCR for review. Output is never publication evidence.

Tesseract is optional. Confidence is a recognition score, not factual verification.
No credentials, external service, or Telegram code are used.
"""
import csv
import io
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
import pymupdf

MAX_PAGES = 5
MAX_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 8_000_000
MAX_SECONDS = 60


def parse_tsv(tsv):
    lines = {}
    confidences = []
    for row in csv.DictReader(io.StringIO(tsv), delimiter='\t'):
        text = (row.get('text') or '').strip()
        if not text:
            continue
        try:
            confidence = float(row['conf'])
            if confidence < 0:
                continue
            key = tuple(row[k] for k in ('block_num', 'par_num', 'line_num'))
        except (ValueError, KeyError):
            continue
        lines.setdefault(key, []).append(text)
        confidences.append(confidence)
    return {
        'text': '\n'.join(' '.join(words) for words in lines.values()),
        'word_count': len(confidences),
        'mean_confidence': round(sum(confidences)/len(confidences), 2) if confidences else None,
        'low_confidence_words': sum(c < 80 for c in confidences),
    }


def inspect_scans(pdf_bytes):
    if len(pdf_bytes) > MAX_BYTES:
        raise ValueError('PDF exceeds OCR size limit')
    report = {'publishable': False, 'status': 'review_only', 'pages': [],
              'note': 'OCR confidence is not evidence of correct figures or table alignment.'}
    engine = shutil.which('tesseract')
    started = time.monotonic()
    with pymupdf.open(stream=pdf_bytes, filetype='pdf') as doc, tempfile.TemporaryDirectory(prefix='wire-ocr-') as work:
        report['total_pages'] = len(doc)
        report['pages_not_inspected'] = max(0, len(doc)-MAX_PAGES)
        for index in range(min(MAX_PAGES, len(doc))):
            page = doc[index]
            item = {'page': index+1, 'publishable': False}
            if len(page.get_text().strip()) >= 100:
                item['status'] = 'text_layer_present'
            elif engine is None:
                item['status'] = 'ocr_unavailable'
            elif time.monotonic()-started >= MAX_SECONDS:
                item['status'] = 'time_budget_exceeded'
            else:
                scale = min(2.5, (MAX_PIXELS / max(page.rect.width*page.rect.height, 1))**0.5)
                path = Path(work)/f'page-{index+1}.png'
                page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False).save(str(path))
                try:
                    remaining = max(0.1, MAX_SECONDS-(time.monotonic()-started))
                    result = subprocess.run([engine, str(path), 'stdout', '-l', 'eng', '--psm', '3', 'tsv'],
                                            capture_output=True, text=True, timeout=min(20, remaining), check=True)
                    item.update(parse_tsv(result.stdout))
                    item['status'] = 'needs_review' if item['word_count'] else 'no_text_recognized'
                except subprocess.TimeoutExpired:
                    item['status'] = 'ocr_timeout'
                except subprocess.CalledProcessError:
                    item['status'] = 'ocr_failed'
            report['pages'].append(item)
    return report
