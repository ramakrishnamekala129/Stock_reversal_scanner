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
from datetime import datetime
from typing import Dict, List, Optional
import pandas as pd

import config
from database.repository import DatabaseRepository
from excel.live_excel import LiveExcelManager
from indicators.pivots import DailyPivots, calculate_daily_pivots
from indicators.hema_t3 import HemaT3RegimeEngine, HemaT3Signal
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
    ):
        self.auth = auth or UpstoxAuth()
        self.market_mode = market_mode.upper() if market_mode else "FUTURES"
        self.rest_client = UpstoxRestClient(self.auth.get_api_client() if self.auth.has_access_token else None)
        self.instrument_mgr = InstrumentManager(self.rest_client)
        self.hist_loader = HistoricalDataLoader(self.rest_client)
        self.session_mgr = MarketSessionManager()
        self.dedup = EventDeduplicator()
        self.signal_engine = SignalEngine()
        self.hema_engine = HemaT3RegimeEngine()
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

    def startup(self, force_refresh: bool = False, symbols: Optional[List[str]] = None, mode: Optional[str] = None):
        """
        Executes complete application startup sequence.
        """
        if mode:
            self.market_mode = mode.upper()
        logger.info(f"Starting Upstox 5M F&O Intraday Scanner [Mode: {self.market_mode}]...")

        # 1. Authenticate / Check credentials
        is_authenticated = self.auth.validate_token()
        rest_status = "CONNECTED" if is_authenticated else "AVAILABLE (PUBLIC ENDPOINTS)"
        analytics_status = "AVAILABLE" if self.auth.has_analytics_token else "NOT CONFIGURED"

        # 2. Load NSE F&O Universe (Futures or Spot)
        self._universe = self.instrument_mgr.load_fno_universe(force_refresh=force_refresh, mode=self.market_mode)
        if symbols:
            symbols_set = set(symbols)
            self._universe = {k: v for k, v in self._universe.items() if k in symbols_set}

        fno_count = len(self._universe)
        if fno_count == 0:
            logger.error("No F&O instruments discovered! Exiting startup.")
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

        # Automatically kick off ultra-fast parallel HEMA + T3 scan on startup so Tab 4 works out of the box like Tab 1
        try:
            threading.Thread(target=self.scan_hema_universe, daemon=True, name="StartupHemaScan").start()
        except Exception as e:
            logger.debug(f"Startup HEMA scan error: {e}")

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

        # Automatically re-evaluate HEMA + T3 strategy across universe on every candle sync
        try:
            threading.Thread(target=self.scan_hema_universe, daemon=True, name="SyncHemaScan").start()
        except Exception as e:
            logger.debug(f"Sync HEMA scan error: {e}")

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
                            
                # 3. Parallel Numba-accelerated evaluation (8 workers max to avoid GIL / CPU starvation)
                def _evaluate_worker(task):
                    sym, tf, df, fut_sym, lot_sz, t_cr, l_tier, is_liq = task
                    try:
                        sig = self.hema_engine.evaluate(
                            df,
                            symbol=sym,
                            timeframe=tf,
                            fut_symbol=fut_sym,
                            lot_size=lot_sz,
                            turnover_cr=t_cr,
                            liquidity_tier=l_tier,
                            is_most_liquid=is_liq,
                        )
                        return sig
                    except Exception as ex:
                        logger.debug(f"HEMA eval error for {sym} {tf}: {ex}")
                        return None

                workers = min(4, os.cpu_count() or 2)
                with ThreadPoolExecutor(max_workers=workers) as executor:
                    signals = list(executor.map(_evaluate_worker, tasks))

                valid_signals = [s.to_dict() for s in signals if s is not None]
                
                # 4. Atomic batch update into DashboardState
                dashboard_state.add_hema_signals_batch(valid_signals)
                elapsed = time.time() - t0
                logger.info(f"HEMA + T3 scan complete: Evaluated {len(tasks)} setups across {len(target_universe)} stocks in {elapsed:.2f}s! Generated {len(valid_signals)} signals.")
                return elapsed, len(tasks), len(valid_signals)
            finally:
                self._is_hema_scanning = False

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
