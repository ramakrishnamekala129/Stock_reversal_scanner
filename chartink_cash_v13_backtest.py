"""Three-month, 5-minute backtest for the Chartink Cash V1.3 scanner.

The module deliberately accepts and produces native 5-minute candles only.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, time as dt_time, timedelta
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from time import perf_counter
from typing import Callable, Dict, List, Optional, Tuple, Union

import numba as nb
import numpy as np
import pandas as pd
try:
    import polars as pl
except ImportError:
    pl = None


SCANNER_URL = "https://chartink.com/screener/intraday-screener-cash-v1-3"
BACKTEST_MONTHS = 3
TIMEFRAME = "5min"
TIMEFRAME_MINUTES = 5
VOLUME_SMA_LENGTH = 20
MIN_AVERAGE_DAILY_VOLUME = 100_000.0
INSTITUTIONAL_VOLUME_MULTIPLIER = 100_000.0
MIN_TURNOVER_CASH_V13 = 10_000_000.0  # Chartink: Daily SMA(volume, 20) * Daily Open >= 10,000,000 (1 Crore)
INSTITUTIONAL_BRANCH_REACHABLE = False


def three_month_window(as_of: Optional[date] = None) -> Tuple[date, date]:
    """Return the inclusive three-calendar-month window ending on *as_of*."""
    end = pd.Timestamp(as_of or date.today()).normalize()
    start = end - pd.DateOffset(months=BACKTEST_MONTHS)
    return start.date(), end.date()


def resolve_backtest_window(
    mode: str = "current_day",
    as_of: Optional[date] = None,
    available_dates: Optional[List[date]] = None,
    live_session: Optional[bool] = None,
) -> Tuple[date, date]:
    """Return (start_date, end_date) for the given backtest mode.

    Modes:
      - 'current_day' (default): Today during live market hours (>= 09:15 IST), else latest completed session.
      - '3_months': Full 3-calendar-month window ending on as_of (or today / latest completed session).
    """
    import pytz
    now_ist = datetime.now(pytz.timezone("Asia/Kolkata"))
    is_live_trading_today = (
        now_ist.weekday() < 5
        and now_ist.time() >= dt_time(9, 15)
    )

    if as_of is not None:
        target_date = as_of
    elif live_session is True:
        target_date = now_ist.date()
    elif live_session is False:
        target_date = max(available_dates) if available_dates else now_ist.date()
    elif is_live_trading_today:
        target_date = now_ist.date()
    elif available_dates:
        target_date = max(available_dates)
    else:
        target_date = date.today()

    normalized_mode = str(mode or "").strip().lower().replace(" ", "_")
    if normalized_mode in ("current_day", "today", "1d", "1_day"):
        return target_date, target_date
    start = (pd.Timestamp(target_date) - pd.DateOffset(months=BACKTEST_MONTHS)).date()
    return start, target_date


def normalize_to_5minute(candles: pd.DataFrame) -> pd.DataFrame:
    """Validate OHLCV input and return NSE-session-aligned 5-minute candles."""
    required = {"timestamp", "open", "high", "low", "close", "volume"}
    missing = required.difference(candles.columns)
    if missing:
        raise ValueError(f"Missing candle columns: {', '.join(sorted(missing))}")
    if candles.empty:
        return pd.DataFrame(columns=list(required))

    frame = candles[list(required)].copy()
    try:
        frame["timestamp"] = pd.to_datetime(frame["timestamp"])
    except (TypeError, ValueError):
        normalized_timestamps = []
        for value in frame["timestamp"]:
            stamp = pd.Timestamp(value)
            if stamp.tzinfo is None:
                stamp = stamp.tz_localize("Asia/Kolkata")
            else:
                stamp = stamp.tz_convert("Asia/Kolkata")
            normalized_timestamps.append(stamp)
        frame["timestamp"] = pd.DatetimeIndex(normalized_timestamps)
    frame = frame.sort_values("timestamp").drop_duplicates("timestamp", keep="last")
    median_spacing = None
    if len(frame) > 1:
        spacing = frame["timestamp"].diff().dropna().dt.total_seconds() / 60.0
        if not spacing.empty:
            median_spacing = float(spacing.median())
        if median_spacing is not None and median_spacing < TIMEFRAME_MINUTES:
            raise ValueError("Cash V1.3 accepts native candles of 5 minutes or higher only")
    if median_spacing == TIMEFRAME_MINUTES:
        return frame.reset_index(drop=True)
    # 09:15 is the NSE cash-session origin.
    indexed = frame.set_index("timestamp")
    result = indexed.resample(
        TIMEFRAME, origin="start_day", offset="15min", closed="left", label="left"
    ).agg({
        "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"
    }).dropna().reset_index()
    return result


def _rsi(close: pd.Series, length: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / length, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / length, adjust=False).mean()
    return (100 - 100 / (1 + gain / loss.replace(0, np.nan))).fillna(50.0)


def cash_v13_signal(
    day_bars: pd.DataFrame,
    prior_daily: pd.DataFrame,
    fii_percentage_change: Optional[float] = None,
) -> Optional[str]:
    """Evaluate the Cash V1.3 branches using OHLCV and quarterly FII change."""
    if day_bars.empty or len(prior_daily) < 20:
        return None

    current = day_bars.iloc[-1]
    d_open = float(day_bars.iloc[0]["open"])
    d_high = float(day_bars["high"].max())
    d_low = float(day_bars["low"].min())
    d_close = float(current["close"])
    d_volume = float(day_bars["volume"].sum())

    median = (d_high + d_low) / 2.0
    typical = (d_high + d_low + d_close) / 3.0
    if not median < typical * 0.997:
        return None

    hist = prior_daily.copy().sort_values("timestamp")
    hist["timestamp"] = pd.to_datetime(hist["timestamp"]).dt.tz_localize(None)
    volume_window = hist["volume"].tail(19).astype(float).tolist() + [d_volume]
    sma_volume_20 = float(np.mean(volume_window))

    current_ts = pd.Timestamp(current["timestamp"])
    if current_ts.tzinfo is not None:
        current_ts = current_ts.tz_localize(None)
    current_month = current_ts.to_period("M")
    previous_month = current_month - 1
    prev_month_rows = hist[hist["timestamp"].dt.to_period("M") == previous_month]
    monthly_breakout = (
        not prev_month_rows.empty
        and d_close >= float(prev_month_rows["high"].max())
        and sma_volume_20 * d_open >= MIN_TURNOVER_CASH_V13
    )
    if monthly_breakout:
        return "Monthly Breakout (Cash V1.3)"

    if fii_percentage_change is None:
        return None

    if len(hist) < 100:
        return None
    weekly = hist.set_index("timestamp")["close"].resample("W-FRI").last().dropna()
    weekly_breakout = len(weekly) >= 20 and d_close > float(weekly.tail(20).max())
    sma_200 = float(hist["close"].tail(200).mean())

    daily_plus = pd.concat([
        hist.tail(40),
        pd.DataFrame([{
            "timestamp": current["timestamp"], "open": d_open, "high": d_high,
            "low": d_low, "close": d_close, "volume": d_volume,
        }]),
    ], ignore_index=True)
    prev_close = daily_plus["close"].shift(1)
    true_range = pd.concat([
        daily_plus["high"] - daily_plus["low"],
        (daily_plus["high"] - prev_close).abs(),
        (daily_plus["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = true_range.rolling(14).mean()
    atr_expanding = len(atr.dropna()) >= 2 and atr.iloc[-1] > atr.iloc[-2]

    up_move = daily_plus["high"].diff()
    down_move = -daily_plus["low"].diff()
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)
    atr_sum = true_range.rolling(14).sum().replace(0, np.nan)
    plus_di = 100 * plus_dm.rolling(14).sum() / atr_sum
    minus_di = 100 * minus_dm.rolling(14).sum() / atr_sum
    dx = (100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
    adx = float(dx.rolling(14).mean().fillna(0).iloc[-1])

    typical_series = (daily_plus["high"] + daily_plus["low"] + daily_plus["close"]) / 3
    money_flow = typical_series * daily_plus["volume"]
    direction = typical_series.diff()
    pos = money_flow.where(direction > 0, 0).rolling(14).sum()
    neg = money_flow.where(direction < 0, 0).rolling(14).sum().replace(0, np.nan)
    mfi = (100 - 100 / (1 + pos / neg)).fillna(50.0).iloc[-1]

    close_5m = day_bars["close"].astype(float)
    ema13 = close_5m.ewm(span=13, adjust=False).mean()
    intraday_trend = len(ema13) >= 13 and ema13.iloc[-1] > ema13.rolling(13).mean().iloc[-1]
    gann_level = (np.sqrt(max(d_open, 0.0)) + 0.125) ** 2
    liquid_volume = (
        sma_volume_20 >= MIN_AVERAGE_DAILY_VOLUME
        and d_volume > sma_volume_20 * INSTITUTIONAL_VOLUME_MULTIPLIER
    )
    fii_growth = fii_percentage_change is not None and fii_percentage_change > 1.5

    if all((weekly_breakout, d_close > sma_200, d_close > adx, mfi > 60, atr_expanding,
            intraday_trend, d_close >= gann_level, 350 < d_close < 3000,
            liquid_volume, fii_growth)):
        return "Institutional Momentum (Cash V1.3)"
    return None


@dataclass
class BacktestTrade:
    symbol: str
    signal_date: str
    entry_time: str
    exit_time: str
    strategy: str
    entry_price: float
    exit_price: float
    pnl_pct: float
    exit_reason: str
    timeframe: str = "5min"


@dataclass
class _DailyHistoryIndex:
    frame: pd.DataFrame
    dates: np.ndarray
    volume_prefix: np.ndarray
    monthly_highs: Dict[pd.Period, float]

    @classmethod
    def build(cls, daily: pd.DataFrame) -> "_DailyHistoryIndex":
        frame = daily.copy().sort_values("timestamp").reset_index(drop=True)
        timestamps = pd.to_datetime(frame["timestamp"]).dt.tz_localize(None)
        frame["timestamp"] = timestamps
        dates = timestamps.to_numpy(dtype="datetime64[D]")
        volumes = frame["volume"].astype(float).to_numpy()
        volume_prefix = np.concatenate(([0.0], np.cumsum(volumes)))
        periods = timestamps.dt.to_period("M")
        monthly_highs = (
            frame.assign(_period=periods)
            .groupby("_period", sort=False)["high"]
            .max()
            .astype(float)
            .to_dict()
        )
        return cls(frame, dates, volume_prefix, monthly_highs)

    def context(self, session_date: date) -> Tuple[int, Optional[float], float, pd.DataFrame]:
        position = int(
            np.searchsorted(self.dates, np.datetime64(session_date), side="left")
        )
        start = max(0, position - (VOLUME_SMA_LENGTH - 1))
        prior_volume_sum = float(self.volume_prefix[position] - self.volume_prefix[start])
        previous_month = pd.Period(session_date, freq="M") - 1
        previous_high = self.monthly_highs.get(previous_month)
        prior = self.frame.iloc[:position] if INSTITUTIONAL_BRANCH_REACHABLE else self.frame.iloc[:0]
        return position, previous_high, prior_volume_sum, prior


def _first_signal_index_for_day(
    day: pd.DataFrame,
    prior_count: int,
    previous_high: Optional[float],
    prior_volume_sum: float,
    fii_percentage_change: Optional[float],
    prior_daily: Optional[pd.DataFrame] = None,
) -> Tuple[Optional[int], Optional[str], bool]:
    """Find the first signal while precomputing the common monthly branch."""
    if day.empty or prior_count < VOLUME_SMA_LENGTH:
        return None, None, False

    day_open = float(day.iloc[0]["open"])
    closes = day["close"].astype(float)
    total_volume = float(day["volume"].astype(float).sum())
    monthly_possible = (
        previous_high is not None
        and float(closes.max()) >= previous_high
        and ((prior_volume_sum + total_volume) / VOLUME_SMA_LENGTH) * day_open
        >= MIN_TURNOVER_CASH_V13
    )
    institutional_possible = (
        INSTITUTIONAL_BRANCH_REACHABLE
        and fii_percentage_change is not None
        and fii_percentage_change > 1.5
        and prior_count >= 100
    )
    if not monthly_possible and not institutional_possible:
        return None, None, False

    highs = day["high"].astype(float).cummax()
    lows = day["low"].astype(float).cummin()
    volumes = day["volume"].astype(float).cumsum()

    timestamps = pd.to_datetime(day["timestamp"])
    clock = timestamps.dt.hour * 60 + timestamps.dt.minute
    eligible = (clock >= 9 * 60 + 20) & (clock <= 14 * 60 + 30)
    median = (highs + lows) / 2.0
    typical = (highs + lows + closes) / 3.0
    common_filter = median < typical * 0.997
    sma_volume_20 = (prior_volume_sum + volumes) / float(VOLUME_SMA_LENGTH)
    monthly_filter = eligible & common_filter
    if previous_high is None:
        monthly_filter &= False
    else:
        monthly_filter &= closes >= previous_high
    monthly_filter &= sma_volume_20 * day_open >= MIN_TURNOVER_CASH_V13
    matching = np.flatnonzero(monthly_filter.to_numpy(dtype=bool))
    if matching.size:
        return int(matching[0]), "Monthly Breakout (Cash V1.3)", True

    if not INSTITUTIONAL_BRANCH_REACHABLE:
        return None, None, True
    if fii_percentage_change is None or fii_percentage_change <= 1.5 or prior_count < 100:
        return None, None, True

    hist = prior_daily if prior_daily is not None else pd.DataFrame()
    for idx in np.flatnonzero((eligible & common_filter).to_numpy(dtype=bool)):
        strategy = cash_v13_signal(
            day.iloc[:idx + 1], hist, fii_percentage_change=fii_percentage_change
        )
        if strategy:
            return int(idx), strategy, True
    return None, None, True


@nb.njit(fastmath=True)
def _evaluate_symbol_numba(
    split_indices: np.ndarray,
    opens: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    volumes: np.ndarray,
    minutes: np.ndarray,
    day_prior_counts: np.ndarray,
    day_prev_highs: np.ndarray,
    day_prior_vols: np.ndarray,
):
    n_days = len(split_indices) - 1
    candidate_stock_days = 0
    candidate_bars = 0
    triggers_day_idx = []
    triggers_local_idx = []

    for d in range(n_days):
        p_count = day_prior_counts[d]
        prev_h = day_prev_highs[d]
        prior_vol = day_prior_vols[d]

        if p_count < 20 or np.isnan(prev_h):
            continue

        start_i = split_indices[d]
        end_i = split_indices[d + 1]
        if end_i <= start_i:
            continue

        day_open = opens[start_i]
        day_max_close = -1e9
        day_tot_vol = 0.0
        for i in range(start_i, end_i):
            if closes[i] > day_max_close:
                day_max_close = closes[i]
            day_tot_vol += volumes[i]

        if day_max_close < prev_h:
            continue
        if ((prior_vol + day_tot_vol) / 20.0) * day_open < MIN_TURNOVER_CASH_V13:
            continue

        candidate_stock_days += 1

        run_h = -1e9
        run_l = 1e9
        run_v = 0.0
        triggered = False

        for i in range(start_i, end_i):
            m = minutes[i]
            if 560 <= m <= 870:  # 09:20 to 14:30
                candidate_bars += 1

            if highs[i] > run_h:
                run_h = highs[i]
            if lows[i] < run_l:
                run_l = lows[i]
            run_v += volumes[i]

            if not triggered and 560 <= m <= 870:
                median = (run_h + run_l) * 0.5
                typical = (run_h + run_l + closes[i]) / 3.0
                if median < typical * 0.997:
                    if closes[i] >= prev_h:
                        if ((prior_vol + run_v) / 20.0) * day_open >= MIN_TURNOVER_CASH_V13:
                            triggers_day_idx.append(d)
                            triggers_local_idx.append(i - start_i)
                            triggered = True

    return candidate_stock_days, candidate_bars, triggers_day_idx, triggers_local_idx


@nb.njit(fastmath=True)
def _simulate_trade_numba(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    minutes: np.ndarray,
    trigger_idx: int,
    target_pct: float,
    stop_pct: float,
    eod_minute: int,
):
    entry = closes[trigger_idx]
    target = entry * (1.0 + target_pct / 100.0)
    stop = entry * (1.0 - stop_pct / 100.0)
    exit_price = entry
    exit_reason = 0  # 0: EOD, 1: STOP LOSS, 2: TARGET
    exit_idx = len(closes) - 1

    for j in range(trigger_idx + 1, len(closes)):
        exit_idx = j
        if lows[j] <= stop:
            exit_price = stop
            exit_reason = 1
            break
        if highs[j] >= target:
            exit_price = target
            exit_reason = 2
            break
        exit_price = closes[j]
        if minutes[j] >= eod_minute:
            break

    return exit_idx, exit_price, exit_reason


def _vectorized_monthly_candidates(
    bars: pd.DataFrame,
    daily_index: _DailyHistoryIndex,
) -> Tuple[int, int, List[Tuple[date, pd.DataFrame, int]]]:
    """Apply the daily gate and monthly 5-minute trigger using Numba-accelerated loops."""
    if bars.empty:
        return 0, 0, []

    work = bars.reset_index(drop=True)
    timestamps = pd.to_datetime(work["timestamp"])
    session_dates = timestamps.dt.date.to_numpy()
    unique_dates, split_indices = np.unique(session_dates, return_index=True)
    split_indices = np.append(split_indices, len(work))

    day_prior_counts = []
    day_prev_highs = []
    day_prior_vols = []
    for d in unique_dates:
        pos, prev_h, prior_v, _ = daily_index.context(d)
        day_prior_counts.append(pos)
        day_prev_highs.append(prev_h if prev_h is not None else np.nan)
        day_prior_vols.append(prior_v)

    day_prior_counts = np.array(day_prior_counts, dtype=np.int32)
    day_prev_highs = np.array(day_prev_highs, dtype=np.float64)
    day_prior_vols = np.array(day_prior_vols, dtype=np.float64)

    opens = work["open"].astype(float).to_numpy()
    highs = work["high"].astype(float).to_numpy()
    lows = work["low"].astype(float).to_numpy()
    closes = work["close"].astype(float).to_numpy()
    volumes = work["volume"].astype(float).to_numpy()
    minutes = (timestamps.dt.hour * 60 + timestamps.dt.minute).to_numpy(dtype=np.int32)

    candidate_days, candidate_bars, triggers_day_idx, triggers_local_idx = _evaluate_symbol_numba(
        split_indices, opens, highs, lows, closes, volumes, minutes,
        day_prior_counts, day_prev_highs, day_prior_vols,
    )

    triggers: List[Tuple[date, pd.DataFrame, int]] = []
    base_columns = ["timestamp", "open", "high", "low", "close", "volume"]
    for day_i, local_i in zip(triggers_day_idx, triggers_local_idx):
        s_date = unique_dates[day_i]
        start_idx = split_indices[day_i]
        end_idx = split_indices[day_i + 1]
        day_df = work.iloc[start_idx:end_idx][base_columns].reset_index(drop=True)
        triggers.append((s_date, day_df, local_i))

    return candidate_days, candidate_bars, triggers


def _prepare_monthly_symbol(
    symbol: str,
    raw: pd.DataFrame,
    daily: Optional[pd.DataFrame],
    start: date,
    end: date,
    candidate_dates: Optional[Set[date]] = None,
) -> Tuple[str, int, int, int, List[Tuple[date, pd.DataFrame, int]]]:
    """Prepare one symbol independently for parallel daily filtering."""
    all_bars = normalize_to_5minute(raw)
    if all_bars.empty:
        return symbol, 0, 0, 0, []
    timestamps = pd.to_datetime(all_bars["timestamp"])
    if candidate_dates is not None:
        bars = all_bars[timestamps.dt.date.isin(candidate_dates)].copy()
    else:
        bars = all_bars[(timestamps.dt.date >= start) & (timestamps.dt.date <= end)].copy()
    if bars.empty:
        return symbol, 0, 0, 0, []

    if daily is None or daily.empty:
        session_dates = timestamps.dt.date
        daily = (
            all_bars.assign(_session_date=session_dates)
            .groupby("_session_date", sort=True)
            .agg({
                "timestamp": "first",
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            })
            .reset_index(drop=True)
        )
    else:
        daily = daily.copy()
        daily["timestamp"] = pd.to_datetime(daily["timestamp"])
    daily_index = _DailyHistoryIndex.build(daily)
    symbol_days = int(pd.to_datetime(bars["timestamp"]).dt.date.nunique())
    shortlisted, candidate_rows, triggers = _vectorized_monthly_candidates(bars, daily_index)
    return symbol, symbol_days, shortlisted, candidate_rows, triggers


try:
    import config
    DEFAULT_BACKTEST_TARGET_PCT = float(getattr(config, "DEFAULT_TARGET_PCT", 5.0))
    DEFAULT_BACKTEST_STOP_PCT = float(getattr(config, "DEFAULT_STOP_LOSS_PCT", 1.0))
except Exception:
    DEFAULT_BACKTEST_TARGET_PCT = 5.0
    DEFAULT_BACKTEST_STOP_PCT = 1.0


class ChartinkCashV13Backtester:
    """Run a fixed three-month intraday backtest using 5-minute decisions."""

    def __init__(
        self,
        target_pct: float = DEFAULT_BACKTEST_TARGET_PCT,
        stop_pct: float = DEFAULT_BACKTEST_STOP_PCT,
        eod_exit: time = time(15, 20),
    ):
        self.target_pct = float(target_pct)
        self.stop_pct = float(stop_pct)
        self.eod_exit = eod_exit

    def run(
        self,
        candles_by_symbol: Dict[str, pd.DataFrame],
        daily_by_symbol: Optional[Dict[str, pd.DataFrame]] = None,
        fii_history_by_symbol: Optional[Dict[str, List[dict]]] = None,
        as_of: Optional[date] = None,
        progress: Optional[Callable[[int, int, str], None]] = None,
        max_workers: int = 1,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        mode: Optional[str] = None,
        candidate_dates_by_symbol: Optional[Dict[str, Set[date]]] = None,
    ) -> Dict[str, object]:
        calculation_started = perf_counter()
        if start_date is not None and end_date is not None:
            start, end = start_date, end_date
        elif mode:
            start, end = resolve_backtest_window(mode, as_of)
        else:
            start, end = three_month_window(as_of)
        daily_by_symbol = daily_by_symbol or {}
        fii_history_by_symbol = fii_history_by_symbol or {}
        trades: List[BacktestTrade] = []
        bars_evaluated = 0
        stock_days_total = 0
        candidate_stock_days = 0

        total_symbols = len(candles_by_symbol)
        source_items = list(candles_by_symbol.items())
        if not INSTITUTIONAL_BRANCH_REACHABLE:
            completed = 0
            with ThreadPoolExecutor(max_workers=max(1, min(max_workers, 8))) as pool:
                futures = {
                    pool.submit(
                        _prepare_monthly_symbol,
                        symbol,
                        raw,
                        daily_by_symbol.get(symbol),
                        start,
                        end,
                        (candidate_dates_by_symbol.get(symbol) if candidate_dates_by_symbol else None),
                    ): symbol
                    for symbol, raw in source_items
                }
                for future in as_completed(futures):
                    symbol = futures[future]
                    prepared_symbol, symbol_days, shortlisted, candidate_rows, triggers = future.result()
                    stock_days_total += symbol_days
                    candidate_stock_days += shortlisted
                    bars_evaluated += candidate_rows
                    for session_date, day, trigger_idx in triggers:
                        trades.append(self._simulate(
                            prepared_symbol,
                            session_date,
                            day,
                            trigger_idx,
                            "Monthly Breakout (Cash V1.3)",
                        ))
                    completed += 1
                    if progress:
                        progress(completed, total_symbols, symbol)
            source_items = []

        for symbol_index, (symbol, raw) in enumerate(source_items, start=1):
            all_bars = normalize_to_5minute(raw)
            if all_bars.empty:
                if progress:
                    progress(symbol_index, total_symbols, symbol)
                continue
            ts = pd.to_datetime(all_bars["timestamp"])
            bars = all_bars[(ts.dt.date >= start) & (ts.dt.date <= end)].copy()
            if bars.empty:
                if progress:
                    progress(symbol_index, total_symbols, symbol)
                continue

            daily = daily_by_symbol.get(symbol)
            if daily is None or daily.empty:
                session_dates = pd.to_datetime(all_bars["timestamp"]).dt.date
                daily = (
                    all_bars.assign(_session_date=session_dates)
                    .groupby("_session_date", sort=True)
                    .agg({
                        "timestamp": "first",
                        "open": "first",
                        "high": "max",
                        "low": "min",
                        "close": "last",
                        "volume": "sum",
                    })
                    .reset_index(drop=True)
                )
            else:
                daily = daily.copy()
                daily["timestamp"] = pd.to_datetime(daily["timestamp"])
            daily_index = _DailyHistoryIndex.build(daily)

            if not INSTITUTIONAL_BRANCH_REACHABLE:
                symbol_days = int(pd.to_datetime(bars["timestamp"]).dt.date.nunique())
                stock_days_total += symbol_days
                shortlisted, candidate_rows, triggers = _vectorized_monthly_candidates(
                    bars, daily_index
                )
                candidate_stock_days += shortlisted
                bars_evaluated += candidate_rows
                for session_date, day, trigger_idx in triggers:
                    trades.append(self._simulate(
                        symbol,
                        session_date,
                        day,
                        trigger_idx,
                        "Monthly Breakout (Cash V1.3)",
                    ))
                if progress:
                    progress(symbol_index, total_symbols, symbol)
                continue

            bar_session_dates = pd.to_datetime(bars["timestamp"]).dt.date
            for session_date, day in bars.groupby(bar_session_dates, sort=False):
                stock_days_total += 1
                day = day.sort_values("timestamp").reset_index(drop=True)
                prior_count, previous_high, prior_volume_sum, prior = daily_index.context(
                    session_date
                )
                fii_change = quarterly_fii_change_for_date(
                    fii_history_by_symbol.get(symbol, []), session_date
                )
                trigger_idx, strategy, passed_daily_filter = _first_signal_index_for_day(
                    day,
                    prior_count,
                    previous_high,
                    prior_volume_sum,
                    fii_change,
                    prior_daily=prior,
                )
                if passed_daily_filter:
                    candidate_stock_days += 1
                    eligible = pd.to_datetime(day["timestamp"]).dt.time.between(
                        time(9, 20), time(14, 30)
                    )
                    bars_evaluated += int(eligible.sum())
                if trigger_idx is None:
                    continue
                trades.append(self._simulate(symbol, session_date, day, trigger_idx, strategy))
            if progress:
                progress(symbol_index, total_symbols, symbol)

        trades.sort(key=lambda trade: (trade.signal_date, trade.symbol, trade.entry_time))
        rows = [asdict(t) for t in trades]
        pnls = [t.pnl_pct for t in trades]
        wins = sum(p > 0 for p in pnls)
        gross_profit = sum(max(p, 0) for p in pnls)
        gross_loss = abs(sum(min(p, 0) for p in pnls))
        return {
            "scanner_url": SCANNER_URL,
            "mode": "current_day" if start == end else "3_months",
            "from_date": start.isoformat(),
            "to_date": end.isoformat(),
            "months": 0 if start == end else BACKTEST_MONTHS,
            "timeframe": TIMEFRAME,
            "minimum_timeframe_minutes": TIMEFRAME_MINUTES,
            "fii_data_status": (
                "skipped_institutional_branch_unreachable"
                if not INSTITUTIONAL_BRANCH_REACHABLE
                else f"upstox_quarterly_shareholdings_{len(fii_history_by_symbol)}_symbols"
                if fii_history_by_symbol
                else "upstox_quarterly_shareholdings_unavailable"
            ),
            "symbols_tested": len(candles_by_symbol),
            "bars_evaluated": bars_evaluated,
            "stock_days_total": stock_days_total,
            "daily_candidate_stock_days": candidate_stock_days,
            "daily_prefilter_rejected_stock_days": stock_days_total - candidate_stock_days,
            "daily_prefilter_reduction_pct": round(
                (stock_days_total - candidate_stock_days) / stock_days_total * 100.0, 2
            ) if stock_days_total else 0.0,
            "calculation_seconds": round(perf_counter() - calculation_started, 2),
            "institutional_branch_reachable": INSTITUTIONAL_BRANCH_REACHABLE,
            "institutional_volume_multiplier": INSTITUTIONAL_VOLUME_MULTIPLIER,
            "formula_warnings": [],
            "total_trades": len(trades),
            "win_rate": round(wins / len(trades) * 100, 2) if trades else 0.0,
            "total_pnl_pct": round(sum(pnls), 2),
            "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss else (999.0 if gross_profit else 0.0),
            "trades": rows,
        }

    def _simulate(self, symbol: str, session_date: date, bars: pd.DataFrame, idx: int, strategy: str) -> BacktestTrade:
        highs = bars["high"].astype(float).to_numpy()
        lows = bars["low"].astype(float).to_numpy()
        closes = bars["close"].astype(float).to_numpy()
        ts = pd.to_datetime(bars["timestamp"])
        minutes = (ts.dt.hour * 60 + ts.dt.minute).to_numpy(dtype=np.int32)
        eod_minute = self.eod_exit.hour * 60 + self.eod_exit.minute

        exit_idx, exit_price, exit_reason_code = _simulate_trade_numba(
            highs, lows, closes, minutes, idx, self.target_pct, self.stop_pct, eod_minute
        )
        reason_map = {0: "EOD", 1: "STOP LOSS", 2: "TARGET"}
        exit_reason = reason_map.get(exit_reason_code, "EOD")
        entry = closes[idx]
        entry_ts = pd.Timestamp(ts.iloc[idx])
        exit_ts = pd.Timestamp(ts.iloc[exit_idx])

        return BacktestTrade(
            symbol=symbol,
            signal_date=session_date.isoformat(),
            entry_time=entry_ts.strftime("%H:%M"),
            exit_time=exit_ts.strftime("%H:%M"),
            strategy=strategy,
            entry_price=round(float(entry), 2),
            exit_price=round(float(exit_price), 2),
            pnl_pct=round(float((exit_price / entry - 1) * 100), 2),
            exit_reason=exit_reason,
        )


def prescreen_candidate_symbols(
    universe: Dict[str, dict],
    daily_by_symbol: Dict[str, pd.DataFrame],
    start_date: date,
    end_date: date,
    fii_history_by_symbol: Optional[Dict[str, List[dict]]] = None,
) -> Tuple[Dict[str, dict], Dict[str, int]]:
    """Filter universe using cached daily candles before requesting 5m broker data.

    Excludes symbols that have 0 candidate trading days in [start_date, end_date]
    based on previous month high and minimum turnover conditions.
    Returns:
        screened_universe: dict of symbols that have at least 1 candidate day.
        candidate_days_map: dict of symbol -> number of candidate days.
    """
class CandidateDaysResult(dict):
    """Dictionary mapping symbol -> number of candidate days, with candidate_dates attached."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.candidate_dates: Dict[str, Set[date]] = {}


