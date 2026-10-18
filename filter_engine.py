import re
from typing import Tuple, Optional

# DROP PATTERNS (Filings & News to drop immediately)
DROP_PATTERNS = [
    # Shareholding patterns
    r'\bshareholding\s+pattern\b',
    r'\breg(?:ulation)?\.?\s*31\b',
    
    # Trading window closures / openings
    r'\btrading\s+window\b',
    r'\bclosure\s+of\s+trading\s+window\b',
    r'\bpit\s+regulations?\b',
    
    # Lost / duplicate certificates
    r'\blost\s+certificate\b',
    r'\bloss\s+of\s+(?:share\s+)?certificate\b',
    r'\bduplicate\s+(?:share\s+)?certificate\b',
    r'\breg(?:ulation)?\.?\s*39\s*\(\s*3\s*\)',
    
    # Routine depository / compliance certificates & SAST disclosures
    r'certificate\s+under\s+(?:reg(?:ulation)?\.?\s*)?74\s*\(\s*5\s*\)',
    r'depositories\s+and\s+participants',
    r'certificate\s+under\s+(?:reg(?:ulation)?\.?\s*)?7\s*\(\s*3\s*\)',
    r'certificate\s+under\s+(?:reg(?:ulation)?\.?\s*)?40\s*\(\s*9\s*\)',
    r'compliance\s+certificate\s+under\s+regulation',
    r'disclosure\s+under\s+regulation\s*29\s*\(\s*[12]\s*\)',
    r'regulation\s*29\s*\(\s*[12]\s*\)\s*of\s*sebi',
    r'\bsast\s+regulations?\b',
    r'please\s+find\s+(?:the\s+)?(?:attach(?:ed)?|enclosed|the\s+same|letter)',
    r'please\s+refer\s+(?:the\s+)?(?:detailed\s+)?(?:disclosure|enclosed|file|attachment)',
    r'refer\s+(?:the\s+)?(?:enclosed\s+file|detailed\s+disclosure)',
    r'self\s+explanatory',
    r'covering\s+letter',
    r'forwarded\s+herewith',
    r'we\s+submit\s+herewith',
    r'copy\s+of\s+letter\s+submitted',
    
    # Newspaper publications, media reports & investor meets transcripts
    r'copy\s+of\s+newspaper\s+publication',
    r'newspaper\s+clipping',
    r'newspaper\s+advertisement',
    r'newspaper\s+publication',
    r'news\s+article\s+published',
    r'clarification\s+on\s+news\s+item',
    r'media\s+clarification',
    r'transcript\s+of\s+(?:analyst|investor|earnings)\s+call',
    r'audio\s+recording\s+of\s+(?:analyst|investor|earnings)\s+call',
    r'investor\s+presentation\b',
    r'analyst\s+meet\s+presentation',
    r'notice\s+of\s+(?:the\s+)?(?:eogm|egm|agm)\b',
    r'\beogm\b',
    
    # Routine board meeting date intimations (scheduling paperwork, NOT results)
    r'\bboard\s+meeting\s+intimation\b',
    r'\bintimation\s+of\s+board\s+meeting\b',
    r'\bupdate\s+on\s+board\s+meeting\b',
    r'\bintimation\s+under\s+regulation\s*33\b',
    r'\breason\s+for\s+del?a?y\b',
    r'\bis\s+scheduled\s+to\s+be\s+held\b',
    r'\bis\s+scheduled\s+on\b',
    r'\bmeeting\s+is\s+scheduled\b',
    r'\bboard\s+meeting\s+is\s+scheduled\b',
    
    # Routine compliance reports, approvals & administrative filings
    r'\btrading\s+approval\b',
    r'\blisting\s+approval\b',
    r'\bapproval\s+for\s+listing\b',
    r'\blisting\s+of\s+equity\s+shares\b',
    r'\bapportionment\s+of\s+cost\s+of\s+acquisition\b',
    r'\bstatus\s+of\s+investors?\s+complaints\b',
    r'\binvestor\s+(?:grievance|complaints?)\b',
    r'\bregulation\s*13\s*\(\s*3\s*\)',
    r'\bstatement\s+of\s+deviation\b',
    r'\breconciliation\s+of\s+share\s+capital\b',
    r'\bcorporate\s+governance\b',
    r'\bnotice\s+of\s+(?:the\s+)?(?:equity\s+)?shareholders?\s+meeting\b',
    r'\bnclt\s+(?:convened\s+)?meeting\b',
    r'\breg(?:ulation)?\.?\s*26e\b',
    r'\breg(?:ulation)?\.?\s*27\s*\(\s*2\s*\)',
    r'\bunit\s+holding\s+pattern\b',
    r'\bunitholding\s+pattern\b',
    r'\bcommercial\s+paper\b',
    r'\bnon-applicability\b',
    r'\bregulation\s*23\s*\(\s*9\s*\)',
    r'\breg\b\.?\s*23\s*\(\s*9\s*\)',
    r'\bcompliances-certificate\s+under\s+reg\b',
    r'\bcompliances-certificate\s+under\s+reg\.\s*74\s*\(\s*5\s*\)',
    r'dear\s+sir[,\s]+please\s+find\s+(?:the\s+)?attachment',

    # Con-call / Earnings call scheduling notices (scheduling paperwork, NOT results)
    r'\bintimation\s+of\s+(?:schedule\s+of\s+)?(?:earnings|investor|analyst)\s+call\b',
    r'\bschedule\s+of\s+(?:earnings|investor|analyst)\s+(?:call|meet)\b',
    r'\bearnings\s+call\s+for\s+q\d\b',
    r'\bcon-?call\b',

    # Company Secretary & Compliance Officer turnover (Mid-level staff, NOT C-suite)
    r'\bcompany\s+secretary\b',
    r'\bcompliance\s+officer\b',
    r'\bcs\s+and\s+compliance\s+officer\b',

    # Investor relations advisors & consultants
    r'\binvestor\s+relations\s+(?:consultant|agency|advisor)\b',

    # Routine MCA administrative name changes
    r'\bchange\s+of\s+name\s+by\s+mca\b',

    # Voting results, scrutinizers & postal ballots (Administrative paperwork)
    r'\bscrutinizer\'?s?\s+report\b',
    r'\bvoting\s+results?\b',
    r'\bpostal\s+ballot\b',
    r'\bdisclosure\s+of\s+material\s+issue\b',
    r'\bin-principle\s+approval\b',

    # ESOP / ESOS / ESPS allotments (Routine employee grants, NOT public market actions)
    r'\ballotment\s+of\s+(?:equity\s+shares\s+under\s+)?(?:esop|esps|stock\s+options?)\b',
    r'\b(?:esop|esos|esps|sweat\s+equity)\b',
    r'\bcompanies\s+esop\s+plan\b',
    r'\ballotment\s+of\s+esop\b',

    # Spurt in volume clarifications / exchange queries
    r'\bspurt\s+in\s+volume\b',
    r'\bclarification\s+(?:on|sought|by\s+the\s+company)\b',

    # Algorithmic retail screener cards & consumer sales fluff
    r'\bvolume\s+(?:gainer|shocker|spike|surge)\b',
    r'\bhigh\s+trading\s+volume\s+stock\b',
    r'\bshares\s+ended\s+(?:lower|higher)\s+in\s+closing\s+session\b',
    r'\btrading\s+volume\s+stock\b',
    r'\bblockbuster\s+sale\b',
    r'\bday\s+\d+\s+orders?\b',
    r'\bfestive\s+sale\b',

    # Retail analyst bylines, hourly intraday widgets & wrap commentary
    r'\b(?:hourly\s+update|sectoral\s+indices|technical\s+pick|taking\s+stock)\b',
    r'\b(?:research\s+analyst|head\s+of\s+research|technical\s+&\s+derivatives|religare\s+broking|sbi\s+securities|geojit|bonanza)\b',
    r'\btop\s+(?:gainers|losers)\s+among\b',
    r'\bstocks\s+among\s+top\s+(?:gainers|losers)\s+in\s+today\'?s?\s+trade\b',
    r'\banalysts?/institutional investor meet/con\.?\s*call updates?\b',
    r'\bcon\.?\s*call updates?\b',
    r'\bvehicle loan agreement\b',
    r'^[A-Z][a-z]+ [A-Z][a-z]+,\s*(?:Principal Analyst|Strategist|Head|Director)',
    r'\bconstituents\s+register\s+notable\b',
    r'\b(?:bears|bulls)\s+back\s+in\s+action\b',
    r'\bmarket\s+close\s*\|\s*sensex\b',
    r'\bopening\s+bell\b',

    # Editorial questions, retail fluff & advice
    r'\?\s*$',
    r'\btime\s+to\s+(?:buy|sell|exit|enter)\b',
    r'\bahead\s+of\s+earnings\b',
    r'\bhyper\s*market\b',
    r'\bgold\s+rates?\s+(?:today|in\s+india)\b',
    r'\bgold\s+prices?\s+(?:down|up|fall)\s+in\s+[a-z]+,\s*[a-z]+\b',
    r'\bhere\'s\s+why\b',
    r'\bwarning\s+signs\b',
    r'\bnears?\s+four-year\s+milestone\b',
    r'\bus\s+bull\s+market\b',
    r'\bbond\s+selling\s+could\s+intensify\b',
    r'\bcurrency\s+check\b',

    # General non-market noise, promos, devotionals, clickbait & retail tipsters
    r'\bjoin\s+(?:our\s+)?(?:telegram|channel|group)\b',
    r'\bt\.me\/',
    r'\bwhatsapp\b',
    r'\bgood\s+morning\b',
    r'\bjai\s+shree\b',
    r'\bshree\s+ganesh\b',
    r'\bnamaste\b',
    r'\bdevotional\b',
    r'\bjoke\b',
    r'\bmemes?\b',
    r'\bsubscribe\b',
    r'\bclick\s+here\b',
    r'\bdisclaimer\b',
    r'\btop\s+\d+\s+stocks\s+to\s+buy\b',
    r'\bstocks\s+to\s+buy\s+today\b',
    r'\bwhich\s+stock\s+to\s+buy\b',
    r'\bsuggests?\s+strategy\b',
    r'\bexpert\s+weighs\s+in\b',
    r'\bstock\s+market\s+outlook\s+today\b',
    r'\bwhere\s+are\s+nifty.*headed\b',
    r'\bhere\'s\s+why\s+it\s+needs\b',
    r'\bmeet\s+india\'s\b',
    r'\bgmp\s+compared\b',
    r'\bworth\s+more\s+than\s+earth\b',
    r'\bwhy\s+is\s+tesla.*attacking\b',
    r'\ba\s+tightening\s+test\b',
    r'\bshould\s+investors\s+buy\s+or\s+sell\b',
    r'\bdividend\s+stock\s+or\s+dividend\s+trap\b',
    r'\blast\s+chance\s+to\s+buy\b',
    r'\bace\s+investor\s+(?:buys|raises)\b',
    r'\bportfolio\s+stock:\s+up\b',
    r'\bwhat\s+can\s+trigger\s+a\s+sharp\s+rally\b',
    r'\bplans\s+your\s+retirement\b',
    r'\bcan\s+the\s+stock\s+rise\s+further\b',
    r'\btech\s+experts\s+decode\b',
    r'\bexperts?\'?\s+views?\b',
]

