from curl_cffi import requests
from typing import List, Dict, Any
from html import unescape
from datetime import datetime
from zoneinfo import ZoneInfo
import re
import logging

logger = logging.getLogger(__name__)

class LiveBlogFetcher:
    """Fetches high-frequency, real-time market flashes & stock deals from Moneycontrol Liveblog."""
    def __init__(self):
        self.session = requests.Session(impersonate='chrome120')
        self.cached_blog_url = None
        self.cached_blog_date = None

    @staticmethod
    def is_fresh(value, now):
        try:
            stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=ZoneInfo("Asia/Kolkata"))
            return -120 <= (now - stamp).total_seconds() <= 2700
        except (ValueError, TypeError):
            return False

    def get_todays_liveblog_url(self) -> str:
        today_str = datetime.now(ZoneInfo('Asia/Kolkata')).strftime('%d-%m-%Y')
        if self.cached_blog_url and self.cached_blog_date == today_str:
            return self.cached_blog_url

        try:
            r = self.session.get('https://www.moneycontrol.com/news/business/markets/', timeout=10)
            if r.status_code != 200:
                raise RuntimeError(f"Liveblog discovery HTTP {r.status_code}")
            links = re.findall(r'https://www.moneycontrol.com/news/business/markets/[^"\'\s<>]+-liveblog-[^"\'\s<>]+\.html', r.text)
            today = datetime.now(ZoneInfo("Asia/Kolkata"))
            date_tokens = [today_str, today.strftime("%B-%d-%Y").lower(), today.strftime("%Y-%m-%d")]
            for l in links:
                if any(token in l.lower() for token in date_tokens):
                    self.cached_blog_url = l
                    self.cached_blog_date = today_str
                    return l
        except Exception as e:
            raise RuntimeError("Liveblog discovery failed") from e
        return ""

    def fetch_live_flashes(self) -> List[Dict[str, Any]]:
        url = self.get_todays_liveblog_url()
        if not url:
            return []

        items = []
        now_ist = datetime.now(ZoneInfo('Asia/Kolkata'))
        today_ymd = now_ist.strftime('%Y%m%d')
        try:
            r = self.session.get(url, timeout=10)
            if r.status_code != 200:
                raise RuntimeError(f"Liveblog HTTP {r.status_code}")

            # 1. First Priority: Structured JSON-LD
            import json
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(r.text, 'html.parser')
            for script in soup.find_all('script', type='application/ld+json'):
                try:
                    data = json.loads(script.string or '{}')
                    updates = data.get('liveBlogUpdate', [])
                    if isinstance(updates, list) and updates:
                        for u in updates:
                            headline = unescape(u.get('headline', '')).strip()
                            body = unescape(u.get('articleBody', '')).strip()
                            date_pub = u.get('datePublished', '')

                            if not headline:
                                continue

                            if not self.is_fresh(date_pub, now_ist):
                                continue

                            # Strip prefix tags
                            clean_hl = re.sub(r'^(?:Nifty50 Today|Sensex Today|Sensex, Nifty Today|Markets@\d+|Stock alert|Stock Market Live)\s*\|\s*', '', headline, flags=re.I)
                            clean_hl = re.sub(r'^Stock alert:\s*', '', clean_hl, flags=re.I)

                            items.append({
                                "source": "Moneycontrol Live",
                                "title": clean_hl.strip(),
                                "description": body,
                                "time": date_pub,
                                "id": f"mc_json_{date_pub}_{clean_hl}",
                                "link": url
                            })
                        if items:
                            return items
                except Exception:
                    pass

            # 2. Fallback to HTML regex parsing with dynamic date pattern
            entries = re.split(rf'id="{today_ymd}(\d{{6}})"', r.text)

            for i in range(1, len(entries), 2):
                raw_time = entries[i]
                block = entries[i+1][:15000]
                time_fmt = f"{raw_time[:2]}:{raw_time[2:4]}:{raw_time[4:6]}"
                if not self.is_fresh(now_ist.date().isoformat() + "T" + time_fmt, now_ist):
                    continue
                
                paras = re.findall(r'<p[^>]*>(.*?)</p>', block, re.DOTALL)
                clean_paras = []
                for p in paras:
                    cp = re.sub(r'<[^>]+>', '', p).strip()
                    cp = unescape(cp)
                    if len(cp) > 15 and not cp.startswith('October') and not cp.startswith('-') and 'http' not in cp[:10]:
                        clean_paras.append(cp)

                if clean_paras:
                    title = clean_paras[0]
                    body = clean_paras[1] if len(clean_paras) > 1 else ""
                    
                    clean_title = re.sub(r'^(?:Nifty50 Today|Sensex Today|Sensex, Nifty Today|Markets@\d+|Stock alert|Stock Market Live)\s*\|\s*', '', title, flags=re.I)
                    clean_title = re.sub(r'^Stock alert:\s*', '', clean_title, flags=re.I)
                    
                    items.append({
                        "source": "Moneycontrol Live",
                        "title": clean_title.strip(),
                        "description": body,
                        "time": time_fmt,
                        "id": f"mc_live_{today_ymd}_{raw_time}",
                        "link": url
                    })
        except Exception as e:
            raise RuntimeError("Liveblog fetch failed") from e
        return items

