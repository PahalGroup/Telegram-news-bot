"""Template C: 4–6 bullets from an earnings-call transcript.

Extractive and deterministic: management statements that carry a figure and
touch guidance, capex, margins, demand or order book are condensed (filler
trimmed, first person turned into the company). Analyst questions are skipped.
"""
import html
import re
from typing import Dict, List

THEMES = {
    "guidance": r"\bguidance\b|\bguide\b|\bexpect(?:s|ed|ing)?\b|\btarget(?:ing)?\b|\boutlook\b|\bgoing forward\b|\bnext (?:year|quarter)\b|\bFY\s?'?\d{2}\b|\baim\b",
    "capex": r"\bcapex\b|capital expenditure|\bcapacity\b|\bexpansion\b|\bnew plant\b|\bgreenfield\b|\bbrownfield\b|\bcommission",
    "margins": r"\bmargins?\b|\bEBITDA\b|\bprofitability\b|\bgross margin\b|\bbps\b|basis points",
    "growth": r"\bgrowth\b|\bgrew\b|\bAUM\b|\bNIM\b|credit cost|disburse|\brevenues?\b|\bpre-?sales\b|\bloan book\b|\bdeposits?\b|\bGNPA\b|\bROA\b|\bROE\b",
    "demand": r"\bdemand\b|\bvolumes?\b|\border (?:book|inflow|pipeline)\b|\bbookings?\b|\bpricing\b|\brealis",
}
FILLER = [
    (r"^(?:so|and|now|also|well|okay|yes|right|see|as I said|as mentioned(?: earlier)?|I think|we think|I would say|let me (?:tell|say)|"
     r"I would like to (?:highlight|mention|share|inform) that|we are (?:happy|pleased|glad) to (?:share|inform|report) that|"
     r"I am (?:happy|pleased) to (?:share|report) that|in terms of)[,\s]+", ""),
    (r"\b(?:you know|basically|actually|sort of|kind of|I mean)[,\s]+", ""),
    (r"\b[Ww]e've\b", "the company has"), (r"\b[Ww]e're\b", "the company is"), (r"\b[Ww]e'll\b", "the company will"),
    (r"\b[Ww]e are\b", "the company is"), (r"\b[Ww]e have\b", "the company has"), (r"\b[Ww]e will\b", "the company will"),
    (r"\b[Ww]e expect\b", "the company expects"), (r"\b[Ww]e\b", "the company"), (r"\b[Oo]ur\b", "its"), (r"\b[Uu]s\b", "the company"),
]
FIGURE = re.compile(r"\d+(?:\.\d+)?\s*(?:%|percent|bps|basis points|crores?|cr\b|lakh|million|billion|mn\b|bn\b|MW|GW|MTPA|tonnes|x\b)|₹\s*\d|Rs\.?\s*\d|INR\s*\d", re.I)
SPEAKER = re.compile(r"^\s*([A-Z][A-Za-z.'\- ]{2,40}):\s*(.*)")
ANALYST_HINT = re.compile(r"\bmoderator\b|\banalyst\b|\bresearch\b|\bsecurities\b|\bcapital\b|\bpartners\b|\binvestments?\b|\bfund\b|\bAMC\b|\bPMS\b", re.I)


PAGE_FURNITURE = re.compile(r"page\s+\d+\s+of\s+\d+|\d{4}\.\d{2}\.\d{2}\s+\d{2}:\d{2}|earnings conference call|"
                            r"^\s*(?:january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{1,2},\s+\d{4}\s*$", re.I)
ABBREV = re.compile(r"\b(Rs|No|Mr|Ms|Mrs|Dr|approx|vs|Co|Ltd|Inc|Sr|Jr|St)\.", re.I)


def speaker_turns(text: str) -> List[Dict[str, str]]:
    """Split a transcript into (speaker, speech) turns."""
    turns, cur = [], None
    for line in text.splitlines():
        if PAGE_FURNITURE.search(line):
            continue
        m = SPEAKER.match(line)
        if m and len(m.group(1).split()) <= 5:
            cur = {"speaker": m.group(1).strip(), "text": m.group(2)}
            turns.append(cur)
        elif cur is not None:
            cur["text"] += " " + line.strip()
    return turns


def management_names(turns) -> set:
    """Speakers introduced by the moderator as management, else long-answer speakers."""
    names = set()
    asked = set()
    for i, t in enumerate(turns):
        if t["speaker"].lower() == "moderator":
            m = re.search(r"(?:question|line of)\s+(?:is\s+)?from(?:\s+the line of)?\s+([A-Z][A-Za-z.\- ]{2,40}?)\s+(?:from|with|of)\b", t["text"])
            if m:
                asked.add(m.group(1).strip().split()[0].lower())
    for t in turns:
        first = t["speaker"].split()[0].lower()
        if t["speaker"].lower() != "moderator" and first not in asked and not ANALYST_HINT.search(t["speaker"]):
            names.add(t["speaker"])
    return names