# COMPILED DROP REGEX
DROP_REGEX = re.compile('|'.join(DROP_PATTERNS), re.IGNORECASE)

# BROKERAGE HOUSES
BROKER_HOUSES = [
    'morgan stanley', 'goldman sachs', 'jefferies', 'citi', 'citigroup', 'clsa',
    'nomura', 'macquarie', 'bernstein', 'hsbc', 'ubs', 'jp morgan', 'jpmorgan',
    'bofa', 'bank of america', 'motilal oswal', 'kotak institutional', 'kotak',
    'icici direct', 'icici securities', 'hdfc sec', 'hdfc securities', 'axis capital',
    'nirmal bang', 'sharekhan', 'emkay', 'nuvama', 'antique', 'jm financial',
    'investec', 'prabhudas lilladher', 'incred', 'phillipcapital', 'elara'
]
BROKER_HOUSES_REGEX = re.compile(r'\b(?:' + '|'.join(re.escape(h) for h in BROKER_HOUSES) + r')\b', re.IGNORECASE)

# RATINGS KEYWORDS
RATINGS_KEYWORDS = [
    'buy', 'sell', 'hold', 'overweight', 'underweight', 'neutral', 'outperform',
    'underperform', 'accumulate', 'add', 'reduce'
]
RATINGS_REGEX = re.compile(r'\b(?:' + '|'.join(RATINGS_KEYWORDS) + r')\b', re.IGNORECASE)

