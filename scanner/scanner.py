"""
Main F&O Intraday Scanner Orchestrator.
Coordinates data feeds, candle engine, pivots, pattern detection, and signal alerts.
"""

from concurrent.futures import ThreadPoolExecutor
import logging
import os
import signal
import sys
import threading
import time
from datetime import date, datetime, time as dt_time
import json
from pathlib import Path
from typing import Dict, List, Optional
import pandas as pd
import pytz

import config
from database.repository import DatabaseRepository
from excel.live_excel import LiveExcelManager
from indicators.pivots import DailyPivots, calculate_daily_pivots
from indicators.hema_t3 import HemaT3RegimeEngine, HemaT3Signal
from indicators.chartink_screener import ChartinkIntradayEngine, ChartinkSignal
from market.candle_engine import Candle, CandleEngine, CandleStatus, MultiTimeframeCandleEngine
from market.historical import HistoricalDataLoader, PreviousDayOHLCV
from market.instruments import InstrumentManager
from market.session import MarketSessionManager
from scanner.dedup import EventDeduplicator
from scanner.formatter import ConsoleFormatter
from scanner.signal_engine import SignalEngine, SignalEvent
from scanner.trigger_tracker import SignalTriggerTracker
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
        market_mode: str = config.DEFAULT_MARKET_MODE,
        universe_name: str = "FNO",
    ):
        self.auth = auth or UpstoxAuth()
        self.market_mode = market_mode.upper() if market_mode else "FUTURES"
        self.universe_name = universe_name.upper() if universe_name else "FNO"
        self.rest_client = UpstoxRestClient(self.auth.get_api_client() if self.auth.has_access_token else None)
        self.instrument_mgr = InstrumentManager(self.rest_client)
        self.hist_loader = HistoricalDataLoader(self.rest_client)
        self.session_mgr = MarketSessionManager()
        self.dedup = EventDeduplicator()
        self.signal_engine = SignalEngine()
        self.hema_engine = HemaT3RegimeEngine()
        self.chartink_engine = ChartinkIntradayEngine()
        self.trigger_tracker = SignalTriggerTracker()
        self.db = DatabaseRepository() if config.ENABLE_DB_STORAGE else None
        self.excel_mgr = LiveExcelManager() if enable_excel else None
        self.web_server = WebServerManager() if enable_web else None

        self.candle_engine = MultiTimeframeCandleEngine(on_candle_closed=self._handle_candle_closed)
        self.ws_streamer: Optional[UpstoxWebSocketStreamer] = None

        self._pivots: Dict[str, DailyPivots] = {}
        self._universe: Dict[str, dict] = {}
        self._is_running = False
        self._is_hema_scanning = False
        self._hema_scan_lock = threading.Lock()
        self._is_chartink_scanning = False
        self._chartink_scan_lock = threading.Lock()
        self._daily_dfs_cache: Dict[str, pd.DataFrame] = {}
        self._daily_cache_loaded: bool = False

    def _load_daily_candles_cache(self):
        """Loads daily candles cache from SQLite database for ultra-fast, sub-second Chartink screening."""
        if self._daily_cache_loaded and self._daily_dfs_cache:
            return
        try:
            t0 = time.time()
            from database.historical_db import HistoricalCandleDatabase
            hist_db = HistoricalCandleDatabase()
            dfs = hist_db.get_all_daily_candles_map()

            # If SQLite DB is empty, auto-seed from JSON cache if present
            if not dfs:
                cache_path = Path("data/cache/all_daily_candles_2026.json")
                if cache_path.exists():
                    logger.info("Seeding daily candles into SQLite database from JSON cache...")
                    with open(cache_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    hist_db.save_all_daily_candles_bulk(data)
                    dfs = hist_db.get_all_daily_candles_map()

            if dfs:
                self._daily_dfs_cache = dfs
                self._daily_cache_loaded = True
                logger.info(f"Loaded {len(dfs)} stocks daily candle cache from SQLite DB in {time.time() - t0:.2f}s for real-time Chartink scanning.")
        except Exception as e:
            logger.warning(f"Error loading daily candles cache from DB: {e}")

    def startup(
        self,
        force_refresh: bool = False,
        symbols: Optional[List[str]] = None,
        mode: Optional[str] = None,
        universe: Optional[str] = None,
    ):
        """
        Executes complete application startup sequence.
        """
        if mode:
            self.market_mode = mode.upper()
        if universe:
            self.universe_name = universe.upper()
        logger.info(f"Starting Upstox Scanner [Universe: {self.universe_name}, Mode: {self.market_mode}]...")

        # 1. Authenticate / Check credentials
        is_authenticated = self.auth.validate_token()
        rest_status = "CONNECTED" if is_authenticated else "AVAILABLE (PUBLIC ENDPOINTS)"
        analytics_status = "AVAILABLE" if self.auth.has_analytics_token else "NOT CONFIGURED"

        # 2. Load Universe (FNO, NIFTY250, or NIFTY500)
        self._universe = self.instrument_mgr.load_universe(
            universe_name=self.universe_name,
            mode=self.market_mode,
            force_refresh=force_refresh,
        )
        if symbols:
            symbols_set = set(symbols)
            self._universe = {k: v for k, v in self._universe.items() if k in symbols_set}

        fno_count = len(self._universe)
        if fno_count == 0:
            logger.error(f"No instruments discovered for universe {self.universe_name}! Exiting startup.")
            return False

        self.session_mgr.stats.symbols_scanned = fno_count

        # 3. Load Previous Trading Day OHLCV & Calculate Pivots
        pd_data_map = self.hist_loader.load_all_previous_day_ohlcv(self._universe, force_refresh=force_refresh, mode=self.market_mode)
        for sym, pd_item in pd_data_map.items():
            fut_inst = self.instrument_mgr.get_futures_instrument(sym) if hasattr(self, "instrument_mgr") else None
            fut_sym = fut_inst.trading_symbol if fut_inst else f"{sym} FUT"
            lot_sz = fut_inst.lot_size if fut_inst and fut_inst.lot_size else 0
            pivot = calculate_daily_pivots(
                symbol=sym,
                date_str=pd_item.date,
                open_p=pd_item.open,
                high_p=pd_item.high,
                low_p=pd_item.low,
                close_p=pd_item.close,
                volume=pd_item.volume,
                fut_symbol=fut_sym,
                lot_size=lot_sz,
            )
            self._pivots[sym] = pivot
            if self.db:
                self.db.save_daily_levels(pivot.to_dict())

        logger.info(f"Computed pivot levels for {len(self._pivots)} F&O symbols.")

        # Initialize Web Dashboard State & Optional Excel
        dashboard_state.initialize_pivots(self._pivots)
        dashboard_state.update_stats(
            active_universe=self.universe_name,
            market_mode=self.market_mode,
            symbols_scanned=fno_count,
        )
        # 4. Fetch today's historical 5M candles (from 1m history) and initialize multi-timeframe engine
        historical_5m = self.hist_loader.load_initial_5m_candles(self._universe, force_refresh=force_refresh)
        key_map = {sym: item["instrument_key"] for sym, item in self._universe.items()}
        self.candle_engine.initialize_history(historical_5m, key_map=key_map, timeframe="5m")

        # Also seed 3m and 15m from raw broker candles if available
        for tf in ["3m", "15m"]:
            if tf in config.SCANNER_TIMEFRAMES:
                try:
                    tf_dfs = self.hist_loader.refresh_latest_broker_candles(self._universe, timeframe=tf)
                    self.candle_engine.initialize_history(tf_dfs, key_map=key_map, timeframe=tf)
                except Exception as e:
                    logger.debug(f"Could not pre-seed {tf} candles: {e}")

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

        # 6b. Launch background historical gap reconciliation (non-blocking)
        if getattr(config, "ENABLE_HISTORICAL_GAP_FILLER", True) and self.auth.has_access_token:
            def _bg_gap_reconcile():
                try:
                    logger.info("Background worker started: verifying historical database completeness...")
                    filled = self.hist_loader.ensure_historical_candles_complete(self._universe)
                    total_filled = filled.get("filled_candles", 0) if isinstance(filled, dict) else 0
                    if total_filled > 0:
                        logger.info(f"Background gap reconciliation complete: Backfilled {total_filled} candles.")
                    else:
                        logger.info("Background gap reconciliation complete: All historical candles are up-to-date.")
                except Exception as ex:
                    logger.warning(f"Background gap reconciliation encountered an error: {ex}")

            gap_thread = threading.Thread(target=_bg_gap_reconcile, name="HistGapReconcilerThread", daemon=True)
            gap_thread.start()

        # 6c. Restore today's existing Chartink breakout signals from SQLite DB (Strictly Today Only)
        if self.db:
            try:
                today_str = datetime.now(pytz.timezone(config.MARKET_TIMEZONE)).strftime("%Y-%m-%d")
                saved_chartink = self.db.load_chartink_signals(today_str)
                if saved_chartink:
                    dashboard_state.add_chartink_signals_batch(saved_chartink)
                    logger.info(f"Restored {len(saved_chartink)} historical Chartink breakout signals from DB for today ({today_str}).")
            except Exception as e:
                logger.debug(f"Could not load chartink signals from DB: {e}")

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

    def _handle_candle_closed(
        self,
        symbol: str,
        candle: Candle,
        df_history: pd.DataFrame,
        timeframe: str = "5m",
        print_console: bool = True,
    ):
        """
        Invoked when a candle closes (3m, 5m, or 15m).
        Runs pattern detection, pivot context scoring, and triggers deduplicated alerts.
        """
        self.session_mgr.stats.candles_processed += 1
        dashboard_state.update_stats(candles_processed=self.session_mgr.stats.candles_processed)

        # Persist 5-minute candles in SQLite & update Web Dashboard price
        if timeframe == "5m" and self.db:
            self.db.save_candle(candle.to_dict())
        dashboard_state.update_price(symbol, candle.close, candle.volume, candle.timestamp)
        if self.excel_mgr:
            self.excel_mgr.update_price(symbol, candle.close, candle.volume, candle.timestamp)

        # 1. Check trigger confirmation/invalidation for previously pending signals on this symbol
        triggered = self.trigger_tracker.check_candle_triggers(
            symbol=symbol,
            candle_high=candle.high,
            candle_low=candle.low,
            candle_close=candle.close,
            candle_timestamp=candle.timestamp,
        )
        for trig_sig in triggered:
            dashboard_state.update_signal_trigger(
                symbol=symbol,
                timestamp=str(trig_sig.get("timestamp", "")),
                pattern=trig_sig.get("pattern", ""),
                new_status=trig_sig.get("trigger_status", ""),
                trigger_time=trig_sig.get("trigger_time", ""),
            )

        pivots = self._pivots.get(symbol)
        if not pivots:
            return

        # 2. Run multi-factor signal detection on newly closed candle with specific timeframe (if enabled)
        if getattr(config, "ENABLE_TAB1_REVERSAL_SIGNALS", False):
            signals = self.signal_engine.evaluate_candle(symbol, df_history, pivots, timeframe=timeframe)

            for sig in signals:
                self.session_mgr.stats.patterns_detected += 1
                pat_name = sig.pattern
                self.session_mgr.stats.pattern_breakdown[pat_name] = (
                    self.session_mgr.stats.pattern_breakdown.get(pat_name, 0) + 1
                )

                # Deduplication check with timeframe
                if self.dedup.is_duplicate(sig.symbol, sig.timestamp, sig.pattern, timeframe=timeframe):
                    continue

                self.dedup.mark_seen(sig.symbol, sig.timestamp, sig.pattern, timeframe=timeframe)

                if "BULLISH" in sig.direction:
                    self.session_mgr.stats.bullish_signals += 1
                elif "BEARISH" in sig.direction:
                    self.session_mgr.stats.bearish_signals += 1
                    self.session_mgr.stats.hanging_man_signals += 1

                sig_dict = sig.to_dict()
                # Register newly formed signal with trigger tracker
                self.trigger_tracker.register_signal(sig_dict)

                # Output formatted signal card to terminal (if live)
                if print_console:
                    ConsoleFormatter.print_signal(sig)

                # Save signal to database, broadcast to FastAPI Web Dashboard & optional Excel
                if self.db:
                    self.db.save_signal(sig_dict)
                dashboard_state.add_signal(sig_dict)
                if self.excel_mgr:
                    self.excel_mgr.add_signal(sig)

        # 3. Evaluate HEMA + T3 Strategy with Anti-Sideways / Market-Regime Filter
        # Only evaluate on supported HEMA multi-timeframes (e.g. 15m, 30m, 1h, 2h, 4h, 1d)
        if len(df_history) >= 5 and timeframe in config.HEMA_TIMEFRAMES:
            try:
                hema_sig = self.hema_engine.evaluate(df_history, symbol=symbol, timeframe=timeframe)
                if hema_sig:
                    dashboard_state.add_hema_signal(hema_sig.to_dict())
            except Exception as e:
                logger.debug(f"HEMA+T3 evaluation error for {symbol} ({timeframe}): {e}")

    def evaluate_initial_history(self):
        """
        Scans all historical candles (3m, 5m, 15m) from 09:15 up to current time across the universe,
        detecting all pattern & reversal signals that occurred today in chronological sequence,
        and accurately confirming/invalidating their trigger states.
        Automatically kicks off HEMA + T3 multi-timeframe scan at startup.
        """
        if getattr(config, "ENABLE_TAB1_REVERSAL_SIGNALS", False):
            logger.info("Scanning existing intraday candles of today's session across timeframes for reversal setups...")
            total_eval = 0
            
            # Chronological multi-timeframe replay: for each timeframe, replay candle by candle
            for tf in config.SCANNER_TIMEFRAMES:
                engine = self.candle_engine.get_engine(tf)
                if not engine:
                    continue
                for sym, candles in list(engine._history.items()):
                    df_full = engine.get_candle_history_df(sym)
                    if len(candles) >= 2:
                        for i in range(2, len(candles) + 1):
                            sub_candle = candles[i - 1]
                            sub_df = df_full.iloc[:i]
                            self._handle_candle_closed(sym, sub_candle, sub_df, timeframe=tf, print_console=False)
                            total_eval += 1
                            
            logger.info(f"Startup candle scan complete: Evaluated {total_eval} historical candles across timeframes, detected {len(self.dedup._seen_events)} signals.")
        else:
            logger.info("Tab 1 5-Minute Reversal Signals disabled in configuration. Skipping historical reversal replay.")

        # Automatically pre-load daily candles and kick off ultra-fast parallel HEMA + T3 scan and Chartink scan on startup
        try:
            self._load_daily_candles_cache()
            threading.Thread(target=self.scan_hema_universe, daemon=True, name="StartupHemaScan").start()
            threading.Thread(target=self.scan_chartink_universe, daemon=True, name="StartupChartinkScan").start()
        except Exception as e:
            logger.debug(f"Startup scan error: {e}")

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

        # Automatically re-evaluate HEMA + T3 and Chartink strategy across universe on every candle sync
        try:
            threading.Thread(target=self.scan_hema_universe, daemon=True, name="SyncHemaScan").start()
            threading.Thread(target=self.scan_chartink_universe, daemon=True, name="SyncChartinkScan").start()
        except Exception as e:
            logger.debug(f"Sync scan error: {e}")

    def scan_hema_universe(self, timeframes: Optional[List[str]] = None, symbols: Optional[List[str]] = None):
        """
        Evaluates HEMA + T3 Strategy with Anti-Sideways / Market-Regime Filter across multiple timeframes.
        Uses single-pass raw candle fetching, parallel multi-timeframe resampling, and
        Numba-accelerated parallel evaluation with atomic batch state updating (100x faster).
        Protected by re-entrancy lock to prevent CPU/thread saturation.
        """
        if self._is_hema_scanning:
            logger.debug("HEMA scan already running in background. Skipping overlapping request.")
            return (0.0, 0, 0)

        with self._hema_scan_lock:
            self._is_hema_scanning = True
            try:
                if timeframes is None:
                    timeframes = ["15m", "30m", "1h", "2h", "4h", "1d"]
                
                target_universe = self._universe
                if symbols:
                    sym_set = set(symbols)
                    target_universe = {k: v for k, v in self._universe.items() if k in sym_set}
                
                if not target_universe:
                    return (0.0, 0, 0)
                    
                t0 = time.time()
                logger.info(f"Starting ultra-fast parallel HEMA + T3 scan across {len(target_universe)} symbols on {timeframes}...")
                
                # 1. Fetch raw candles once & resample all timeframes in parallel in memory (using candle_engine to avoid disk queries)
                multi_tf_candles = self.hist_loader.load_multi_timeframe_candles(
                    target_universe, timeframes=timeframes, candle_engine=self.candle_engine
                )
                
                # 2. Build task list of all (symbol, timeframe, df) tuples
                tasks = []
                for sym, tf_map in multi_tf_candles.items():
                    inst_info = target_universe.get(sym, {})
                    fut_inst = getattr(self, "instrument_mgr", None)
                    fut = fut_inst.get_futures_instrument(sym) if fut_inst else None
                    fut_sym = fut.trading_symbol if fut else f"{sym} FUT"
                    lot_sz = fut.lot_size if fut and fut.lot_size else inst_info.get("lot_size", 0)
                    t_cr = inst_info.get("turnover_cr", 0.0)
                    l_tier = inst_info.get("liquidity_tier", "Normal")
                    is_liq = inst_info.get("is_most_liquid", False)
                    
                    for tf, df in tf_map.items():
                        if df is not None and len(df) >= 5:
                            tasks.append((sym, tf, df, fut_sym, lot_sz, t_cr, l_tier, is_liq))
                            
                # 3. Parallel evaluation across all session candles
                def _evaluate_worker(task):
                    sym, tf, df, fut_sym, lot_sz, t_cr, l_tier, is_liq = task
                    try:
                        sigs = self.hema_engine.evaluate_all_signals(
                            df,
                            symbol=sym,
                            timeframe=tf,
                            fut_symbol=fut_sym,
                            lot_size=lot_sz,
                            turnover_cr=t_cr,
                            liquidity_tier=l_tier,
                            is_most_liquid=is_liq,
                        )
                        for s in sigs:
                            opt_type = "CE" if "BULLISH" in str(s.signal).upper() or "BUY" in str(s.signal).upper() else "PE"
                            opt = self.instrument_mgr.get_atm_option(sym, s.price, opt_type)
                            if opt:
                                s.option_strike = f"{opt['strike_price']:g} {opt_type}"
                                s.option_symbol = opt["trading_symbol"]
                                s.option_lot_size = opt["lot_size"]
                                s.option_expiry = opt["expiry_date"]
                            else:
                                s.option_strike = "Cash EQ Only"
                        return sigs
                    except Exception as ex:
                        logger.debug(f"HEMA eval error for {sym} {tf}: {ex}")
                        return []

                workers = min(4, os.cpu_count() or 2)
                with ThreadPoolExecutor(max_workers=workers) as executor:
                    sig_lists = list(executor.map(_evaluate_worker, tasks))

                # Flatten all signals across the full day session
                flat_signals = [s for sublist in sig_lists for s in sublist if s is not None]
                valid_signals = [s.to_dict() for s in flat_signals]
                
                # 4. Atomic batch update into DashboardState
                dashboard_state.add_hema_signals_batch(valid_signals)
                elapsed = time.time() - t0
                logger.info(f"HEMA + T3 scan complete: Evaluated {len(tasks)} setups across {len(target_universe)} stocks in {elapsed:.2f}s! Generated {len(valid_signals)} full-day signals.")
                return elapsed, len(tasks), len(valid_signals)

            finally:
                self._is_hema_scanning = False

    def scan_chartink_universe(self, symbols: Optional[List[str]] = None):
        """
        Evaluates Chartink Intraday Screener rules across the F&O universe.
        Checks:
        1. Mandatory Master Filter: ((high + low) / 2) < (typical - typical * 0.003)
        2. Sub 1: SMA(Vol, 20) * Open >= 10 Cr and Monthly Close >= Prev Month High
        3. Sub 2: Weekly Close > 20-week Max Close and Daily Close > Daily SMA(200)
        4. Sub 3: Daily Vol SMA(7) > 100k, Close >= 100, Higher Low, Green Candle,
                  SMA(11..35) crossover, RSI(14) crossover (11..55), Vol >= SMA(5..20)
        """
        if self._is_chartink_scanning:
            logger.debug("Chartink scan already running in background. Skipping overlapping request.")
            return (0.0, 0, 0)

        with self._chartink_scan_lock:
            self._is_chartink_scanning = True
            try:
                self._load_daily_candles_cache()

                target_universe = self._universe
                if symbols:
                    sym_set = set(symbols)
                    target_universe = {k: v for k, v in self._universe.items() if k in sym_set}

                if not target_universe and self._daily_dfs_cache:
                    target_universe = {s: {"symbol": s} for s in self._daily_dfs_cache.keys()}

                if not target_universe:
                    return (0.0, 0, 0)

                now_ist = datetime.now(pytz.timezone(config.MARKET_TIMEZONE))
                today_date = now_ist.date()
                market_open_time = dt_time(9, 15)
                is_market_hours = (now_ist.time() >= market_open_time) and (now_ist.weekday() < 5)

                today_db_candles_map = {}
                if hasattr(self, "db") and self.db:
                    try:
                        today_db_candles_map = self.db.get_candles_by_date(str(today_date))
                    except Exception as e:
                        logger.debug(f"Error querying today_db_candles_map: {e}")
                        today_db_candles_map = {}

                t0 = time.time()
                tasks = []
                for sym, inst_info in target_universe.items():
                    fut_inst = getattr(self, "instrument_mgr", None)
                    fut = fut_inst.get_futures_instrument(sym) if fut_inst else None
                    fut_sym = fut.trading_symbol if fut else f"{sym} FUT"
                    lot_sz = fut.lot_size if fut and fut.lot_size else inst_info.get("lot_size", 0)
                    t_cr = inst_info.get("turnover_cr", 0.0)
                    l_tier = inst_info.get("liquidity_tier", "Normal")
                    is_liq = inst_info.get("is_most_liquid", False)

                    df_daily = self._daily_dfs_cache.get(sym)
                    if df_daily is not None and len(df_daily) >= 15:
                        tasks.append((sym, df_daily, fut_sym, lot_sz, t_cr, l_tier, is_liq))

                if not tasks:
                    return (0.0, 0, 0)

                def _chartink_worker(task):
                    sym, df, fut_sym, lot_sz, t_cr, l_tier, is_liq = task
                    try:
                        df_today_candles = None
                        if is_market_hours:
                            # 1. Check if candle_engine has today's live candles
                            if hasattr(self, "candle_engine") and self.candle_engine:
                                df_5m = self.candle_engine.get_candle_history_df(sym, include_forming=True)
                                if df_5m is not None and not df_5m.empty:
                                    df_today = df_5m[pd.to_datetime(df_5m["timestamp"]).dt.date == today_date]
                                    if not df_today.empty:
                                        df_today_candles = df_today

                            # 2. Fallback to today's 5M candles from SQLite DB
                            if df_today_candles is None and today_db_candles_map:
                                df_db_today = today_db_candles_map.get(sym)
                                if df_db_today is not None and not df_db_today.empty:
                                    df_today_candles = df_db_today

                        # If intraday candle history exists, determine EXACT first detection time
                        if df_today_candles is not None and not df_today_candles.empty:
                            sig = self.chartink_engine.find_first_detection(
                                symbol=sym,
                                df_daily=df,
                                df_today_candles=df_today_candles,
                                fut_symbol=fut_sym,
                                lot_size=lot_sz,
                                turnover_cr=t_cr,
                                liquidity_tier=l_tier,
                                is_most_liquid=is_liq,
                                target_date=today_date,
                            )
                        else:
                            # Fallback to single live streaming quote
                            today_override = None
                            if is_market_hours:
                                lp = dashboard_state.live_prices.get(sym)
                                if lp and lp.get("is_live", False) and lp.get("ltp", 0) > 0:
                                    today_override = {
                                        "timestamp": datetime.now(),
                                        "close": float(lp["ltp"]),
                                        "volume": int(lp.get("volume", 0)),
                                    }

                            sig = self.chartink_engine.evaluate_stock(
                                symbol=sym,
                                df_daily=df,
                                today_override=today_override,
                                fut_symbol=fut_sym,
                                lot_size=lot_sz,
                                turnover_cr=t_cr,
                                liquidity_tier=l_tier,
                                is_most_liquid=is_liq,
                                target_date=today_date,
                            )
                        if sig:
                            opt = self.instrument_mgr.get_atm_option(sym, sig.price, "CE")
                            if opt:
                                sig.option_strike = f"{opt['strike_price']:g} CE"
                                sig.option_symbol = opt["trading_symbol"]
                                sig.option_lot_size = opt["lot_size"]
                                sig.option_expiry = opt["expiry_date"]
                            else:
                                sig.option_strike = "Cash EQ Only"
                        return sig
                    except Exception as ex:
                        logger.warning(f"Chartink eval error for {sym}: {ex}")
                        return None

                workers = min(8, os.cpu_count() or 4)
                with ThreadPoolExecutor(max_workers=workers) as executor:
                    raw_signals = list(executor.map(_chartink_worker, tasks))

                valid_signals = [s.to_dict() for s in raw_signals if s is not None]
                dashboard_state.add_chartink_signals_batch(valid_signals, target_date=str(today_date))
                if valid_signals and hasattr(self, "db") and self.db:
                    try:
                        self.db.save_chartink_signals_batch(valid_signals, str(today_date))
                    except Exception as ex:
                        logger.debug(f"Error persisting chartink signals to DB: {ex}")
                elapsed = time.time() - t0
                if valid_signals:
                    logger.info(f"Chartink scan complete: Evaluated {len(tasks)} stocks in {elapsed:.2f}s! Found {len(valid_signals)} breakout candidates.")
                return elapsed, len(tasks), len(valid_signals)
            finally:
                self._is_chartink_scanning = False

    def _chartink_live_loop(self):
        """Dedicated real-time background monitor loop for Chartink Screener.
        Continuously re-evaluates all 210 stocks every 10 seconds against live streaming quotes."""
        logger.info("Chartink real-time background monitor loop started (every 10s).")
        while self._is_running:
            try:
                time.sleep(10.0)
                if not self._is_running:
                    break
                self.scan_chartink_universe()
            except Exception as e:
                logger.debug(f"Chartink live monitor loop exception: {e}")

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

        # Start real-time background loop for continuous Chartink evaluations
        threading.Thread(target=self._chartink_live_loop, daemon=True, name="ChartinkLiveLoop").start()

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
