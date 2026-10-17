import os
from datetime import time
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

# Load .env if present
load_dotenv()

# Telegram Settings
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "").strip()

# Operating Schedule (IST: Asia/Kolkata)
TIMEZONE = ZoneInfo("Asia/Kolkata")
OPERATING_START_TIME = time(7, 0)    # 07:00 AM IST
OPERATING_END_TIME = time(23, 30)    # 11:30 PM IST

# Polling Interval (in seconds)
EXCHANGE_POLL_INTERVAL = int(os.getenv("EXCHANGE_POLL_INTERVAL", "10")) # 10 seconds for high-frequency NSE/BSE
NEWS_POLL_INTERVAL = int(os.getenv("NEWS_POLL_INTERVAL", "15"))         # 15 seconds for News RSS/Wires
OVERNIGHT_SLEEP_INTERVAL = 60                                           # 60 seconds check during overnight quiet

# Database path for deduplication
DB_PATH = os.path.join(os.path.dirname(__file__), "data", "market_wire.db")
LOG_PATH = os.path.join(os.path.dirname(__file__), "data", "wire_out.log")