def prescreen_candidate_symbols(
    universe: Dict[str, dict],
    daily_by_symbol: Dict[str, pd.DataFrame],
    start_date: date,
    end_date: date,
    fii_history_by_symbol: Optional[Dict[str, List[dict]]] = None,
) -> Tuple[Dict[str, dict], CandidateDaysResult]:
    """Filter universe using cached daily candles before requesting 5m broker data.

    Excludes symbols that have 0 candidate trading days in [start_date, end_date]
    based on previous month high and minimum turnover conditions.
    Returns:
        screened_universe: dict of symbols that have at least 1 candidate day.
        candidate_days_map: CandidateDaysResult (symbol -> count, with .candidate_dates mapping).
    """
    screened_universe: Dict[str, dict] = {}
    candidate_days_map = CandidateDaysResult()
    fii_history_by_symbol = fii_history_by_symbol or {}

    for symbol, meta in universe.items():
        daily = daily_by_symbol.get(symbol)
        if daily is None or daily.empty or len(daily) < VOLUME_SMA_LENGTH:
            continue

        daily_index = _DailyHistoryIndex.build(daily)
        timestamps = pd.to_datetime(daily["timestamp"]).dt.tz_localize(None)
        session_dates = timestamps.dt.date
        mask = (session_dates >= start_date) & (session_dates <= end_date)
        window_days = daily[mask]

        candidate_dates: Set[date] = set()
        if not window_days.empty:
            for _, row in window_days.iterrows():
                session_date = pd.to_datetime(row["timestamp"]).date()
                prior_count, previous_high, prior_volume_sum, _ = daily_index.context(session_date)
                if prior_count < VOLUME_SMA_LENGTH or previous_high is None:
                    continue

                day_open = float(row["open"])
                day_high = float(row["high"])
                day_vol = float(row["volume"])

                monthly_possible = (
                    day_high >= previous_high
                    and ((prior_volume_sum + day_vol) / VOLUME_SMA_LENGTH) * day_open >= MIN_TURNOVER_CASH_V13
                )

                institutional_possible = False
                if INSTITUTIONAL_BRANCH_REACHABLE:
                    fii_change = quarterly_fii_change_for_date(
                        fii_history_by_symbol.get(symbol, []), session_date
                    )
                    institutional_possible = (
                        fii_change is not None
                        and fii_change > 1.5
                        and prior_count >= 100
                    )

                if monthly_possible or institutional_possible:
                    candidate_dates.add(session_date)

        # Check live/forming current day or next day if it falls within the window and is not in daily table
        last_daily_date = session_dates.max() if not session_dates.empty else None
        if end_date >= date.today() and (last_daily_date is None or last_daily_date < end_date):
            target_date = end_date
            prior_count, previous_high, prior_volume_sum, _ = daily_index.context(target_date)
            if prior_count >= VOLUME_SMA_LENGTH and previous_high is not None:
                last_close = float(daily.iloc[-1]["close"])
                sma_v = prior_volume_sum / VOLUME_SMA_LENGTH
                turnover = sma_v * last_close
                # Day candle filter for next day / today: must have turnover >= 10 Cr and last close within 8% of monthly breakout
                if turnover >= MIN_TURNOVER_CASH_V13 and last_close >= previous_high * 0.92:
                    candidate_dates.add(target_date)

        if candidate_dates:
            screened_universe[symbol] = meta
            candidate_days_map[symbol] = len(candidate_dates)
            candidate_days_map.candidate_dates[symbol] = candidate_dates

    return screened_universe, candidate_days_map


