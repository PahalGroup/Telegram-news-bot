"""Bounded PDF transport; retries are for transient failures, never access bypass."""
import time
from urllib.parse import urlparse
from curl_cffi import requests
from curl_cffi.requests.exceptions import RequestException


class PDFDownloadError(RuntimeError):
    def __init__(self, reason, attempts, retryable=False):
        self.reason, self.attempts, self.retryable = reason, attempts, retryable
        super().__init__(f'{reason}; attempts={attempts}')


def download_pdf(url, client=None, max_bytes=20*1024*1024, budget_seconds=45,
                 attempt_seconds=25, clock=time.monotonic, sleep=time.sleep):
    parsed = urlparse(url)
    if parsed.scheme != 'https' or not parsed.hostname:
        raise PDFDownloadError('PDF_URL_NOT_HTTPS', 0)
    owned = client is None
    client = client or requests.Session()
    started = clock()
    attempts, transient_retries = 0, 0
    current = url
    reason = 'PDF_DOWNLOAD_BUDGET_EXHAUSTED'
    try:
        while attempts < 3:
            remaining = budget_seconds - (clock() - started)
            if remaining <= 0:
                break
            attempts += 1
            buffer = bytearray()
            oversized = False
            def collect(chunk):
                nonlocal oversized
                if len(buffer) + len(chunk) > max_bytes:
                    oversized = True
                    return 0  # libcurl aborts; never buffer the excess bytes.
                buffer.extend(chunk)
                return len(chunk)
            retryable = False
            delay = 1.0
            try:
                response = client.get(current, timeout=min(attempt_seconds, remaining),
                    headers={'User-Agent': 'Mozilla/5.0', 'Accept-Encoding': 'identity'},
                    content_callback=collect)
                status = response.status_code
                if oversized:
                    raise PDFDownloadError('PDF_SIZE_LIMIT_EXCEEDED', attempts)
                if status == 404 and current == url and parsed.hostname == 'www.bseindia.com' and parsed.path.startswith('/xml-data/corpfiling/AttachLive/'):
                    current = url.replace('/AttachLive/', '/AttachHis/', 1)
                    continue
                if status in (429, 500, 502, 503, 504):
                    reason, retryable = f'PDF_HTTP_{status}', True
                    retry_after = response.headers.get('Retry-After', '')
                    if isinstance(retry_after, str) and retry_after.isdigit():
                        delay = max(1.0, float(retry_after))
                    elif retry_after:
                        # Do not retry earlier than an uninterpreted server date.
                        raise PDFDownloadError(reason, attempts, True)
                elif status != 200:
                    raise PDFDownloadError(f'PDF_HTTP_{status}', attempts)
                elif not bytes(buffer).lstrip().startswith(b'%PDF-'):
                    raise PDFDownloadError('PDF_INVALID_CONTENT', attempts)
                elif b'%%EOF' not in buffer[-2048:]:
                    reason, retryable = 'PDF_INCOMPLETE_CONTENT', True
                else:
                    return bytes(buffer)
            except RequestException as exc:
                if oversized:
                    raise PDFDownloadError('PDF_SIZE_LIMIT_EXCEEDED', attempts) from exc
                # Explicit curl network/partial-transfer failures only. TLS and
                # access errors do not gain automatic retries or changed clients.
                code = getattr(exc, 'code', None)
                reason = f'PDF_TRANSPORT_{code}'
                retryable = code in (7, 18, 28, 52, 55, 56)
                if not retryable:
                    raise PDFDownloadError(reason, attempts) from exc
            remaining = budget_seconds - (clock() - started)
            if not retryable or transient_retries >= 1 or delay >= remaining:
                raise PDFDownloadError(reason, attempts, retryable)
            transient_retries += 1
            sleep(delay)
        raise PDFDownloadError(reason, attempts, True)
    finally:
        if owned:
            client.close()