# WIRE KEYWORDS (Macro, Rupee, Crude, Gold, FII/DII, Fed, RBI, Block Deals, etc.)
WIRE_KEYWORDS = [
    'nifty', 'sensex', 'rupee', 'inr', 'crude', 'brent', 'wti', 'gold', 'silver',
    'fii', 'dii', 'fpi', 'rbi', 'reserve bank', 'imf', 'world bank', 'fed', 'federal reserve',
    'fomc', 'jerome powell', 'block deal', 'bulk deal', 'ofs', 'offer for sale',
    'trade deal', 'tariff', 'geopolitic', 'opec', 'inflation', 'cpi', 'wpi', 'gdp',
    'yield', 'treasury'
]
WIRE_REGEX = re.compile(r'\b(?:' + '|'.join(WIRE_KEYWORDS) + r')\b', re.IGNORECASE)

# COMPANY ACTIONS / FILINGS WHITELIST KEYWORDS
COMPANY_ACTIONS_KEYWORDS = [
    # Orders & Contracts & Tenders
    'order', 'bagged order', 'bags order', 'secures order', 'contract', 'awarded', 'wins order', 'loi', 'letter of award',
    'bidder', 'qualified bidder', 'sole eligible', 'tender', 'artillery', 'shells', 'defence', 'defense', 'lowest bidder', 'l1 bidder',
    # Results & Financials
    'result', 'q1', 'q2', 'q3', 'q4', 'fy24', 'fy25', 'fy26', 'fy27', 'net profit', 'revenue', 'ebitda', 'pat', 'margin', 'loss', 'guidance',
    # Projects & Capex
    'project', 'commission', 'commercial production', 'capex', 'plant', 'expansion', 'facility', 'capacity', 'solar', 'wind', 'renewable', 'utility-scale', 'battery storage', 'mw', 'gw', 'execution',
    # Stores & Branches
    'store', 'outlet', 'showroom', 'branch', 'exclusive brand store',
    # Ratings (Upgrades, downgrades, or revisions)
    'credit rating', 'rating upgrade', 'rating downgrade', 'upgrade', 'upgrades', 'downgrade', 'downgrades', 'credit rating upgrade', 'credit rating downgrade',
    # Approvals & Products
    'approval', 'usfda', 'fda', 'cci', 'patent', 'nod', 'clearance', 'environmental clearance', 'mobile game', 'launch',
    # Director & C-Suite Leadership changes
    'director', 'kmp', 'key managerial personnel', 'managing director', 'chief executive', 'ceo', 'cfo', 'coo', 'executive director',
    'resignation', 'resigned', 'appointment', 'appointed', 're-designation', 'senior management',
    # Corporate actions, QIP, Loans & Buybacks
    'bonus', 'dividend', 'stock split', 'rights issue', 'buyback', 'merger', 'demerger', 'amalgamation', 'scheme of arrangement', 'acquisition', 'stake',
    'qip', 'qualified institutional placement', 'term loan', 'facility agreement', 'fund raising', 'allotment', 'warrant', 'convertible warrants', 'debt securities',
    # Operations & Business Highlights
    'toll collection', 'sales growth', 'festive sale', 'swagotsav', 'demand', 'growth in', 'surge', 'formula ride', 'racing game'
]
COMPANY_ACTIONS_REGEX = re.compile(r'\b(?:' + '|'.join(COMPANY_ACTIONS_KEYWORDS) + r')\b', re.IGNORECASE)