def get_latest_completed_session_date(as_of: Optional[date] = None) -> date:
    """Return the date of the latest completed NSE trading session."""
    import pytz
    now_ist = datetime.now(pytz.timezone("Asia/Kolkata"))
    ref_date = as_of or now_ist.date()
    if ref_date == now_ist.date() and now_ist.time() < dt_time(15, 30):
        ref_date = ref_date - timedelta(days=1)
    while ref_date.weekday() >= 5:  # 5=Saturday, 6=Sunday
        ref_date = ref_date - timedelta(days=1)
    return ref_date


def load_three_month_5minute_data(
    rest_client,
    universe: Dict[str, dict],
    as_of: Optional[date] = None,
    current_session_by_symbol: Optional[Dict[str, pd.DataFrame]] = None,
    progress: Optional[Callable[[int, int, str], None]] = None,
    max_workers: int = 4,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    hist_db: Optional[Any] = None,
    cache_dir: Optional[Union[str, Path]] = None,
) -> Dict[str, pd.DataFrame]:
    """Load native Upstox 5-minute candles using SQLite for persistent, reusable storage.

    All candle bars are read from and written to SQLite (candles_history_5m)
    so all universes (NIFTY 500, F&O, etc.) reuse the same database without redownloading.
    """
    from database.historical_db import HistoricalCandleDatabase

    if start_date is not None and end_date is not None:
        start, end = start_date, end_date
    else:
        start, end = three_month_window(as_of)

    if hist_db is not None and isinstance(hist_db, HistoricalCandleDatabase):
        db = hist_db
    elif hist_db is not None and hasattr(hist_db, "get_candles_by_symbol") and hasattr(hist_db, "get_candles_for_symbols_bulk"):
        db = hist_db
    elif cache_dir is not None:
        db = HistoricalCandleDatabase(db_path=Path(cache_dir) / "candles_cache.db")
    else:
        db = HistoricalCandleDatabase()

    result: Dict[str, pd.DataFrame] = {}
    current_session_by_symbol = current_session_by_symbol or {}
    items = [(s, meta) for s, meta in universe.items() if meta.get("instrument_key")]
    symbols_list = [s for s, _ in items]
    latest_completed = get_latest_completed_session_date(as_of=end)

    if progress:
        progress(0, len(symbols_list), f"Reading {len(symbols_list)} symbols from SQLite DB...")

    # Step 1: Fast bulk load of all symbols from SQLite
    bulk_candles: Dict[str, pd.DataFrame] = {}
    if hasattr(db, "get_candles_for_symbols_bulk"):
        bulk_candles = db.get_candles_for_symbols_bulk(
            symbols_list,
            from_date=start.isoformat(),
            to_date=end.isoformat(),
        )

    missing_items: List[Tuple[str, dict, Optional[pd.DataFrame]]] = []
    for s, meta in items:
        # Incorporate live session if provided
        live = current_session_by_symbol.get(s)
        if live is not None and not live.empty:
            try:
                db.save_candles_batch(s, meta["instrument_key"], live.to_dict("records"))
            except Exception:
                pass
            cached = db.get_candles_by_symbol(s, from_date=start.isoformat(), to_date=end.isoformat())
            if not cached.empty:
                result[s] = normalize_to_5minute(cached)
            continue

        cached = bulk_candles.get(s)
        if cached is None or cached.empty:
            cached = db.get_candles_by_symbol(s, from_date=start.isoformat(), to_date=end.isoformat())

        # Check if backward-compatible parquet cache exists on disk
        if (cached is None or cached.empty) and cache_dir:
            c_file = Path(cache_dir) / f"{s}_{start}_{end}_5m.parquet"
            if c_file.exists():
                try:
                    df_p = pd.read_parquet(c_file)
                    if not df_p.empty:
                        db.save_candles_batch(s, meta["instrument_key"], df_p.to_dict("records"))
                        cached = db.get_candles_by_symbol(s, from_date=start.isoformat(), to_date=end.isoformat())
                except Exception:
                    pass

        if cached is not None and not cached.empty:
            cached_last = pd.to_datetime(cached["timestamp"]).max().date()
            now_ist = datetime.now(ZoneInfo("Asia/Kolkata"))
            today_ist = now_ist.date()
            if end >= today_ist and now_ist.weekday() < 5 and (dt_time(9, 15) <= now_ist.time() <= dt_time(15, 35)):
                # Market is actively live right now.
                # Check if cached data contains the newest completed 5m candle.
                # A 5m candle starting at T completes at T+5m.
                # If cached has a candle within 7 minutes of now, it's fresh.
                max_ts = pd.to_datetime(cached["timestamp"]).max()
                if max_ts.tzinfo is None:
                    max_ts = max_ts.tz_localize(ZoneInfo("Asia/Kolkata"))
                else:
                    max_ts = max_ts.tz_convert(ZoneInfo("Asia/Kolkata"))
                is_complete = (max_ts >= (now_ist - timedelta(minutes=7)))
            else:
                is_complete = (
                    cached_last >= end
                    or cached_last >= latest_completed
                    or (len(cached) >= 3000 and cached_last >= (end - timedelta(days=4)))
                )
            if is_complete:
                result[s] = normalize_to_5minute(cached)
                continue

        missing_items.append((s, meta, cached))

    if not missing_items or rest_client is None:
        # All symbols satisfied by SQLite cache
        for s, meta, cached in missing_items:
            if cached is not None and not cached.empty:
                result[s] = normalize_to_5minute(cached)
        if progress:
            progress(len(symbols_list), len(symbols_list), f"Loaded {len(result)}/{len(symbols_list)} symbols from SQLite DB")
        return result

    # Step 2: Download ONLY genuinely missing symbols from broker API
    def fetch_missing(s: str, meta: dict, cached: Optional[pd.DataFrame]) -> Tuple[str, pd.DataFrame]:
        cursor = pd.Timestamp(end)
        start_ts = (
            pd.Timestamp(pd.to_datetime(cached["timestamp"]).max().date())
            if cached is not None and not cached.empty
            else pd.Timestamp(start)
        )
        while cursor >= start_ts:
            chunk_start = max(start_ts, cursor - pd.Timedelta(days=29))
            raw = rest_client.get_broker_5m_history(
                meta["instrument_key"],
                to_date=cursor.date().isoformat(),
                from_date=chunk_start.date().isoformat(),
            )
            if raw:
                db.save_candles_batch(s, meta["instrument_key"], raw)
            cursor = chunk_start - pd.Timedelta(days=1)

        final_candles = db.get_candles_by_symbol(s, from_date=start.isoformat(), to_date=end.isoformat())
        if final_candles.empty:
            return s, pd.DataFrame()
        return s, normalize_to_5minute(final_candles)

    completed = 0
    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, 8))) as pool:
        futures = {pool.submit(fetch_missing, s, meta, cached): s for s, meta, cached in missing_items}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                loaded_symbol, frame = future.result()
                if not frame.empty:
                    result[loaded_symbol] = frame
            finally:
                completed += 1
                if progress:
                    progress(completed, len(missing_items), f"Downloaded missing 5m {symbol}")

    return result


