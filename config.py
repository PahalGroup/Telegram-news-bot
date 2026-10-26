import os
from datetime import time
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

# Load .env if present
load_dotenv()

# Telegram Settings
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHANNEL_ID = (os.getenv("TELEGRAM_CHANNEL_ID", "") or os.getenv("TELEGRAM_CHAT_ID", "")).strip()

# Operating Schedule (IST: Asia/Kolkata) - 24/7 Continuous Mode
TIMEZONE = ZoneInfo("Asia/Kolkata")
TWENTY_FOUR_SEVEN = True
OPERATING_START_TIME = time(0, 0)      # 24/7 Active
OPERATING_END_TIME = time(23, 59, 59)  # 24/7 Active

# Polling Interval (in seconds)
EXCHANGE_POLL_INTERVAL = int(os.getenv("EXCHANGE_POLL_INTERVAL", "10")) # 10 seconds for high-frequency NSE/BSE
NEWS_POLL_INTERVAL = int(os.getenv("NEWS_POLL_INTERVAL", "15"))         # 15 seconds for News RSS/Wires
OVERNIGHT_SLEEP_INTERVAL = 15                                           # Continuous 24/7 active polling

# Database path for deduplication
DB_PATH = os.path.join(os.path.dirname(__file__), "data", "market_wire.db")
LOG_PATH = os.path.join(os.path.dirname(__file__), "data", "wire_out.log")

