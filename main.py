import os
import time
import sqlite3
import hashlib
import re
import requests
import google.generativeai as genai
from datetime import datetime
from zoneinfo import ZoneInfo

# --- CONFIGURATION ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "@your_channel_id")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "YOUR_GEMINI_API_KEY")

DB_PATH = "market_wire.db"
TIMEZONE = ZoneInfo("Asia/Kolkata")
OPERATING_START_TIME = time(7, 0)
OPERATING_END_TIME = time(23, 30)

# --- GEMINI SETUP ---
genai.configure(api_key=GEMINI_API_KEY)
generation_config = {
    "temperature": 0.2,
    "top_p": 1,
    "top_k": 1,
    "max_output_tokens": 512,
}
safety_settings = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
]
gemini_model = genai.GenerativeModel(
    model_name="gemini-1.5-flash",
    generation_config=generation_config,
    safety_settings=safety_settings
)
SYSTEM_PROMPT = """You are an AI engine for a high-speed Indian stock market news channel on Telegram. Your task is to reformat raw corporate announcements and news headlines into concise, scannable, and fact-only alerts. Your operating hours are 7:00 AM to 11:30 PM India time. Remain silent overnight.

**Core Directives:**
1.  **Source Input**: You will receive raw text from BSE/NSE filings, news wires (Moneycontrol, Mint), and RBI.
2.  **Primary Task**: Analyze the text and determine if it is a "KEEP" or "DROP" event. If it is a "KEEP" event, reformat it according to the strict rules below.

**Strict Filtering Rules (DROP these immediately):**
- **DROP** any mention of: shareholding patterns, trading window closures, lost/duplicate share certificates, compliance certificates (Reg. 74, 39, 40), and routine paperwork.
- **DROP** any con-call or analyst meeting *schedules*. Only report the *outcome* or *results*.
- **DROP** newspaper publications, transcripts, investor presentations, and analyst bylines.
- **DROP** generic, automated market commentary like "Nifty 50 stocks among top gainers" or "Volume shockers."
- **DROP** any content that looks like a company's mailing address, contact details, or website footers (e.g., "Phiroze Jeejeebhoy Towers", "Dalal Street", "CIN: L...").
- **DROP** jokes, promotions, "good morning" messages, or "subscribe to our channel" text.

**Formatting Rules (For KEEP events):**
- **Corporate Filings**: Always format as `Company Name – SUBJECT IN CAPS`. Use an en-dash (–).
    - *Example*: `Hindustan Aeronautics – ORDER WIN FROM DEFENCE MINISTRY`
- **Broker Notes**: Must contain the house, stock, rating, and a target price. If any are missing, drop it. Format with double line breaks.
    - *Example*:
      Nuvama on Dixon:
      Rating: BUY
      Target: ₹16,500

      • Festive smartphone volumes down 12% YoY.
      • Mid-segment demand softness impacting realization.
- **News & Wires**: Keep the headline short and factual. Do not add any commentary, "why it matters," or source lines.
    - *Example*: `OSWAL PUMPS: Plans ₹455.85 crore solar cell plant.`
- **Data Integrity**: **NEVER** round numbers. If the source says ₹455.85 crore, you must use ₹455.85 crore.
- **Clarity**: Use double line breaks between paragraphs and bullet points for mobile readability."""

# Extended Drop Patterns from your handoff file
DROP_PATTERNS = [
    # Shareholding, trading window, lost certs
    r'\bshareholding\s+pattern\b', r'\btrading\s+window\b', r'\blost\s+certificate\b', r'\bduplicate\s+share\b', r'reg(?:ulation)?\.?\s*39\s*\(\s*3\s*\)',
    # Routine compliance
    r'certificate\s+under\s+reg', r'compliance\s+certificate', r'investor\s+complaints', r'corporate\s+governance',
    # Meeting intimations (not results)
    r'\bboard\s+meeting\s+intimation\b', r'\bis\s+scheduled\s+to\s+be\s+held\b',
    # Newspaper ads, transcripts
    r'copy\s+of\s+newspaper', r'transcript\s+of\s+(?:analyst|investor)',
    # Con-call scheduling
    r'\bintimation\s+of\s+schedule\s+of\s+(?:earnings|investor|analyst)\s+call\b', r'\bschedule\s+of\s+.*?\s+(?:call|meet)\b',
    # Routine admin
    r'\bcompany\s+secretary\b', r'\bscrutinizer\'?s?\s+report\b', r'\bvoting\s+results?\b', r'\bpostal\s+ballot\b',
    # ESOPs
    r'\ballotment\s+of\s+.*?\s+esop\b',
    # Exchange queries
    r'\bspurt\s+in\s+volume\b', r'clarification\s+sought\s+by',
    # Algorithmic spam
    r'stocks\s+among\s+top\s+(?:gainers|losers)', r'\bvolume\s+(?:gainer|shocker|spike)\b',
    # Clickbait
    r'\btime\s+to\s+(?:buy|sell|exit)\b', r'\bexperts?\'?\s+views?\b', r'\bshould\s+investors\s+buy\b',
    # Promotions & noise
    r'\bjoin\s+(?:our\s+)?telegram\b', r'\bgood\s+morning\b', r'\bdisclaimer\b',
    # Address/Footer info
    r'phiroze\s+jejeebhoy|dalal\s+street|cin\s*:\s*l\w+'
]
COMPILED_DROP_REGEX = re.compile('|'.join(DROP_PATTERNS), re.IGNORECASE)

