"""Evidence-preserving quality checks; no model calls or invented financial facts."""
import hashlib
import html
import re

PLACEHOLDER = re.compile(
    r"please (?:refer|find)|self explanatory|verified from official|"
    r"press release$|^transaction details:?$|^board .* decisions:?$|"
    r"kindly take|enclosed (?:herewith|file)|dear sir|"
    r"has informed (?:bse|the exchange)|pursuant to regulation|"
    r"reason for change.*appointment|removal.*death|"
    r"monitoring agency report|dispatched via github|investorrelations@|"
    r"^CIN:|^name of the entity awarding|^significant terms and conditions|"
    r"^whether |^time period by which|^broad consideration or size", re.I
)
ACTION = re.compile(
    r"\b(?:approved|approves|awarded|award|received|receives|secured|secures|"
    r"acquir\w*|acquis\w*|resign\w*|appoint\w*|commission\w*|invest\w*|"
    r"revenue|net profit|net loss|profit after tax|ebitda|dividend|"
    r"assumed charge|loan|financ\w*|capacity|contract|agreement|order|rating|"
    r"repo|auction|policy|tariff|tax|duty|stake|merger|buyback)\b", re.I
)

def clean(value):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", str(value or "")))).strip()

def fingerprint(company, text):
    # Preserve decimals, negative signs and the full claim; no daily clock or category collision.
    value = clean(company + "\n" + text).casefold()
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

def substantive(text):
    value = clean(text).lstrip("•-▪►* \ufe0f")
    if not 25 <= len(value) <= 700 or PLACEHOLDER.search(value):
        return False
    if re.search(r"^(?:that |of |from |and |or |/|t &|re-)", value, re.I):
        return False
    if re.search(r"(?:\b(?:and|or|with|from|for|to|of|the|its|their|other)|[,;:/(])\s*$", value, re.I):
        return False
    return bool(ACTION.search(value))

def clean_points(points, headline=""):
    result, seen = [], {clean(headline).casefold()}
    for point in points:
        value = clean(point).lstrip("•-▪►* \ufe0f")
        key = value.casefold().rstrip(".")
        if substantive(value) and key not in seen:
            result.append(value)
            seen.add(key)
    return result[:6]

def sentences(text):
    # Protect common abbreviations and decimals before identifying sentence boundaries.
    text = clean(text)
    # Strip only a recognized introductory clause; retain the factual body verbatim.
    text = re.sub(r"^(?:Pursuant to|In continuation of|With reference to).*?this is to inform you that\\s+", "", text, flags=re.I)
    protected = re.sub(r"\b(Mr|Mrs|Ms|Dr|Rs|Ltd|Ref|No)\.", r"\1<dot>", text)
    return [part.replace("<dot>", ".").strip() for part in
            re.split(r"(?<=[.!?])\s+(?=[A-Z])", protected) if part.strip()]

