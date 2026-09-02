# Upstox 5-Minute F&O Intraday Reversal Scanner

A high-performance, real-time intraday trading scanner designed for the entire National Stock Exchange (NSE) F&O universe (210+ liquid equity stocks). 

The system leverages Upstox Protobuf V3 WebSocket feeds, multi-factor technical confluence scoring, Central Pivot Range (CPR), Daily Floor Pivots, Trap Zones, and candlestick pattern recognition to identify high-probability intraday reversal setups on 5-minute candles.

---

## 🚀 Key Highlights & Features

- **210+ F&O Universe Monitoring**: Automatically downloads NSE equity instruments, filters for the official F&O derivatives list, and monitors all instruments simultaneously.
- **Upstox Protobuf V3 Live Feeds**: Streams real-time ticks with sub-second market data processing and automatic reconnection.
- **Multi-Factor Confluence Scoring Engine**:
  - Every detected reversal candidate is scored based on confluence weightings (minimum actionable conviction score $\ge 4$).
  - Scoring factors: Candlestick pattern conviction, Pivot Point crossover, PDH/PDL breakouts, R1/S1 interactions, CPR Breakouts/Breakdowns, Narrow CPR bonus, and Relative Volume surges ($> 1.2\times$).
- **Central Pivot Range (CPR)**:
  - Daily Pivot Point ($PP$), Top Central ($TC$), and Bottom Central ($BC$).
  - **Narrow CPR ($\le 0.20\%$)**: Identifies explosive trending day candidates.
  - **Candlestick Patterns at CPR**: Pinpoints bullish support bounces and bearish resistance rejections directly at the Central Pivot Range.
  - **Decisive CPR Breakout & Breakdown**: Validates that $\ge 60\%$ of the 5-minute candle range and real body closes decisively outside the CPR.
- **Trap Zones**:
  - **Bull Trap Zone**: Confluence between Resistance 1 ($R1$) and Previous Day High ($PDH$).
  - **Bear Trap Zone**: Confluence between Support 1 ($S1$) and Previous Day Low ($PDL$).
  - **Narrow Trap Zone ($\le 0.20\%$)**: Highlights ultra-tight trap zones for sharp reversals.
- **8 Candlestick Patterns**:
  - Bullish: `Bullish Engulfing`, `Bullish Harami`, `Hammer`, `Inverse Hammer`.
  - Bearish: `Bearish Engulfing`, `Bearish Harami`, `Shooting Star`, `Hanging Man`.
- **Native Desktop GUI (Tkinter)**:
  - Sleek, dark-themed responsive desktop application.
  - **Interactive Multi-Select CPR / Trap Filter**: Filter simultaneously by any combination of Narrow CPR, Trap Zones, CPR Breakouts, and Reversals at CPR.
  - **Dynamic Sorting & Clickable Headers**: Sort instantly by Time, Score, Relative Volume, Symbol, LTP, or CPR %.
  - **Flicker-Free In-Place Updates**: Live prices, percentage changes, volumes, and technical zones update smoothly in real time without scroll-jumping.
  - Audio chimes for new bullish/bearish alerts.
  - One-click CSV export for offline review.
- **FastAPI Web Dashboard**: Embedded web application with real-time WebSocket broadcasting.
- **Local SQLite Caching**: Sub-millisecond candle lookbacks and atomic batch inserts (`reversal_scanner.db` in WAL mode).

---

## 📂 Repository Architecture

