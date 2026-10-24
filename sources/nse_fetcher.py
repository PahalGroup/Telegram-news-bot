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
                if isinstance(data, list):
                    return data
            return []
        except Exception as e:
            logger.error(f"Error fetching NSE announcements: {e}")
            self._warm = False
            return []

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
                if isinstance(data, list):
                    return data
            return []
        except Exception as e:
            logger.error(f"Error fetching NSE FII/DII data: {e}")
            return []