# --- DATABASE & DEDUPLICATION ---
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sent_posts (
                id INTEGER PRIMARY KEY,
                content_hash TEXT UNIQUE NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()

def is_duplicate(text):
    norm_text = re.sub(r'\s+', ' ', re.sub(r'[^\w\s]', '', text.lower())).strip()
    content_hash = hashlib.sha256(norm_text.encode('utf-8')).hexdigest()
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM sent_posts WHERE content_hash = ?", (content_hash,))
        if cursor.fetchone():
            return True, content_hash
    return False, content_hash

def record_post(content_hash):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO sent_posts (content_hash) VALUES (?)", (content_hash,))
        conn.commit()

# --- TELEGRAM DISPATCHER ---
def send_telegram_message(text):
    if not text or "drop" in text.lower():
        print(f"Skipping empty or 'DROP' message.")
        return False
    
    is_dup, content_hash = is_duplicate(text)
    if is_dup:
        print(f"Skipping duplicate message: {text[:60]}...")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHANNEL_ID, "text": text, "disable_web_page_preview": True}
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            print(f"Dispatched: {text[:60]}...")
            record_post(content_hash)
            return True
        else:
            print(f"Error sending message: {response.text}")
            return False
    except Exception as e:
        print(f"Telegram dispatch failed: {e}")
        return False

# --- NEWS FETCHERS ---
def fetch_bse_announcements():
    print("Fetching from BSE...")
    today = datetime.now(TIMEZONE).strftime('%Y%m%d')
    url = f"https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w?pageno=1&strCat=-1&strPrevDate={today}&strScrip=&strSearch=P&strToDate={today}&strType=C"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        response = requests.get(url, headers=headers, timeout=15)
        data = response.json().get('Table', [])
        return [f"{item['SLONGNAME']} – {item['NEWSSUB']}\n\nDetails: {item['HEADLINE']}" for item in data]
    except Exception as e:
        print(f"Error fetching from BSE: {e}")
        return []

def fetch_nse_announcements():
    print("Fetching from NSE...")
    url = "https://www.nseindia.com/api/corporate-announcements?index=equities"
    # NSE requires browser-like headers
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/javascript, */*; q=0.01',
        'Referer': 'https://www.nseindia.com/companies-listing/corporate-filings-announcements',
    }
    try:
        # Use a session to handle cookies
        session = requests.Session()
        session.get("https://www.nseindia.com", headers=headers, timeout=15) # Warm up session
        response = session.get(url, headers=headers, timeout=15)
        data = response.json()
        return [f"{item['symbol']} – {item['subject']}\n\nDetails: {item['body']}" for item in data]
    except Exception as e:
        print(f"Error fetching from NSE: {e}")
        return []

# --- MAIN WORKFLOW ---
def process_announcements(announcements):
    for raw_text in announcements:
        if COMPILED_DROP_REGEX.search(raw_text):
            print(f"Pre-filtered (regex): {raw_text[:70]}...")
            continue
        
        is_dup, _ = is_duplicate(raw_text)
        if is_dup:
            print(f"Skipping duplicate (pre-AI): {raw_text[:70]}...")
            continue
            
        try:
            prompt_parts = [SYSTEM_PROMPT, "Input Text:\n", raw_text, "\nOutput:"]
            response = gemini_model.generate_content(prompt_parts)
            clean_message = response.text.strip()
            
            if clean_message and "drop" not in clean_message.lower():
                send_telegram_message(clean_message)
                time.sleep(2) # Avoid rate limiting
            else:
                print(f"AI decided to DROP: {raw_text[:70]}...")

        except Exception as e:
            print(f"Error processing with Gemini: {e}")

def main_job():
    now_ist = datetime.now(TIMEZONE).time()
    if not (OPERATING_START_TIME <= now_ist <= OPERATING_END_TIME):
        print(f"Outside operating hours ({OPERATING_START_TIME}-{OPERATING_END_TIME} IST). Sleeping.")
        return

    print(f"--- Running market wire check at {now_ist} IST ---")
    init_db()
    
    bse_items = fetch_bse_announcements()
    if bse_items:
        process_announcements(bse_items)

    time.sleep(5) # Pause between exchange fetches

    nse_items = fetch_nse_announcements()
    if nse_items:
        process_announcements(nse_items)

    print("--- Cycle complete ---")

# This is the function that will be called by the cloud scheduler
def handler(event=None, context=None):
    """
    Entry point for Google Cloud Functions or AWS Lambda.
    For GitHub Actions, this function will be called directly in the workflow.
    """
    main_job()
    return {
        'statusCode': 200,
        'body': 'Job executed successfully!'
    }

if __name__ == "__main__":
    # For local testing, run the job once.
    handler()