load_broker_5minute_data = load_three_month_5minute_data


def quarterly_fii_change_for_date(history: List[dict], session_date: date) -> Optional[float]:
    """Return the latest point change between FII holding quarters known by date."""
    observations: List[Tuple[date, float]] = []
    for row in history or []:
        try:
            period = pd.Period(pd.Timestamp(row["period"]), freq="Q")
            quarter_end = period.end_time.date()
            observations.append((quarter_end, float(row["value"])))
        except (KeyError, TypeError, ValueError):
            continue
    eligible = sorted(item for item in observations if item[0] <= session_date)
    if len(eligible) < 2:
        return None
    return eligible[-1][1] - eligible[-2][1]


def load_quarterly_fii_history(
    rest_client,
    universe: Dict[str, dict],
    cache_dir: Path = Path("data/cache/fii_shareholdings"),
    cache_days: int = 7,
    progress: Optional[Callable[[int, int, str], None]] = None,
) -> Dict[str, List[dict]]:
    """Load the Upstox quarterly FII holding series for each cash-equity ISIN."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    today = date.today()
    result: Dict[str, List[dict]] = {}
    items = list(universe.items())

    for index, (symbol, meta) in enumerate(items, start=1):
        instrument_key = str(meta.get("instrument_key", ""))
        isin = str(meta.get("isin") or instrument_key.rsplit("|", 1)[-1]).upper()
        if not isin.startswith("IN"):
            if progress:
                progress(index, len(items), symbol)
            continue

        cache_file = cache_dir / f"{symbol}_{isin}.json"
        history: List[dict] = []
        if cache_file.exists():
            try:
                cached = json.loads(cache_file.read_text(encoding="utf-8"))
                fetched_on = date.fromisoformat(cached["fetched_on"])
                if (today - fetched_on).days <= cache_days:
                    history = cached.get("history", [])
            except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
                history = []

        if not history:
            holdings = rest_client.get_quarterly_share_holdings(isin)
            fii = next(
                (row for row in holdings if str(row.get("category", "")).lower() == "fii"),
                {},
            )
            history = fii.get("history", []) if isinstance(fii, dict) else []
            if history:
                payload = {"fetched_on": today.isoformat(), "history": history}
                try:
                    cache_file.write_text(json.dumps(payload), encoding="utf-8")
                except OSError:
                    pass
        if history:
            result[symbol] = history
        if progress:
            progress(index, len(items), symbol)
    return result
