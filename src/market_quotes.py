"""Narrow, source-based crude-price flashes; not general semantic news matching."""
import math
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo
from src.pipeline_observer import publication_time

BENCHMARKS = {
    'Brent crude': r'\bBrent(?:\s+(?:crude|oil|futures))*\b',
    'WTI crude': r'\b(?:(?:U\.?S\.?\s+)?West Texas Intermediate|WTI)(?:\s+(?:crude|oil|futures))*\b',
}
PRICE = r'(?P<currency>US\$|USD|\$)\s*(?P<price>\d+(?:\.\d+)?)\s+(?:per|a)\s+barrel\b'
UNSAFE_CONTEXT = re.compile(r'\b(?:yesterday|previous|last|settled|closing|closed|target|forecast|'
    r'expects?|expected|could|would|may|might|estimate|January|February|March|April|May|June|'
    r'July|August|September|October|November|December)\b', re.I)
BRIDGE = re.compile(r'^(?:\s|[,:%.$+\-\d]|fell|falls|slipped|slips|rose|rises|dropped|drops|'
                    r'gained|gains|climbed|climbs|declined|declines|was|were|is|at|to|by|or|'
                    r'traded|trading|stood|down|up|more|than|nearly|about|per|a|barrel|USD|US\$)+$', re.I)


def is_quote_story(candidate):
    title = candidate.get('title', '')
    return (candidate.get('source') in ('NEWS', 'LIVEBLOG')
            and not candidate.get('company')
            and bool(re.search(r'\b(?:oil|crude|brent|wti)\b', title, re.I))
            and bool(re.search(r'\b(?:prices?|slips?|falls?|rises?|gains?|drops?|climbs?|declines?)\b',
                               title, re.I)))


def extract_quotes(description):
    quotes = {}
    # Some feeds omit the space between sentences. Never split a decimal.
    clauses = re.split(r'(?<=[.!?])\s*(?=[A-Z])', description)
    for clause in clauses:
        for benchmark, pattern in BENCHMARKS.items():
            for asset in re.finditer(pattern, clause, re.I):
                tail = clause[asset.end():]
                match = None
                for possible in re.finditer(PRICE, tail, re.I):
                    bridge = tail[:possible.start()]
                    context = clause[:asset.start()] + bridge
                    # "fell BY $1.59 per barrel" is a change, not the quote.
                    absolute = (re.search(r'\b(?:at|to)\s*$', bridge, re.I)
                                or re.fullmatch(r'\s*:?\s*', bridge))
                    if (absolute and len(bridge) <= 100 and not UNSAFE_CONTEXT.search(context)
                            and BRIDGE.fullmatch(bridge)):
                        match = possible
                        break
                if match is None:
                    continue
                # Do not infer the currency or a unit from a nearby unrelated quote.
                try:
                    price = format(Decimal(match['price']).normalize(), 'f')
                except InvalidOperation:
                    continue
                if Decimal(price) <= 0:
                    continue
                currency = match['currency'].upper()
                key = (benchmark, currency)
                quote = {'benchmark': benchmark, 'currency': currency, 'price': price,
                         'text': f'{benchmark}: {currency}{price} per barrel.'}
                if key in quotes and quotes[key] != quote:
                    quotes[key] = None  # conflicting prices within one source: withhold
                elif key not in quotes:
                    quotes[key] = quote
    return [quote for quote in quotes.values() if quote]


def validate_event(event):
    if not isinstance(event, dict):
        raise ValueError('Invalid market quote event')
    epoch = event.get('publication_epoch')
    if (not isinstance(epoch, (float, int)) or isinstance(epoch, bool)
            or not math.isfinite(epoch) or epoch <= 0):
        raise ValueError('Invalid market quote time')
    quotes = event.get('quotes')
    if not isinstance(quotes, list) or not quotes:
        raise ValueError('Invalid market quote list')
    for quote in quotes:
        if (not isinstance(quote, dict)
                or quote.get('benchmark') not in BENCHMARKS
                or quote.get('currency') not in ('$', 'US$', 'USD')
                or not isinstance(quote.get('price'), str)
                or not re.fullmatch(r'\d+(?:\.\d+)?', quote['price'])
                or Decimal(quote['price']) <= 0
                or not isinstance(quote.get('text'), str)):
            raise ValueError('Invalid market quote')


def select_new_quotes(quotes, item, records):
    """Compare with the latest acknowledged quote, so a price reversion survives.

    Only source timestamps on the same India calendar date are comparable.
    A new benchmark or price is retained; an older quote is not a live update.
    """
    published = publication_time(item.get('published_at'), item.get('source'))
    if published is None:
        return quotes, None
    epoch = published.timestamp()
    day = published.astimezone(ZoneInfo('Asia/Kolkata')).date()
    selected = []
    for quote in quotes:
        prior = None
        for record in reversed(records):
            event = record.get('market_quote_event')
            if not event:
                continue
            validate_event(event)
            previous_day = datetime.fromtimestamp(event['publication_epoch'], ZoneInfo('Asia/Kolkata')).date()
            if previous_day != day:
                continue
            match = next((q for q in event['quotes'] if
                          (q['benchmark'], q['currency']) == (quote['benchmark'], quote['currency'])), None)
            if match is not None:
                prior = (event['publication_epoch'], match)
                break
        if prior and (prior[0] > epoch or prior[1]['price'] == quote['price']):
            continue
        selected.append(quote)
    event = {'publication_epoch': epoch, 'quotes': selected} if selected else None
    return selected, event
