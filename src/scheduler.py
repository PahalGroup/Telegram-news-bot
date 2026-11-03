"""When posts may go out: active window, freshness, pacing and same-company merge."""
import time as _time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from typing import Callable, List, Optional

IST = timezone(timedelta(hours=5, minutes=30))
ACTIVE_START = time(7, 0)
ACTIVE_END = time(23, 0)
MAX_AGE = timedelta(minutes=30)
MAX_AGE_SLOW = timedelta(hours=3)        # results and concall transcripts
SLOW_EVENTS = {"RESULTS", "EARNINGS CALL"}
MIN_GAP_SECONDS = 20
MERGE_WINDOW = timedelta(minutes=30)


def ist(dt: datetime) -> datetime:
    return (dt if dt.tzinfo else dt.replace(tzinfo=IST)).astimezone(IST)


def in_active_window(now: datetime) -> bool:
    t = ist(now).time()
    return ACTIVE_START <= t < ACTIVE_END


def fresh_enough(published: Optional[datetime], discovered: datetime, event_label: str) -> bool:
    if published is None:
        return False
    limit = MAX_AGE_SLOW if event_label in SLOW_EVENTS else MAX_AGE
    return ist(discovered) - ist(published) <= limit


@dataclass
class Pending:
    key: str
    company: str
    event: str
    header: str
    bullets: List[str]
    discovered: datetime
    published: Optional[datetime] = None
    tail: str = ""                      # e.g. source link
    ids: List[str] = field(default_factory=list)

    def text(self):
        body = [self.header] + [f"• {b}" for b in self.bullets]
        if self.tail:
            body.append(self.tail)
        return "\n".join(body)


class Outbox:
    """FIFO queue that releases at most one post every MIN_GAP_SECONDS.

    A filing from a company that already has a queued post of the same event
    type, or that had one sent within MERGE_WINDOW, is folded into it (queued)
    or dropped when it adds no new bullet (sent). Different event types post
    separately.
    """

    def __init__(self, min_gap=MIN_GAP_SECONDS, max_bullets=6):
        self.min_gap = min_gap
        self.max_bullets = max_bullets
        self.queue: "OrderedDict[str, Pending]" = OrderedDict()
        self.recent = {}               # (company, event) -> (sent_at, bullets)
        self.last_sent = None

    @staticmethod
    def merge_key(company, event):
        return f"{company.casefold().strip()}|{event}"

    def put(self, p: Pending) -> str:
        k = self.merge_key(p.company, p.event)
        p.ids = p.ids or [p.key]
        if k in self.queue:
            q = self.queue[k]
            for b in p.bullets:
                if b.casefold() not in {x.casefold() for x in q.bullets} and len(q.bullets) < self.max_bullets:
                    q.bullets.append(b)
            q.ids.extend(p.ids)
            return "MERGED_QUEUED"
        sent = self.recent.get(k)
        if sent and p.discovered - sent[0] <= MERGE_WINDOW:
            new = [b for b in p.bullets if b.casefold() not in sent[1]]
            if not new:
                return "MERGED_ALREADY_SENT"
            # Same event within the window: only the new facts, once.
            p.bullets = new
        self.queue[k] = p
        return "QUEUED"

    def due(self, now: datetime) -> Optional[Pending]:
        """Pop the oldest post if pacing and the active window allow it."""
        if not self.queue or not in_active_window(now):
            return None
        if self.last_sent and (now - self.last_sent).total_seconds() < self.min_gap:
            return None
        k, p = next(iter(self.queue.items()))
        del self.queue[k]
        self.last_sent = now
        self.recent[k] = (now, {b.casefold() for b in p.bullets})
        return p

    def drop_overnight(self, now: datetime) -> List[Pending]:
        """At the end of the active window stale queued items are discarded."""
        if in_active_window(now):
            return []
        stale = [p for p in self.queue.values() if not fresh_enough(p.published, now, p.event)]
        for p in stale:
            self.queue.pop(self.merge_key(p.company, p.event), None)
        return stale
