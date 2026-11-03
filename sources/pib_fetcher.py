"""PIB press releases (English) for the finance/economy ministries.

PIB's RSS feed is Hindi-only and undated, so the English 'all releases' page
is read instead. Releases are grouped under ministry headings; only the
ministries that move markets are kept.
"""
import html
import re
from typing import Dict, List

from curl_cffi import requests

URL = "https://pib.gov.in/allRel.aspx?reg=3&lang=1"
MARKET_MINISTRIES = re.compile(
    r"finance|commerce|cabinet|prime minister|petroleum|power|coal|steel|road transport|railways|"
    r"heavy industries|new and renewable|civil aviation|niti aayog|statistics|corporate affairs|"
    r"mines|telecommunications|communications|electronics|chemicals|fertilizers|defence", re.I)
BLOCK = re.compile(r"<h3[^>]*>\s*(?P<ministry>[^<]+?)\s*</h3>(?P<body>.*?)</ul>", re.S)
LINK = re.compile(r"<a[^>]*href='(?P<href>/PressReleaseDetail\.aspx\?PRID=(?P<prid>\d+))'[^>]*>(?P<title>[^<]+)</a>")


def parse_releases(page: str) -> List[Dict[str, str]]:
    out = []
    for block in BLOCK.finditer(page):
        ministry = html.unescape(block["ministry"]).strip()
        if not MARKET_MINISTRIES.search(ministry):
            continue
        for link in LINK.finditer(block["body"]):
            title = re.sub(r"\s+", " ", html.unescape(link["title"])).strip()
            out.append({"id": f"PIB_{link['prid']}", "ministry": ministry, "title": title,
                        "link": "https://pib.gov.in" + link["href"], "source": "PIB"})
    return out


class PIBFetcher:
    def __init__(self, session=None):
        self.session = session or requests.Session(impersonate="chrome120")

    def fetch_releases(self) -> List[Dict[str, str]]:
        r = self.session.get(URL, timeout=15)
        if r.status_code != 200:
            raise RuntimeError(f"PIB HTTP {r.status_code}")
        items = parse_releases(r.text)
        if not items and "PRID=" not in r.text:
            raise RuntimeError("PIB page layout changed: no releases found")
        return items
