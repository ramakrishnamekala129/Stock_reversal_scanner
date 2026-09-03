"""
5-Minute Candle Aggregation Engine.
Aggregates live ticks into strict 5-minute candles aligned to NSE market session (09:15 - 15:30 IST).
Maintains FORMING vs CLOSED state and triggers signal callbacks ONLY upon candle closure.
"""

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from enum import Enum
import logging
from typing import Any, Callable, Dict, List, Optional
import pandas as pd
import pytz

import config
from upstox.websocket import NormalizedTick

logger = logging.getLogger(__name__)


class CandleStatus(str, Enum):
    FORMING = "FORMING"
    CLOSED = "CLOSED"


@dataclass
class Candle:
    """Structure representing a 5-minute OHLCV candle."""
    symbol: str
    instrument_key: str
    timestamp: datetime  # Candle start timestamp (e.g. 09:15:00)
    open: float
    high: float
    low: float
    close: float
    volume: int
    status: CandleStatus = CandleStatus.FORMING
    tick_count: int = 0

    def is_valid(self) -> bool:
        """Validates OHLC sanity."""
        return (
            self.open > 0
            and self.high >= max(self.open, self.close)
            and self.low <= min(self.open, self.close)
            and self.low > 0
            and self.volume >= 0
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "instrument_key": self.instrument_key,
            "timestamp": self.timestamp.isoformat() if isinstance(self.timestamp, datetime) else str(self.timestamp),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "is_closed": self.status == CandleStatus.CLOSED,
        }


