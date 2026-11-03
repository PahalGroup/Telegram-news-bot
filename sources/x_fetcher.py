"""Posts from official handles via the X API v2 (needs X_BEARER_TOKEN).

Only accounts in OFFICIAL_HANDLES are read; replies and retweets are skipped.
Without a token the fetcher is disabled and returns [] (it never scrapes).
"""
import os
from typing import Dict, List

import requests

OFFICIAL_HANDLES = ("RBI", "FinMinIndia", "PIB_India", "NSEIndia", "BSEIndia", "SEBI_India")
API = "https://api.x.com/2"


def parse_timeline(handle: str, payload: dict) -> List[Dict[str, str]]:
    out = []
    for tweet in payload.get("data") or []:
        refs = {r.get("type") for r in tweet.get("referenced_tweets") or []}
        if refs & {"retweeted", "replied_to"}:
            continue
        out.append({"id": f"X_{tweet['id']}", "source": "X", "handle": handle,
                    "title": tweet.get("text", "").strip(), "published_at": tweet.get("created_at"),
                    "link": f"https://x.com/{handle}/status/{tweet['id']}"})
    return out


class XFetcher:
    def __init__(self, token=None, session=None):
        self.token = token or os.environ.get("X_BEARER_TOKEN", "")
        self.session = session or requests.Session()
        self._ids = {}

    @property
    def enabled(self):
        return bool(self.token)

    def _get(self, path, **params):
        r = self.session.get(API + path, params=params, timeout=10,
                             headers={"Authorization": f"Bearer {self.token}"})
        r.raise_for_status()
        return r.json()

    def fetch_recent(self, since_id=None) -> List[Dict[str, str]]:
        if not self.enabled:
            return []
        items = []
        for handle in OFFICIAL_HANDLES:
            if handle not in self._ids:
                self._ids[handle] = self._get(f"/users/by/username/{handle}")["data"]["id"]
            params = {"max_results": 10, "tweet.fields": "created_at,referenced_tweets"}
            if since_id:
                params["since_id"] = since_id
            items.extend(parse_timeline(handle, self._get(f"/users/{self._ids[handle]}/tweets", **params)))
        return items
