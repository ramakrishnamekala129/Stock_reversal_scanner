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
TIMEFRAME = os.getenv("TIMEFRAME", "5minute")
CANDLE_DURATION_MINUTES = 5 if "5" in TIMEFRAME else (3 if "3" in TIMEFRAME else (15 if "15" in TIMEFRAME else 5))
# Market Mode: 'SPOT' (cash equity)
DEFAULT_MARKET_MODE = "SPOT"
# Default Stock Universe: 'NIFTY500' (Broad Market 500 stocks)
DEFAULT_UNIVERSE = "NIFTY500"
# Intraday Breakout Execution Target & Stop Loss Settings
DEFAULT_TARGET_PCT = float(os.getenv("DEFAULT_TARGET_PCT", "2.0"))        # Profit Target: +2.0%
DEFAULT_STOP_LOSS_PCT = float(os.getenv("DEFAULT_STOP_LOSS_PCT", "1.0"))  # Initial Stop Loss: -1.0%
ENABLE_TRAILING_STOP = os.getenv("ENABLE_TRAILING_STOP", "true").lower() in ("true", "1", "yes")  # Trailing Stop: Enabled
TRAILING_ACTIVATION_PCT = float(os.getenv("TRAILING_ACTIVATION_PCT", "1.0"))  # Activate trailing at +1.0%
TRAILING_OFFSET_PCT = float(os.getenv("TRAILING_OFFSET_PCT", "0.4"))          # Trail by 0.4% from peak
DEFAULT_EOD_EXIT_TIME = os.getenv("DEFAULT_EOD_EXIT_TIME", "14:30")            # Intraday EOD Time Exit / Square-Off: 14:30

# Primary Reversal Scanner Timeframes
# Derive from TIMEFRAME unless explicitly overridden by SCANNER_TIMEFRAMES env
_configured_tfs = os.getenv("SCANNER_TIMEFRAMES", "")
if _configured_tfs:
    SCANNER_TIMEFRAMES = [t.strip() for t in _configured_tfs.split(",") if t.strip()]
else:
    # Default timeframes strictly aligned with TIMEFRAME setting (no 3m unless configured)
    base_tf = "5m" if "5" in TIMEFRAME else ("3m" if "3" in TIMEFRAME else ("15m" if "15" in TIMEFRAME else "5m"))
    SCANNER_TIMEFRAMES = [base_tf, "15m"] if base_tf != "15m" else ["15m"]

TIMEFRAME_MINUTES = {"3m": 3, "5m": 5, "15m": 15}
HEMA_TIMEFRAMES = ["15m", "30m", "1h", "2h", "4h", "1d"]

# Feature Flags (Tab 1 5-Minute Reversal Signals disabled by default per user request)
ENABLE_TAB1_REVERSAL_SIGNALS = os.getenv("ENABLE_TAB1_REVERSAL_SIGNALS", "false").lower() in ("true", "1", "yes")

# Historical Candle Database & Automated Gap Filler Settings
HISTORICAL_DB_PATH = DATA_DIR / "historical_candles.db"
ENABLE_HISTORICAL_GAP_FILLER = os.getenv("ENABLE_HISTORICAL_GAP_FILLER", "true").lower() in ("true", "1", "yes")
HISTORICAL_LOOKBACK_DAYS = int(os.getenv("HISTORICAL_LOOKBACK_DAYS", "30"))
HISTORICAL_CANDLE_GRANULARITY = os.getenv("HISTORICAL_CANDLE_GRANULARITY", "1minute")

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
NARROW_CPR_THRESHOLD_PCT = 0.21        # CPR width <= 0.21% of price
NARROW_TRAP_ZONE_THRESHOLD_PCT = 0.21   # Trap width <= 0.21% of price

# Signal Scoring Thresholds & Weights
MIN_SIGNAL_SCORE = 4

SCORE_WEIGHTS = {
    # Patterns
    "BULLISH_ENGULFING": 3,
    "BEARISH_ENGULFING": 3,
    "BULLISH_HARAMI": 2,
    "BEARISH_HARAMI": 2,
    "HAMMER": 2,
    "PIN_BAR": 2,
    "INVERSE_HAMMER": 2,
    "SHOOTING_STAR": 2,
    "BEARISH_PIN_BAR": 2,
    "HANGING_MAN": 2,
    "BULLISH_MARUBOZU": 3,
    "BEARISH_MARUBOZU": 3,
    
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