class CandleEngine:
    """
    Real-time 5-minute candle aggregator.
    Maintains active forming candles and rolling closed history for each symbol.
    """

    def __init__(
        self,
        on_candle_closed: Optional[Callable[[str, Candle, pd.DataFrame], None]] = None,
        candle_duration_minutes: int = config.CANDLE_DURATION_MINUTES,
    ):
        self.on_candle_closed = on_candle_closed
        self.duration_minutes = candle_duration_minutes
        self.tz = pytz.timezone(config.MARKET_TIMEZONE)

        # symbol -> current forming Candle
        self._forming_candles: Dict[str, Candle] = {}
        # symbol -> list of closed Candle instances
        self._history: Dict[str, List[Candle]] = {}
        # Track last cumulative volume seen to calculate incremental candle volume
        self._last_cum_volume: Dict[str, int] = {}

    def initialize_history(self, symbol_dfs: Dict[str, pd.DataFrame], key_map: Optional[Dict[str, str]] = None):
        """
        Seeds candle history from initial historical DataFrames without triggering closed callbacks.
        """
        callback = self.on_candle_closed
        self.on_candle_closed = None  # Temporarily disable callback during seeding
        try:
            for sym, df in symbol_dfs.items():
                self.sync_broker_candles(sym, df, key_map=key_map)
        finally:
            self.on_candle_closed = callback
        logger.info(f"Initialized candle history for {len(self._history)} symbols directly from broker.")
    def sync_broker_candles(self, symbol: str, broker_df: pd.DataFrame, key_map: Optional[Dict[str, str]] = None):
        """
        Syncs official broker-side 5M candles into the history and dispatches on_candle_closed for newly closed candles.
        """
        if broker_df.empty:
            return

        inst_key = key_map.get(symbol, "") if key_map else ""
        existing_history = self._history.get(symbol, [])
        last_known_ts = existing_history[-1].timestamp if existing_history else None

        updated_list = []
        newly_closed = []

        for _, row in broker_df.iterrows():
            ts = row["timestamp"]
            if isinstance(ts, pd.Timestamp):
                ts = ts.to_pydatetime()
            if ts.tzinfo is None:
                ts = self.tz.localize(ts)
            else:
                ts = ts.astimezone(self.tz)

            candle = Candle(
                symbol=symbol,
                instrument_key=inst_key,
                timestamp=ts,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=int(row["volume"]),
                status=CandleStatus.CLOSED,
                tick_count=1,
            )
            if candle.is_valid():
                updated_list.append(candle)
                if last_known_ts is None or ts > last_known_ts:
                    newly_closed.append(candle)

        if updated_list:
            self._history[symbol] = updated_list[-100:]

        # Dispatch on_candle_closed for new official broker candles
        if self.on_candle_closed and newly_closed:
            df_full = self.get_candle_history_df(symbol)
            for c in newly_closed:
                try:
                    self.on_candle_closed(symbol, c, df_full)
                except Exception as e:
                    logger.error(f"Error in on_candle_closed callback for {symbol}: {e}", exc_info=True)

    def get_candle_history_df(self, symbol: str, include_forming: bool = False) -> pd.DataFrame:
        """
        Returns closed candle history for symbol as a pandas DataFrame.
        If include_forming is True, also appends the current forming candle if valid.
        """
        candles = list(self._history.get(symbol, []))
        forming = self._forming_candles.get(symbol) if include_forming else None
        if forming and forming.is_valid():
            candles.append(forming)
        if not candles:
            return pd.DataFrame()

        data = [
            {
                "timestamp": c.timestamp,
                "open": c.open,
                "high": c.high,
                "low": c.low,
                "close": c.close,
                "volume": c.volume,
                "is_forming": c.status == CandleStatus.FORMING,
            }
            for c in candles
        ]
        df = pd.DataFrame(data).sort_values("timestamp").reset_index(drop=True)
        return df

    def get_candle_start_time(self, dt: datetime) -> datetime:
        """
        Calculates the candle start boundary for a given timestamp in IST.
        Session starts at 09:15.
        Intervals properly align from 09:15 boundary for any duration (3m, 5m, 15m).
        """
        dt_ist = dt.astimezone(self.tz) if dt.tzinfo else self.tz.localize(dt)
        # Calculate total minutes elapsed since midnight
        total_minutes = dt_ist.hour * 60 + dt_ist.minute
        session_open_minutes = 9 * 60 + 15  # 555 minutes (09:15)

        if total_minutes >= session_open_minutes:
            diff = total_minutes - session_open_minutes
            bucket_offset = (diff // self.duration_minutes) * self.duration_minutes
            bucket_total_minutes = session_open_minutes + bucket_offset
        else:
            # Fallback for pre-session timestamps
            bucket_total_minutes = (total_minutes // self.duration_minutes) * self.duration_minutes

        bucket_hour = bucket_total_minutes // 60
        bucket_min = bucket_total_minutes % 60
        candle_start = dt_ist.replace(hour=bucket_hour, minute=bucket_min, second=0, microsecond=0)
        return candle_start

    def process_tick(self, tick: NormalizedTick):
        """
        Processes an incoming live tick.
        Updates or closes 5-minute candle buckets.
        """
        symbol = tick.symbol
        ts = tick.timestamp.astimezone(self.tz) if tick.timestamp.tzinfo else self.tz.localize(tick.timestamp)

        # Ignore ticks outside active NSE market hours (09:15 to 15:30 IST)
        # Prevents post-market settlement ticks (15:40 - 16:00) from creating phantom candles
        t = ts.time()
        if t < time(9, 15) or t > time(15, 30):
            return

        candle_start = self.get_candle_start_time(ts)

        # Track cumulative volume per symbol across ticks
        prev_cum_vol = self._last_cum_volume.get(symbol)
        
        if prev_cum_vol is None:
            # First tick seen for this symbol: initialize baseline
            self._last_cum_volume[symbol] = tick.volume
            inc_volume = 0 if tick.volume > 0 else 0
        else:
            if tick.volume >= prev_cum_vol:
                inc_volume = tick.volume - prev_cum_vol
            else:
                inc_volume = tick.volume
            self._last_cum_volume[symbol] = tick.volume

        current_forming = self._forming_candles.get(symbol)

        if current_forming is None:
            # First candle for this symbol
            self._forming_candles[symbol] = Candle(
                symbol=symbol,
                instrument_key=tick.instrument_key,
                timestamp=candle_start,
                open=tick.ltp,
                high=tick.ltp,
                low=tick.ltp,
                close=tick.ltp,
                volume=inc_volume,
                status=CandleStatus.FORMING,
                tick_count=1,
            )
            return

        # Check if the tick belongs to a new 5-minute candle period
        if candle_start > current_forming.timestamp:
            # 1. Close the previous forming candle
            current_forming.status = CandleStatus.CLOSED
            
            if current_forming.is_valid():
                if symbol not in self._history:
                    self._history[symbol] = []
                self._history[symbol].append(current_forming)
                
                # Keep rolling window to avoid unbounded memory growth (keep last 100 5m candles)
                if len(self._history[symbol]) > 100:
                    self._history[symbol] = self._history[symbol][-100:]

                # Trigger the on_candle_closed callback
                if self.on_candle_closed:
                    df = self.get_candle_history_df(symbol)
                    try:
                        self.on_candle_closed(symbol, current_forming, df)
                    except Exception as e:
                        logger.error(f"Error in on_candle_closed callback for {symbol}: {e}", exc_info=True)
            else:
                logger.warning(f"Discarding invalid candle for {symbol}: {current_forming}")

            # 2. Start new forming candle
            self._forming_candles[symbol] = Candle(
                symbol=symbol,
                instrument_key=tick.instrument_key,
                timestamp=candle_start,
                open=tick.ltp,
                high=tick.ltp,
                low=tick.ltp,
                close=tick.ltp,
                volume=inc_volume,
                status=CandleStatus.FORMING,
                tick_count=1,
            )
        elif candle_start == current_forming.timestamp:
            # Update current forming candle
            current_forming.high = max(current_forming.high, tick.ltp)
            current_forming.low = min(current_forming.low, tick.ltp)
            current_forming.close = tick.ltp
            current_forming.volume += inc_volume
            current_forming.tick_count += 1
        else:
            # Tick from older timestamp (late/delayed tick), ignore for forming candle
            logger.debug(f"Received delayed tick for {symbol} timestamp {ts} < current candle {current_forming.timestamp}")

    def force_close_active_candles(self):
        """
        Forces all active forming candles to close (useful at market close 15:30).
        """
        for symbol, candle in list(self._forming_candles.items()):
            if candle and candle.is_valid():
                candle.status = CandleStatus.CLOSED
                if symbol not in self._history:
                    self._history[symbol] = []
                self._history[symbol].append(candle)
                if self.on_candle_closed:
                    df = self.get_candle_history_df(symbol)
                    try:
                        self.on_candle_closed(symbol, candle, df)
                    except Exception as e:
                        logger.error(f"Error in on_candle_closed callback for {symbol}: {e}")
        self._forming_candles.clear()


class MultiTimeframeCandleEngine:
    """
    Multi-Timeframe Real-time Candle Aggregator.
    Runs 3-minute, 5-minute, and 15-minute CandleEngines concurrently from the same tick stream.
    """

    def __init__(
        self,
        timeframes: Optional[List[str]] = None,
        on_candle_closed: Optional[Callable[[str, Candle, pd.DataFrame, str], None]] = None,
    ):
        self.timeframe_keys = timeframes or config.SCANNER_TIMEFRAMES
        self.on_candle_closed = on_candle_closed
        self.engines: Dict[str, CandleEngine] = {}

        for tf in self.timeframe_keys:
            minutes = config.TIMEFRAME_MINUTES.get(tf, 5)

            # Create closure capturing current tf
            def _make_callback(tf_name: str):
                return lambda sym, candle, df: self._handle_sub_candle_closed(sym, candle, df, tf_name)

            engine = CandleEngine(
                on_candle_closed=_make_callback(tf),
                candle_duration_minutes=minutes,
            )
            self.engines[tf] = engine

    def _handle_sub_candle_closed(self, symbol: str, candle: Candle, df: pd.DataFrame, timeframe: str):
        if self.on_candle_closed:
            try:
                self.on_candle_closed(symbol, candle, df, timeframe)
            except Exception as e:
                logger.error(f"Error in multi-timeframe candle closed callback for {symbol} ({timeframe}): {e}", exc_info=True)

    def process_tick(self, tick: NormalizedTick):
        """Dispatches incoming tick to all active timeframe candle engines."""
        for engine in self.engines.values():
            engine.process_tick(tick)

    def get_engine(self, timeframe: str = "5m") -> CandleEngine:
        return self.engines.get(timeframe, self.engines.get("5m"))

    def get_candle_history_df(self, symbol: str, timeframe: str = "5m", include_forming: bool = False) -> pd.DataFrame:
        engine = self.get_engine(timeframe)
        if engine:
            return engine.get_candle_history_df(symbol, include_forming=include_forming)
        return pd.DataFrame()

    def initialize_history(
        self,
        symbol_dfs: Dict[str, pd.DataFrame],
        key_map: Optional[Dict[str, str]] = None,
        timeframe: str = "5m",
    ):
        """Seeds candle history for a given timeframe engine."""
        engine = self.get_engine(timeframe)
        if engine:
            engine.initialize_history(symbol_dfs, key_map=key_map)

    def sync_broker_candles(
        self,
        symbol: str,
        broker_df: pd.DataFrame,
        key_map: Optional[Dict[str, str]] = None,
        timeframe: str = "5m",
    ):
        """Syncs broker candles for a specific timeframe."""
        engine = self.get_engine(timeframe)
        if engine:
            engine.sync_broker_candles(symbol, broker_df, key_map=key_map)

    @property
    def _history(self) -> Dict[str, List[Candle]]:
        """Returns the primary 5m candle history dictionary."""
        eng_5m = self.get_engine("5m")
        return eng_5m._history if eng_5m else {}

    def force_close_active_candles(self):
        for engine in self.engines.values():
            engine.force_close_active_candles()
