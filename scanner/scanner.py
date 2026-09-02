"""
Main F&O Intraday Scanner Orchestrator.
Coordinates data feeds, candle engine, pivots, pattern detection, and signal alerts.
"""

import logging
import signal
import sys
import threading
import time
from datetime import datetime
from typing import Dict, List, Optional
import pandas as pd

import config
from database.repository import DatabaseRepository
from excel.live_excel import LiveExcelManager
from indicators.pivots import DailyPivots, calculate_daily_pivots
from market.candle_engine import Candle, CandleEngine, CandleStatus
from market.historical import HistoricalDataLoader, PreviousDayOHLCV
from market.instruments import InstrumentManager
from market.session import MarketSessionManager
from scanner.dedup import EventDeduplicator
from scanner.formatter import ConsoleFormatter
from scanner.signal_engine import SignalEngine, SignalEvent
from upstox.auth import UpstoxAuth
from upstox.rest import UpstoxRestClient
from upstox.websocket import NormalizedTick, UpstoxWebSocketStreamer
from web.server import WebServerManager
from web.state import dashboard_state

logger = logging.getLogger(__name__)


class FNOIntradayScanner:
    """End-to-End Backend F&O Intraday Scanner."""

    def __init__(
        self,
        auth: Optional[UpstoxAuth] = None,
        enable_excel: bool = config.ENABLE_EXCEL_EXPORT,
        enable_web: bool = config.ENABLE_WEB_DASHBOARD,
    ):
        self.auth = auth or UpstoxAuth()
        self.rest_client = UpstoxRestClient(self.auth.get_api_client() if self.auth.has_access_token else None)
        self.instrument_mgr = InstrumentManager(self.rest_client)
        self.hist_loader = HistoricalDataLoader(self.rest_client)
        self.session_mgr = MarketSessionManager()
        self.dedup = EventDeduplicator()
        self.signal_engine = SignalEngine()
        self.db = DatabaseRepository() if config.ENABLE_DB_STORAGE else None
        self.excel_mgr = LiveExcelManager() if enable_excel else None
        self.web_server = WebServerManager() if enable_web else None

        self.candle_engine = CandleEngine(on_candle_closed=self._handle_candle_closed)
        self.ws_streamer: Optional[UpstoxWebSocketStreamer] = None

        self._pivots: Dict[str, DailyPivots] = {}
        self._universe: Dict[str, dict] = {}
        self._is_running = False

    def startup(self, force_refresh: bool = False, symbols: Optional[List[str]] = None):
        """
        Executes complete application startup sequence.
        """
        logger.info("Starting Upstox 5M F&O Intraday Scanner...")

        # 1. Authenticate / Check credentials
        is_authenticated = self.auth.validate_token()
        rest_status = "CONNECTED" if is_authenticated else "AVAILABLE (PUBLIC ENDPOINTS)"
        analytics_status = "AVAILABLE" if self.auth.has_analytics_token else "NOT CONFIGURED"

        # 2. Load NSE F&O Universe
        self._universe = self.instrument_mgr.load_fno_universe(force_refresh=force_refresh)
        if symbols:
            symbols_set = set(symbols)
            self._universe = {k: v for k, v in self._universe.items() if k in symbols_set}

        fno_count = len(self._universe)
        if fno_count == 0:
            logger.error("No F&O instruments discovered! Exiting startup.")
            return False

        self.session_mgr.stats.symbols_scanned = fno_count

        # 3. Load Previous Trading Day OHLCV & Calculate Pivots
        pd_data_map = self.hist_loader.load_all_previous_day_ohlcv(self._universe, force_refresh=force_refresh)
        for sym, pd_item in pd_data_map.items():
            pivot = calculate_daily_pivots(
                symbol=sym,
                date_str=pd_item.date,
                open_p=pd_item.open,
                high_p=pd_item.high,
                low_p=pd_item.low,
                close_p=pd_item.close,
                volume=pd_item.volume,
            )
            self._pivots[sym] = pivot
            if self.db:
                self.db.save_daily_levels(pivot.to_dict())

        logger.info(f"Computed pivot levels for {len(self._pivots)} F&O symbols.")

        # Initialize Web Dashboard State & Optional Excel
        dashboard_state.initialize_pivots(self._pivots)
        # 4. Fetch today's historical 5M candles (from 1m history)
        historical_5m = self.hist_loader.load_initial_5m_candles(self._universe, force_refresh=force_refresh)
        key_map = {sym: item["instrument_key"] for sym, item in self._universe.items()}
        self.candle_engine.initialize_history(historical_5m, key_map=key_map)
        self.evaluate_initial_history()

        # 5. Connect WebSocket
        ws_status = "READY"
        if self.auth.has_access_token:
            try:
                self.ws_streamer = UpstoxWebSocketStreamer(
                    api_client=self.auth.get_api_client(),
                    instrument_key_to_symbol=self.instrument_mgr.key_to_symbol_map,
                    on_tick=self._handle_live_tick,
                    mode="full",
                )
                inst_keys = self.instrument_mgr.get_instrument_keys()
                self.ws_streamer.connect(inst_keys)
                ws_status = "CONNECTED"
                dashboard_state.update_stats(ws_status="CONNECTED")
            except Exception as e:
                logger.error(f"Failed to start WebSocket streamer: {e}")
                ws_status = f"ERROR ({e})"
                dashboard_state.update_stats(ws_status=f"ERROR ({e})")
                self.session_mgr.stats.websocket_errors += 1
        else:
            ws_status = "NO ACCESS TOKEN (SIMULATION / DRY-RUN ONLY)"
            dashboard_state.update_stats(ws_status="DRY RUN / SIMULATION")

        # 6. Start FastAPI Web Dashboard (after history, signals & websocket are ready)
        if self.web_server:
            self.web_server.start()

        # 7. Display Startup Banner
        ConsoleFormatter.print_startup_banner(
            rest_status=rest_status,
            analytics_status=analytics_status,
            ws_status=ws_status,
            fno_count=fno_count,
        )

        self._is_running = True
        return True

    def _handle_live_tick(self, tick: NormalizedTick):
        """
        Receives normalized ticks from WebSocket and forwards them to Candle Engine & Web Dashboard.
        """
        if not self._is_running:
            return
        self.candle_engine.process_tick(tick)
        dashboard_state.update_price(tick.symbol, tick.ltp, tick.volume, tick.timestamp)
        if self.excel_mgr:
            self.excel_mgr.update_price(tick.symbol, tick.ltp, tick.volume, tick.timestamp)

    def _handle_candle_closed(self, symbol: str, candle: Candle, df_history: pd.DataFrame, print_console: bool = True):
        """
        Invoked ONLY when a 5-minute candle closes (e.g. at 09:20:00, 09:25:00, etc.).
        Runs pattern detection, pivot context scoring, and triggers deduplicated alerts.
        """
        self.session_mgr.stats.candles_processed += 1
        dashboard_state.update_stats(candles_processed=self.session_mgr.stats.candles_processed)

        # Persist closed candle in SQLite & update Web Dashboard price
        if self.db:
            self.db.save_candle(candle.to_dict())
        dashboard_state.update_price(symbol, candle.close, candle.volume, candle.timestamp)
        if self.excel_mgr:
            self.excel_mgr.update_price(symbol, candle.close, candle.volume, candle.timestamp)

        pivots = self._pivots.get(symbol)
        if not pivots:
            return

        # Run multi-factor signal detection
        signals = self.signal_engine.evaluate_candle(symbol, df_history, pivots)

        for sig in signals:
            self.session_mgr.stats.patterns_detected += 1
            pat_name = sig.pattern
            self.session_mgr.stats.pattern_breakdown[pat_name] = (
                self.session_mgr.stats.pattern_breakdown.get(pat_name, 0) + 1
            )

            # Deduplication check
            if self.dedup.is_duplicate(sig.symbol, sig.timestamp, sig.pattern):
                continue

            self.dedup.mark_seen(sig.symbol, sig.timestamp, sig.pattern)

            if "BULLISH" in sig.direction:
                self.session_mgr.stats.bullish_signals += 1
            elif "BEARISH" in sig.direction:
                self.session_mgr.stats.hanging_man_signals += 1

            # Output formatted signal card to terminal (if live)
            if print_console:
                ConsoleFormatter.print_signal(sig)

            # Save signal to database, broadcast to FastAPI Web Dashboard & optional Excel
            if self.db:
                self.db.save_signal(sig.to_dict())
            dashboard_state.add_signal(sig)
            if self.excel_mgr:
                self.excel_mgr.add_signal(sig)

    def evaluate_initial_history(self):
        """
        Scans all 5-minute historical candles from 09:15 up to current time across the universe,
        detecting all pattern & reversal signals that occurred today.
        """
        logger.info("Scanning existing 5-minute candles of today's session for reversal setups...")
        total_eval = 0
        for sym, candles in list(self.candle_engine._history.items()):
            df_full = self.candle_engine.get_candle_history_df(sym)
            if len(candles) >= 2:
                for i in range(2, len(candles) + 1):
                    sub_candle = candles[i - 1]
                    sub_df = df_full.iloc[:i]
                    self._handle_candle_closed(sym, sub_candle, sub_df, print_console=False)
                    total_eval += 1
        logger.info(f"Startup candle scan complete: Evaluated {total_eval} historical candles, detected {len(self.dedup._seen_events)} signals.")

    def sync_broker_candles_for_all(self):
        """
        Fetches the latest official broker-side 5-minute candles for the entire F&O universe.
        """
        logger.info("Syncing official 5-minute candles directly from broker for F&O universe...")
        broker_dfs = self.hist_loader.refresh_latest_broker_candles(self._universe)
        key_map = {sym: item["instrument_key"] for sym, item in self._universe.items()}
        for sym, df_b in broker_dfs.items():
            self.candle_engine.sync_broker_candles(sym, df_b, key_map=key_map)
        logger.info(f"Broker candle sync complete for {len(broker_dfs)} symbols.")

    def run_live(self):
        """
        Main execution loop for live market scanning.
        Polls official broker candles on every 5-minute closure (e.g. 09:20, 09:25...)
        while maintaining live WebSocket event tracking.
        """
        # Register graceful termination handlers if in main thread
        try:
            if threading.current_thread() is threading.main_thread():
                signal.signal(signal.SIGINT, self._signal_handler)
                signal.signal(signal.SIGTERM, self._signal_handler)
        except (ValueError, AttributeError):
            pass

        logger.info("Scanner listening for live market events... Press Ctrl+C to stop.")
        last_synced_minute = -1

        try:
            while self._is_running:
                now_ist = self.session_mgr.get_current_ist_time()
                
                # Check for Market Close (15:30 IST)
                if self.session_mgr.is_market_closed(now_ist):
                    logger.info("Market session closed. Finalizing candle queues...")
                    self.candle_engine.force_close_active_candles()
                    self.stop()
                    break

                # 5-minute boundary check: 2 seconds after each 5-minute boundary (e.g. 12:45:02, 12:50:02...)
                current_min = now_ist.minute
                current_sec = now_ist.second
                if current_min % 5 == 0 and current_sec >= 2 and current_min != last_synced_minute:
                    last_synced_minute = current_min
                    logger.info(f"5-Minute candle boundary reached ({now_ist.strftime('%H:%M:%S')}). Fetching broker-side candles...")
                    self.sync_broker_candles_for_all()

                time.sleep(1.0)
        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received.")
            self.stop()

    def _signal_handler(self, sig, frame):
        logger.info("Shutdown signal received.")
        self.stop()

    def stop(self):
        """Stops scanner, closes connections, and prints session summary statistics."""
        if not self._is_running:
            return
        self._is_running = False
        logger.info("Stopping scanner...")

        if self.ws_streamer:
            self.ws_streamer.disconnect()

        if self.excel_mgr:
            self.excel_mgr.close()

        if self.web_server:
            self.web_server.stop()

        # Print Session Summary Statistics
        self.session_mgr.stats.print_summary()
