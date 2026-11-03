"""Global and pre-open market cues for the morning pack (Yahoo chart API + NSE market status)."""
from __future__ import annotations

import logging

logger = logging.getLogger("MarketCues")

YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=5d&interval=1d"

# label, yahoo symbol, unit prefix
SYMBOLS = {
    "S&P 500": "^GSPC", "Nasdaq": "^IXIC", "Dow Jones": "^DJI",
    "Nikkei": "^N225", "Hang Seng": "^HSI",
    "Brent crude": "BZ=F", "WTI crude": "CL=F", "Gold": "GC=F",
    "USD/INR": "INR=X", "India VIX": "^INDIAVIX", "Nifty 50": "^NSEI",
}


def parse_yahoo(payload: dict) -> dict | None:
    """Last close and day change from the closes array.

    chartPreviousClose is the close *before the 5-day window*, so it must not be
    used for the daily change; the two most recent non-null closes are used instead.
    """
    try:
        res = payload["chart"]["result"][0]
        closes = [c for c in res["indicators"]["quote"][0]["close"] if c is not None]
    except (KeyError, IndexError, TypeError):
        return None
    if len(closes) < 2 or not closes[-2]:
        return None
    last, prev = closes[-1], closes[-2]
    return {"last": round(last, 2), "change": round(last - prev, 2), "pct": round((last - prev) / prev * 100, 2)}


def parse_nse_status(payload: dict) -> dict:
    out = {}
    g = payload.get("giftnifty") or {}
    try:
        out["GIFT Nifty"] = {"last": float(g["LASTPRICE"]), "change": float(g["DAYCHANGE"]), "pct": float(g["PERCHANGE"])}
    except (KeyError, TypeError, ValueError):
        pass
    n = payload.get("indicativenifty50") or {}
    try:
        val = n.get("indexLast") or n.get("closingValue")
        pct = n.get("indexPercChange") if n.get("indexPercChange") is not None else n.get("perChange")
        out["Nifty indicative"] = {"last": float(val), "pct": float(pct), "status": n.get("status"),
                                   "time": n.get("indicativeTime") or n.get("dateTime")}
    except (TypeError, ValueError):
        pass
    return out


def fetch_cues(session=None) -> dict:
    from curl_cffi import requests
    s = session or requests.Session(impersonate="chrome120")
    out = {}
    for label, sym in SYMBOLS.items():
        try:
            q = parse_yahoo(s.get(YAHOO_URL.format(sym=sym), timeout=15).json())
            if q:
                out[label] = q
        except Exception as e:  # one bad symbol must not kill the pack
            logger.warning("yahoo %s failed: %s", sym, e)
    try:
        s.get("https://www.nseindia.com", timeout=15)
        out.update(parse_nse_status(s.get("https://www.nseindia.com/api/marketStatus",
                                          headers={"Referer": "https://www.nseindia.com/"}, timeout=15).json()))
    except Exception as e:
        logger.warning("nse market status failed: %s", e)
    return out
