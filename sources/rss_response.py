"""Classify bounded RSS responses without logging article bodies or session headers."""
import hashlib
import re
import xml.etree.ElementTree as ET

MAX_FEED_BYTES = 4 * 1024 * 1024


class FeedResponseError(RuntimeError):
    def __init__(self, reason, diagnostics):
        self.reason, self.diagnostics = reason, diagnostics
        super().__init__(reason)


def parse_rss(data, content_type='', status=200, final_url=''):
    details = {'http_status': status, 'content_type': str(content_type),
               'response_bytes': len(data), 'body_sha256': hashlib.sha256(data).hexdigest(),
               'final_url': final_url}
    if len(data) > MAX_FEED_BYTES:
        raise FeedResponseError('RSS_SIZE_LIMIT_EXCEEDED', details)
    prefix = data[:4096].lower()
    # Match a document root, not HTML embedded in an RSS item's CDATA.
    opening = prefix.lstrip(b'\xef\xbb\xbf \t\r\n')
    if re.match(br'(?:<\?xml\b.*?\?>\s*)?(?:<!--.*?-->\s*)*<(?:!doctype\s+html|html(?:\s|>))', opening, re.DOTALL):
        details['body_kind'] = 'html'
        reason = 'RSS_ACCESS_CHALLENGE' if any(marker in prefix for marker in
            (b'access denied', b'captcha', b'request rejected', b'permission denied')) else 'RSS_HTML_INSTEAD_OF_FEED'
        raise FeedResponseError(reason, details)
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        details['body_kind'] = 'malformed_xml'
        details['parse_position'] = exc.position
        raise FeedResponseError('RSS_MALFORMED_XML', details) from exc
    details['root_tag'] = root.tag
    if root.tag != 'rss' or root.find('channel') is None:
        raise FeedResponseError('RSS_UNEXPECTED_SCHEMA', details)
    return root, details
