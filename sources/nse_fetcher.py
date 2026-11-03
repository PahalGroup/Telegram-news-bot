from curl_cffi import requests
from typing import List, Dict, Any
import logging

logger = logging.getLogger(__name__)

class NSEFetcher:
    def __init__(self):
        self.session = requests.Session(impersonate="chrome120")
        self._warm = False

    def _warmup(self):
        try:
            self.session.get("https://www.nseindia.com", timeout=10)
            self._warm = True
        except Exception as e:
            logger.warning(f"NSE warmup failed: {e}")
            self._warm = False

    def fetch_announcements(self) -> List[Dict[str, Any]]:
        """Fetch latest corporate announcements from NSE."""
        if not self._warm:
            self._warmup()

        headers = {
            "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-announcements",
            "Accept": "application/json, text/plain, */*",
        }
        url = "https://www.nseindia.com/api/corporate-announcements?index=equities"
        try:
            r = self.session.get(url, headers=headers, timeout=12)
            if r.status_code == 403 or r.status_code == 401:
                # refresh session
                self._warmup()
                r = self.session.get(url, headers=headers, timeout=12)
            
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list) and all(isinstance(row, dict) for row in data):
                    return data
            raise RuntimeError(f"NSE announcements invalid HTTP/schema: {r.status_code}")
        except Exception as e:
            self._warm = False
            raise RuntimeError("NSE announcements request failed") from e

    def fetch_fii_dii(self) -> List[Dict[str, Any]]:
        """Fetch latest FII / DII net trade data from NSE."""
        if not self._warm:
            self._warmup()

        headers = {
            "Referer": "https://www.nseindia.com/reports/fii-dii",
            "Accept": "application/json, text/plain, */*",
        }
        url = "https://www.nseindia.com/api/fiidiiTradeReact"
        try:
            r = self.session.get(url, headers=headers, timeout=10)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list) and all(isinstance(row, dict) for row in data):
                    return data
            raise RuntimeError(f"NSE FII/DII invalid HTTP/schema: {r.status_code}")
        except Exception as e:
            raise RuntimeError("NSE FII/DII request failed") from e


DEAL_MIN_CR = 50.0


def large_deals(snapshot, min_cr=DEAL_MIN_CR):
    """Block and bulk deals worth at least `min_cr` crore from NSE's large-deal snapshot.

    Value = quantity x weighted average trade price. Deals for the same
    symbol, client and side are summed (a client often trades in pieces).
    """
    out = {}
    for kind, key in (("BLOCK DEAL", "BLOCK_DEALS_DATA"), ("BULK DEAL", "BULK_DEALS_DATA")):
        for row in snapshot.get(key) or []:
            try:
                qty = float(str(row["qty"]).replace(",", ""))
                price = float(str(row["watp"]).replace(",", ""))
            except (KeyError, ValueError):
                continue
            k = (kind, row.get("symbol"), row.get("clientName"), row.get("buySell"))
            d = out.setdefault(k, {"kind": kind, "symbol": row.get("symbol"), "company": row.get("name"),
                                   "client": row.get("clientName"), "side": row.get("buySell"),
                                   "date": row.get("date"), "qty": 0.0, "value": 0.0})
            d["qty"] += qty
            d["value"] += qty * price
    # NSE lists a block-window trade in both tables; report it once, as the block deal.
    for (kind, sym, client, side), d in list(out.items()):
        twin = out.get(("BLOCK DEAL", sym, client, side))
        if kind == "BULK DEAL" and twin and abs(twin["qty"] - d["qty"]) < 1:
            del out[(kind, sym, client, side)]
    deals = []
    for d in out.values():
        d["value_cr"] = d["value"] / 1e7
        d["price"] = d["value"] / d["qty"] if d["qty"] else 0.0
        if d["value_cr"] >= min_cr:
            deals.append(d)
    return sorted(deals, key=lambda d: -d["value_cr"])


def _side_session(self):
    """Own session for deals/IPO calls: they run in a different pool thread from announcements."""
    if getattr(self, "_side", None) is None:
        self._side = requests.Session(impersonate="chrome120")
        self._side.get("https://www.nseindia.com", timeout=10)
    return self._side


def _fetch_large_deals(self):
    r = _side_session(self).get("https://www.nseindia.com/api/snapshot-capital-market-largedeal",
                                headers={"Referer": "https://www.nseindia.com/market-data/large-deals"}, timeout=12)
    if r.status_code != 200:
        raise RuntimeError(f"NSE large deals HTTP {r.status_code}")
    return large_deals(r.json())


def _fetch_past_issues(self):
    r = _side_session(self).get("https://www.nseindia.com/api/public-past-issues",
                                headers={"Referer": "https://www.nseindia.com/market-data/all-upcoming-issues-ipo"}, timeout=12)
    if r.status_code != 200:
        raise RuntimeError(f"NSE past issues HTTP {r.status_code}")
    return r.json()


NSEFetcher.fetch_large_deals = _fetch_large_deals
NSEFetcher.fetch_past_issues = _fetch_past_issues