class FilterEngine:
    @staticmethod
    def should_drop(text: str) -> bool:
        """Returns True if the text matches any blacklisted/drop pattern."""
        if not text:
            return True
        return bool(DROP_REGEX.search(text))

    @staticmethod
    def classify_and_validate(text: str, source_type: str) -> Tuple[bool, Optional[str]]:
        """
        Classifies incoming item into:
        - 'filing': Exchange company announcement
        - 'flash': Fast company news/earnings flash
        - 'broker_note': Brokerage upgrade/downgrade/target
        - 'wire': Macro/market one-liner
        Returns (is_kept, category)
        """
        if FilterEngine.should_drop(text):
            return False, None

        # 1. Broker Note Check
        has_broker = bool(BROKER_HOUSES_REGEX.search(text))
        has_rating_or_target = bool(re.search(r'\btarget\b|\brating\b|\btp\b|₹\d+|\brs\.?\s*\d+', text, re.IGNORECASE)) and bool(RATINGS_REGEX.search(text))
        if has_broker and (has_rating_or_target or 'target' in text.lower()):
            return True, 'broker_note'

        # 2. Exchange Filings Check
        st = (source_type or "").lower()
        if st in ('nse_filing', 'bse_filing', 'bse', 'nse'):
            # Must match company actions whitelist
            if COMPANY_ACTIONS_REGEX.search(text):
                return True, 'filing'
            return False, None

        # 3. Regulatory / Official updates (SEBI, RBI, PIB)
        if st in ('sebi', 'rbi', 'pib'):
            return True, st

        # 4. Wire Check (Macro, Commodities, Currency, FII/DII, RBI)
        if WIRE_REGEX.search(text):
            # Check if it has flash/wire characteristics
            return True, 'wire'

        # 5. Company News Flash Check (From Mint, Moneycontrol, ET)
        if COMPANY_ACTIONS_REGEX.search(text):
            return True, 'flash'

        return False, None
