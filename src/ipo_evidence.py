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
