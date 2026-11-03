"""IPO summaries require a complete named event, not a title or form label."""
import re
from src.quality import clean, sentences
from src.disclosure_evidence import response

BOILERPLATE = re.compile(
    r'corporate identity number|registered office|name of the target entity|'
    r'details in brief|dear sir|kindly take|pursuant to|'
    r'acts as the investment manager', re.I)
IPO = re.compile(r'\bIPO\b|\binitial public offer(?:ing)?\b', re.I)
ACTION = re.compile(
    r'\b(?:completed|launched|opened|closed|approved|filed|allotted|'
    r'listed|commenced|sold|received approval)\b', re.I)
NAMED_ENTITY = re.compile(r'\b[A-Z][A-Za-z0-9&(),.“”" -]{2,140}\b(?:Limited|Ltd\.?)\b')
FRAGMENT_END = re.compile(
    r'\b(?:a|an|the|wholly|partly|of|to|in|for|and|with|its|by|from)\s*[.,;:]?$', re.I)


def evaluate_ipo(company, title, description, category='CORP_RESTRUCTURE'):
    points = []
    for line in description.splitlines():
        for raw in sentences(line):
            point = clean(raw).lstrip('•▪- ')
            if not 35 <= len(point) <= 700:
                continue
            if (BOILERPLATE.search(point) or FRAGMENT_END.search(point)
                    or point.count('(') != point.count(')')
                    or re.search(r'\b(?:not|never|if|may|might)\b', point, re.I)):
                continue
            # A source headline cannot supply a missing issuer or event to an
            # anonymous allotment sentence. Keep the named event verbatim.
            if IPO.search(point) and ACTION.search(point) and NAMED_ENTITY.search(point):
                if point.casefold() not in {p.casefold() for p in points}:
                    points.append(point)
    return response(category, company, 'IPO UPDATE', points[:5],
                    'IPO_REQUIRES_COMPLETE_NAMED_EVENT')


# PDF table detection can overlap the cover-letter text above the actual table.
# Recover only this complete, explicit sale grammar from one blank-line-delimited
# paragraph in one full-width text block. Never join separate cells or pages.
SALE_PARAGRAPH = re.compile(
    r'^As part of the IPO,\s+[A-Z][A-Za-z0-9&(),.“”" -]{2,160}'
    r'\bLimited\s*\([^)]{1,30}\),?\s+a wholly owned subsidiary of the Company '
    r'has sold [\d,]+ equity shares by way of Offer for Sale, at the '
    r'price of (?:Rs\.?|INR|₹)\s*[\d,]+(?:\.\d+)? per share\.$')


def recover_ipo_sale_paragraphs(blocks, page_width):
    result = []
    for block in blocks:
        if len(block) < 5 or block[2] - block[0] < page_width * 0.55:
            continue
        for paragraph in re.split(r'\n\s*\n', str(block[4])):
            point = clean(paragraph)
            if SALE_PARAGRAPH.fullmatch(point) and point not in result:
                result.append(point)
    return result


# ---------------------------------------------------------------------------
# Listing-day and allotment posts (template: "{Company} Ltd – IPO LISTING").
# Issue price comes from NSE's past-issues list; the listing (open) price from the
# exchange quote on the listing date. Allotment links point to the registrar's
# own public status page, never a third-party aggregator.
# ---------------------------------------------------------------------------
from datetime import datetime as _dt, timezone as _tz, timedelta as _td

REGISTRARS = {
    "kfin": ("KFin Technologies", "https://ipostatus.kfintech.com/"),
    "link intime": ("MUFG Intime (Link Intime)", "https://in.mpms.mufg.com/Initial_Offer/public-issues.html"),
    "mufg": ("MUFG Intime (Link Intime)", "https://in.mpms.mufg.com/Initial_Offer/public-issues.html"),
    "bigshare": ("Bigshare Services", "https://ipo.bigshareonline.com/IPO_Status.html"),
    "skyline": ("Skyline Financial Services", "https://www.skylinerta.com/ipo.php"),
    "cameo": ("Cameo Corporate Services", "https://ipostatus1.cameoindia.com/"),
    "purva": ("Purva Sharegistry", "https://www.purvashare.com/investor-service/ipo-query"),
    "maashitla": ("Maashitla Securities", "https://maashitla.com/allotment-status/public-issues"),
}
_IST = _tz(_td(hours=5, minutes=30))


def parse_past_issues(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        try:
            price = float(str(r.get("issuePrice", "")).strip().replace(",", ""))
            listed = _dt.strptime(r["listingDate"].strip(), "%d-%b-%Y").date()
        except (ValueError, KeyError, AttributeError):
            continue
        out.append({"company": r["company"].strip(), "symbol": r["symbol"].strip(),
                    "issue_price": price, "listing_date": listed, "series": r.get("securityType", "EQ")})
    return out


def listing_open(chart: dict, listing_date) -> float | None:
    """Open price on the listing date from a Yahoo chart payload."""
    try:
        res = chart["chart"]["result"][0]
        for ts, o in zip(res["timestamp"], res["indicators"]["quote"][0]["open"]):
            if o is not None and _dt.fromtimestamp(ts, _IST).date() == listing_date:
                return float(o)
    except (KeyError, IndexError, TypeError):
        pass
    return None


def registrar_link(text: str):
    low = (text or "").lower()
    for key, val in REGISTRARS.items():
        if key in low:
            return val
    return None


def _title(name: str) -> str:
    name = re.sub(r"\s+(?:Limited|Ltd\.?)$", "", name.strip(), flags=re.I)
    return f"{name} Ltd"


def listing_post(issue: dict, open_price: float, close_price: float | None = None) -> str:
    import html as _h
    prem = (open_price - issue["issue_price"]) / issue["issue_price"] * 100
    word = "premium" if prem > 0 else "discount" if prem < 0 else "flat"
    arrow = "↑" if prem > 0 else "↓" if prem < 0 else "→"
    lines = [f"<b>{_h.escape(_title(issue['company']))} – IPO LISTING</b>", "",
             f"• Listed at ₹{open_price:,.2f} vs issue price ₹{issue['issue_price']:,.0f}",
             f"• Listing {word}: {arrow} {abs(prem):.1f}%"]
    if close_price:
        c = (close_price - issue["issue_price"]) / issue["issue_price"] * 100
        lines.append(f"• Closed day one at ₹{close_price:,.2f} ({'↑' if c >= 0 else '↓'} {abs(c):.1f}% vs issue)")
    lines.append(f"• Listing date: {issue['listing_date']:%d %b %Y}")
    return "\n".join(lines)


def allotment_post(company: str, registrar_text: str, date_text: str | None = None) -> str | None:
    import html as _h
    reg = registrar_link(registrar_text)
    if not reg:
        return None
    lines = [f"<b>{_h.escape(_title(company))} – IPO ALLOTMENT</b>", ""]
    if date_text:
        lines.append(f"• Basis of allotment finalised on {_h.escape(date_text)}")
    lines.append(f"• Registrar: {reg[0]}")
    lines.append(f'• Check status: <a href="{reg[1]}">{reg[1]}</a>')
    return "\n".join(lines)