LEAD_IN = re.compile(r"^(?:to conclude|in conclusion|for now|before I conclude|from a [\w ]+ perspective|overall|"
                     r"on the [\w ]+ front|coming to [^,:]{0,60}|turning to [^,:]{0,40}|let me [^,:]{0,80}|"
                     r"moving (?:on )?to [^,:]{0,40}|in Q\d|during the quarter)\s*[,:]\s*", re.I)


def condense(sentence: str) -> str:
    s = re.sub(r"\s+", " ", sentence).strip(" .,-–")
    for _ in range(2):
        s = LEAD_IN.sub("", s)
    s = re.sub(r"^I would (?:also )?like to ", "", s, flags=re.I)
    for _ in range(2):
        for pat, rep in FILLER:
            s = re.sub(pat, rep, s, flags=re.I)
    s = s.strip(" ,")
    s = re.sub(r"\bthe company (reiterate|remain|maintain|plan|see|believe|continue|target|expect|aim)\b",
               lambda m: "the company " + m.group(1) + "s", s)
    s = re.sub(r"^(revise|raise|reiterate|maintain|lower|cut)\b", lambda m: "The company " + m.group(1).rstrip("e") + "es"
               if m.group(1).endswith("e") else "The company " + m.group(1) + "s", s, flags=re.I)
    s = re.sub(r"\bTurning to the [A-Za-z]+:\s*", "", s)
    return s[:1].upper() + s[1:] if s else s


SKIP_SENTENCE = re.compile(r"\bjoining\b|\bwelcome\b|\bregulation\b|digitally signed|\btranscript\b|\bconference call\b|"
                           r"\byou\b|\byour\b|\bGDP\b|\brepo\b|\bRBI\b|\bslide\b|\bthank", re.I)


def readable(text: str) -> bool:
    """Two-column scans flatten into word fragments; such text is withheld."""
    lines = [l for l in text.splitlines() if l.strip()]
    if len(lines) < 30:
        return False
    short = sum(len(l.strip()) < 18 for l in lines)
    return short / len(lines) < 0.45


def summarise(text: str, max_points=6, min_points=4) -> List[str]:
    if not readable(text):
        return []
    turns = speaker_turns(text)
    # The exchange cover letter precedes the call; start at the moderator.
    first = next((i for i, t in enumerate(turns) if t["speaker"].lower() == "moderator"), None)
    if first is None:
        return []
    turns = turns[first:]
    mgmt = management_names(turns)
    # Anyone who mostly asks questions is an analyst, whatever the label.
    asks = {}
    for t in turns:
        asks.setdefault(t["speaker"], []).append("?" in t["text"])
    mgmt = {m for m in mgmt if sum(asks[m]) / len(asks[m]) < 0.34}
    candidates = []
    for order, t in enumerate(turns):
        if t["speaker"] not in mgmt:
            continue
        protected = ABBREV.sub(lambda m: m.group(1) + "\u2024", re.sub(r"\s+", " ", t["text"]))
        for sent in (x.replace("\u2024", ".") for x in re.split(r"(?<=[.!?])\s+", protected)):
            if not (40 <= len(sent) <= 240) or "?" in sent or not FIGURE.search(sent) or SKIP_SENTENCE.search(sent):
                continue
            themes = [k for k, p in THEMES.items() if re.search(p, sent, re.I)]
            if themes:
                candidates.append((order, themes, condense(sent)))
    chosen, covered, seen = [], set(), set()
    # One bullet per theme first, then the remaining strongest lines.
    for want in THEMES:
        for c in candidates:
            if want in c[1] and want not in covered and c[2].casefold() not in seen:
                chosen.append(c); covered.update(c[1]); seen.add(c[2].casefold()); break
    for c in candidates:
        if len(chosen) >= max_points:
            break
        if c[2].casefold() not in seen and len(c[1]) >= 2:
            chosen.append(c); seen.add(c[2].casefold())
    if len(chosen) < min_points:
        return []
    chosen.sort(key=lambda c: c[0])
    return [c[2] for c in chosen[:max_points]]


def format_concall(company: str, points: List[str], period: str = "", link: str = None) -> str:
    from src.editorial import header
    label = f"EARNINGS CALL {period}".strip()
    lines = [header(company, label)] + [f"• {html.escape(p.rstrip('.'))}" for p in points]
    if link:
        lines.append(f'<a href="{html.escape(link, quote=True)}">Source</a>')
    return "\n".join(lines)


def summarise_pdf(doc, **kw) -> List[str]:
    text = "\n".join(page.get_text() for page in doc)
    return summarise(text, **kw)