```text
Stock_reversal_scanner/
│
├── config.py                 # Central configuration: thresholds, scoring weights, API settings
├── main.py                   # Main CLI & GUI entry point
├── run_scanner.bat           # 1-Click launcher for live scanning
├── run_dry_run.bat           # 1-Click launcher for historical session analysis
│
├── indicators/
│   └── pivots.py             # Pivot formulas (PP, TC, BC, R1-R3, S1-S3), CPR, and Trap Zones
│
├── patterns/
│   └── candlestick.py        # Algorithmic pattern detection (Engulfing, Harami, Hammer, etc.)
│
├── scanner/
│   ├── scanner.py            # Main orchestrator coordinating feeds, candles, and alerts
│   ├── signal_engine.py      # Confluence scoring engine & signal generator
│   ├── dedup.py              # Event deduplication cache (prevents duplicate alerts)
│   └── formatter.py          # Terminal colored alert cards
│
├── gui/
│   └── app.py                # Native desktop Tkinter dashboard with multi-select filters
│
├── upstox/
│   ├── auth.py               # Token authentication and API client initialization
│   ├── rest.py               # Upstox REST API wrapper
│   └── websocket.py          # MarketDataStreamerV3 protobuf tick streamer
│
├── market/
│   ├── candle_engine.py      # 5-minute candle aggregation & closure manager
│   ├── historical.py         # Async historical 5M and daily OHLCV loader
│   ├── instruments.py        # Upstox instrument master & F&O filter
│   └── session.py            # Market timing manager (09:15 - 15:30 IST)
│
├── database/
│   └── repository.py         # SQLite WAL repository for candles, pivots, and signals
│
├── excel/
│   └── live_excel.py         # Live streaming Excel workbook exporter
│
├── web/
│   ├── server.py             # FastAPI web server
│   ├── state.py              # Thread-safe WebSocket broadcast state
│   ├── templates/index.html  # Modern dark web UI template
│   └── static/               # CSS & JavaScript for web interface
│
└── tests/                    # Pytest test suite (21 unit tests covering all components)
```

---

## ⚙️ Key Configuration (`config.py`)

All thresholds can be configured in [`config.py`](config.py):

| Parameter | Default | Description |
|---|---|---|
| `NARROW_CPR_THRESHOLD_PCT` | `0.20` | Threshold ($\le 0.20\%$) to classify CPR as Narrow |
| `NARROW_TRAP_ZONE_THRESHOLD_PCT` | `0.20` | Threshold ($\le 0.20\%$) to classify Trap Zones as Narrow |
| `MIN_SIGNAL_SCORE` | `4` | Minimum conviction score required to trigger an alert |
| `MIN_RELATIVE_VOLUME` | `1.2` | Minimum volume surge factor required for volume confirmation |
| `HAMMER_WICK_BODY_RATIO` | `2.0` | Minimum lower shadow to real body ratio for Hammer |
| `ENGULFING_MIN_BODY_PCT` | `0.5` | Minimum candle body size for Engulfing patterns |
| `HARAMI_MAX_BODY_RATIO` | `0.6` | Maximum current body ratio relative to prior body |

---

## 🛠️ Installation & Setup

### 1. Prerequisites
- **Python 3.10+** (Python 3.11, 3.12, 3.13 supported)
- Windows / Linux / macOS
- Upstox Developer Account with an active API app

### 2. Clone & Install Dependencies
```bash
git clone https://github.com/ramakrishnamekala129/Stock_reversal_scanner.git
cd Stock_reversal_scanner

# Install required Python packages
pip install -r requirements.txt
# (or: pip install upstox-python-sdk httpx pandas pytz openpyxl fastapi uvicorn websockets pytest)
```

### 3. Upstox Access Token Configuration
Place your generated daily Upstox Access Token into a text file named `upstok_accesstoken.txt` in the root directory, or set it via environment variable:
```bash
# In upstok_accesstoken.txt (single line):
eyJhbGciOiJ...your_access_token_here...
```
*(Note: `upstok_accesstoken.txt` and `.env` are excluded by `.gitignore` to prevent secret leakage).*

---

## 🚦 Execution

### 1. Live Real-Time Scanner (Desktop GUI)
Double-click [`run_scanner.bat`](run_scanner.bat) or run:
```bash
python main.py
```
- Launches the native desktop dashboard.
- Connects to Upstox V3 WebSocket.
- Scans all 5-minute candle closures in real time.

### 2. Historical Dry-Run / Session Back-Scan
Double-click [`run_dry_run.bat`](run_dry_run.bat) or run:
```bash
python main.py --dry-run
```
- Fetches all of today's completed 5-minute historical candles from market open (`09:15` IST).
- Reconstructs all reversal setups that formed throughout the day without opening live WebSocket streams.

### 3. Headless / Web-Only Mode
```bash
python main.py --no-gui
```
- Runs backend scanning in headless mode.
- Access the web interface at `http://localhost:8000`.

---

## 🧪 Verification & Testing

The project includes an automated unit test suite covering candle engines, indicators, patterns, GUI, WebSocket, and database persistence:

```bash
python -m pytest tests/ -v
```

Output:
```text
============================= 21 passed in 3.81s ==============================
```

---

## ⚠️ Disclaimer
*This software is developed for educational and analytical purposes only. Intraday trading in derivatives involves substantial risk of loss. Always apply strict stop-loss and risk management principles before placing trades based on automated signals.*
