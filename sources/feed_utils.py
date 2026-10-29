"""Shared strict publication-time handling for RSS sources."""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

def fresh_rss_date(value, max_age_seconds, now=None):
    if not value:
        return False
    try:
        published = parsedate_to_datetime(value)
        if published.tzinfo is None:
            return False
        age = ((now or datetime.now(timezone.utc)) - published).total_seconds()
        return -120 <= age <= max_age_seconds
    except (TypeError, ValueError, OverflowError):
        return False
