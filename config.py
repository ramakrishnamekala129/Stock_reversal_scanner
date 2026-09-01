"""
Centralized Configuration for Upstox F&O Intraday Scanner.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env if present
load_dotenv()

# Base directories
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
CACHE_DIR = DATA_DIR / "cache"
DB_PATH = DATA_DIR / "scanner.db"

# Create data/cache directories if not existing
DATA_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Upstox Credentials
UPSTOX_ACCESS_TOKEN = os.getenv("UPSTOX_ACCESS_TOKEN", "")
UPSTOX_ANALYTICS_TOKEN = os.getenv("UPSTOX_ANALYTICS_TOKEN", "")
UPSTOX_CLIENT_ID = os.getenv("UPSTOX_CLIENT_ID", "")
UPSTOX_CLIENT_SECRET = os.getenv("UPSTOX_CLIENT_SECRET", "")
UPSTOX_REDIRECT_URI = os.getenv("UPSTOX_REDIRECT_URI", "https://127.0.0.1:5000/")

# Fallback to local token file if UPSTOX_ACCESS_TOKEN is not in env
TOKEN_FILE_PATH = BASE_DIR / "upstok_accesstoken.txt"
if not UPSTOX_ACCESS_TOKEN and TOKEN_FILE_PATH.exists():
    try:
        with open(TOKEN_FILE_PATH, "r", encoding="utf-8") as f:
            UPSTOX_ACCESS_TOKEN = f.read().strip()
    except Exception:
        pass

# Market Timings & Timezone
MARKET_TIMEZONE = "Asia/Kolkata"
MARKET_OPEN = "09:15"
MARKET_CLOSE = "15:30"
TIMEFRAME = "5minute"
CANDLE_DURATION_MINUTES = 5

# Volume Confirmation
VOLUME_LOOKBACK = 20
MIN_RELATIVE_VOLUME = 1.2
ENABLE_VOLUME_CONFIRMATION = True

# Candlestick Pattern Ratios & Thresholds
HAMMER_WICK_BODY_RATIO = 2.0
INVERSE_HAMMER_WICK_BODY_RATIO = 2.0
HANGING_MAN_WICK_BODY_RATIO = 2.0
DOJI_BODY_THRESHOLD = 0.1  # body <= 10% of total candle range
ENGULFING_MIN_BODY_PCT = 0.5  # body must be at least 50% of total candle range
HARAMI_MAX_BODY_RATIO = 0.6   # current body <= 60% of previous body

# Context & Pivot Settings
ENABLE_PIVOT_CONTEXT = True
ENABLE_TREND_CONTEXT = True
TREND_LOOKBACK_CANDLES = 5

# Signal Scoring Thresholds & Weights
MIN_SIGNAL_SCORE = 4

SCORE_WEIGHTS = {
    # Patterns
    "BULLISH_ENGULFING": 3,
    "BEARISH_ENGULFING": 3,
    "BULLISH_HARAMI": 2,
    "BEARISH_HARAMI": 2,
    "HAMMER": 2,
    "INVERSE_HAMMER": 2,
    "SHOOTING_STAR": 2,
    "HANGING_MAN": 2,
    
    # Pivot Context
    "CLOSE_ABOVE_PIVOT": 1,
    "CLOSE_BELOW_PIVOT": -1,
    "BREAK_PDH": 2,
    "BREAK_PDL": -2,
    "BREAK_R1": 2,
    "BREAK_S1": -2,
    "NEAR_S1_S2_SUPPORT": 2,
    "NEAR_R1_R2_RESISTANCE": -2,
    
    # Volume
    "HIGH_RELATIVE_VOLUME": 1,
}

# API Rate Limits & Performance
UPSTOX_RATE_LIMIT_PER_SEC = 25  # Official Upstox Market Data limit: 25 req/sec
MAX_CONCURRENT_REQUESTS = 25
API_RETRY_ATTEMPTS = 3
API_RETRY_BACKOFF_BASE = 1.0  # seconds
CACHE_EXPIRY_HOURS = 12

# Database, Excel & UI Dashboard Settings
ENABLE_DB_STORAGE = True
ENABLE_EXCEL_EXPORT = False
EXCEL_FILE_PATH = BASE_DIR / "fno_scanner_live.xlsx"
EXCEL_AUTO_OPEN = False
EXCEL_UPDATE_INTERVAL_SECONDS = 1.0

# Tkinter Desktop GUI Settings
ENABLE_TKINTER_GUI = True

# FastAPI Web Dashboard Settings (Optional)
ENABLE_WEB_DASHBOARD = False
WEB_HOST = "127.0.0.1"
WEB_PORT = 8000
WEB_AUTO_OPEN = False
