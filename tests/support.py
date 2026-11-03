"""Test clock for daemon tests that replay fixed historical timestamps."""
from datetime import datetime, timedelta

from src.scheduler import IST, parse_time


class FollowClock:
    """'Now' is one minute after the newest item just polled, plus 30 s per call.

    Fixture items carry fixed timestamps; this keeps them inside the freshness
    window and spaces successive cycles beyond the 20 s posting gap.
    """

    def __init__(self, daemon, default=datetime(2026, 11, 2, 13, 0, tzinfo=IST)):
        self.daemon, self.default, self.ticks = daemon, default, 0

    def __call__(self):
        self.ticks += 1
        times = [parse_time(i.get("published_at")) for i in getattr(self.daemon, "_last_raw", []) or []
                 if isinstance(i, dict) and i.get("published_at")]
        times = [t for t in times if t]
        base = max(times) + timedelta(minutes=1) if times else self.default
        return base + timedelta(seconds=30 * self.ticks)


def install(daemon):
    daemon.clock = FollowClock(daemon)
    return daemon
