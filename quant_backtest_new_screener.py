"""
Institutional Quantitative Backtest Engine for Chartink Intraday Breakout Screener.
Evaluates the complete non-repainting, multi-factor screener across all 498 Nifty 500 stocks:
- Master Pivot Clearance: ((H + L)/2) < ((H + L + C)/3 * 0.997)
- Sub-Strategy 1: Monthly Breakout & Institutional Turnover >= 10 Cr
- Sub-Strategy 2: 20-Week Structural High & Above 200 SMA
- Sub-Strategy 3: Intraday Momentum, Multi-SMA Crossover, RSI(14) Crossover & Volume Surge
- Non-Repainting Chronological 5-Minute Candle First-Detection
- Multi-Model Execution (Fixed TP/SL, Trailing Stop, Candle-Low SL, Multi-Day Swing)
- Quant-Grade Risk Analytics: Sharpe, Sortino, Calmar, Omega, VaR, CVaR, Skewness, Kurtosis
- 2,500-Run Monte Carlo Permutation & Tail Stress Testing
- Slippage Friction Curves, Time-of-Day Alpha & Sectoral Attribution
- High-Resolution Institutional Performance Dashboard Visualization
"""

import os
import sqlite3
from datetime import datetime, date, time as dtime
from pathlib import Path
import logging
import time
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy import stats
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("quant_backtest")

# Database & file paths
DB_PATH = Path("data/historical_candles.db")
UNIVERSE_CSV = Path("data/universes/nifty_500.csv")
OUT_TRADES_CSV = Path("data/quant_new_screener_trades.csv")
OUT_SUMMARY_CSV = Path("data/quant_new_screener_summary.csv")
OUT_MC_CSV = Path("data/quant_new_screener_monte_carlo.csv")
OUT_DASHBOARD_PNG = Path("data/quant_new_screener_dashboard.png")


def load_nifty500_sectors() -> Dict[str, str]:
    """Loads symbol-to-sector mapping from Nifty 500 universe CSV."""
    if not UNIVERSE_CSV.exists():
        logger.warning("Nifty 500 CSV not found, using generic sectors.")
        return {}
    df = pd.read_csv(UNIVERSE_CSV)
    mapping = {}
    for _, row in df.iterrows():
        sym = str(row.get("Symbol", "")).strip().upper()
        ind = str(row.get("Industry", "Other")).strip()
        if sym:
            mapping[sym] = ind
    return mapping


def load_all_daily_candles() -> Dict[str, pd.DataFrame]:
    """Loads full historical daily candle series for all symbols from SQLite DB."""
    logger.info("Loading daily historical candles from database...")
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute("PRAGMA cache_size = -64000")
    
    df_all = pd.read_sql_query(
        "SELECT symbol, timestamp, open, high, low, close, volume FROM candles_history_daily ORDER BY symbol, timestamp",
        conn
    )
    conn.close()

    df_all["timestamp"] = pd.to_datetime(df_all["timestamp"]).dt.tz_localize(None)
    df_all["date"] = df_all["timestamp"].dt.date
    daily_map = {}
    for sym, group in df_all.groupby("symbol"):
        daily_map[sym] = group.reset_index(drop=True)

    logger.info(f"Loaded daily candle history for {len(daily_map)} symbols.")
    return daily_map


def resample_to_5m(df_1m: pd.DataFrame) -> pd.DataFrame:
    """Fast resampling of 1m intraday candles to 5m bars."""
    if df_1m.empty:
        return pd.DataFrame()
    df = df_1m.copy()
    df.set_index("timestamp", inplace=True)
    res = df.resample("5min", closed="left", label="left").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna()
    res.reset_index(inplace=True)
    return res


def compute_rsi(prices: np.ndarray, length: int = 14) -> np.ndarray:
    """Numpy-vectorized RSI calculation."""
    if len(prices) < length + 1:
        return np.full_like(prices, 50.0)
    deltas = np.diff(prices)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    
    avg_gain = np.zeros(len(prices))
    avg_loss = np.zeros(len(prices))
    
    avg_gain[length] = np.mean(gains[:length])
    avg_loss[length] = np.mean(losses[:length])
    
    for i in range(length + 1, len(prices)):
        avg_gain[i] = (avg_gain[i - 1] * (length - 1) + gains[i - 1]) / length
        avg_loss[i] = (avg_loss[i - 1] * (length - 1) + losses[i - 1]) / length
        
    rs = np.where(avg_loss == 0, 100.0, avg_gain / np.where(avg_loss == 0, 1e-9, avg_loss))
    rsi = 100.0 - (100.0 / (1.0 + rs))
    rsi[:length] = 50.0
    return rsi


def evaluate_screener_at_bar(
    cum_open: float,
    cum_high: float,
    cum_low: float,
    cum_close: float,
    cum_vol: float,
    prior_daily: pd.DataFrame,
    current_date: date,
) -> Tuple[bool, List[str], float, float, float]:
    """
    Evaluates exact Chartink Screener rules on cumulative intraday bar:
    1. Master Pivot Clearance: (H + L)/2 < (H + L + C)/3 * 0.997
    2. Sub 1: Monthly Breakout & Turnover >= 10 Cr
    3. Sub 2: 20-Week High & > 200 SMA
    4. Sub 3: Intraday Momentum, Multi-SMA cross, RSI cross & Vol Surge
    """
    n_prior = len(prior_daily)
    if n_prior < 15:
        return False, [], 0.0, 0.0, 1.0

    # 1. Master Pivot Clearance Filter
    median = (cum_high + cum_low) / 2.0
    typical = (cum_high + cum_low + cum_close) / 3.0
    threshold = typical * 0.997
    if median >= threshold:
        return False, [], 0.0, 0.0, 1.0

    diff_pct = ((typical - median) / median) * 100.0 if median > 0 else 0.0
    matched_subs = []

    # 2. Sub-Strategy 1: Monthly Breakout & Turnover >= 10 Cr
    vol_hist = prior_daily["volume"].tolist() + [cum_vol]
    sma_vol_20 = np.mean(vol_hist[-20:])
    turnover_cr = (sma_vol_20 * cum_open) / 10_000_000.0
    turnover_pass = turnover_cr >= 10.0

    cur_month = current_date.month
    cur_year = current_date.year
    prev_month = cur_month - 1 if cur_month > 1 else 12
    prev_year = cur_year if cur_month > 1 else cur_year - 1

    ts_series = prior_daily["timestamp"]
    pm_df = prior_daily[(ts_series.dt.month == prev_month) & (ts_series.dt.year == prev_year)]
    if not pm_df.empty:
        prev_month_high = float(pm_df["high"].max())
    else:
        lb_start = max(0, n_prior - 44)
        lb_end = max(1, n_prior - 22)
        prev_month_high = float(prior_daily["high"].iloc[lb_start:lb_end].max()) if lb_end > lb_start else float("inf")

    if turnover_pass and (cum_close >= prev_month_high):
        matched_subs.append("Monthly Breakout (Sub 1)")

    # 3. Sub-Strategy 2: 20-Week High & > 200 SMA
    if n_prior >= 105:
        max_20w_high = float(prior_daily["high"].iloc[-100:].max())
        sma_200_len = min(200, n_prior)
        sma_200 = float(prior_daily["close"].iloc[-sma_200_len:].mean())
        if (cum_close > max_20w_high) and (cum_close > sma_200):
            matched_subs.append("20W High + 200 SMA (Sub 2)")

    # 4. Sub-Strategy 3: Multi-SMA Cross, RSI Cross & Vol Surge
    sma_vol_7 = float(np.mean(vol_hist[-7:]))
    prev_day_low = float(prior_daily["low"].iloc[-1])
    prev_day_close = float(prior_daily["close"].iloc[-1])

    sub3_base = (
        (sma_vol_7 > 100_000.0)
        and (cum_close >= 100.0)
        and (cum_low > prev_day_low)
        and (cum_close > cum_low)
        and (cum_close > cum_open)
    )

    rsi_val = 50.0
    vol_surge_ratio = 1.0

    if sub3_base:
        close_hist = prior_daily["close"].tolist() + [cum_close]
        c_arr = np.array(close_hist, dtype=float)

        # Multi-SMA crossover across range 11 to 35
        ma_cross = False
        for period in range(11, 36):
            if len(c_arr) > period:
                sma_now = np.mean(c_arr[-period:])
                sma_prev = np.mean(c_arr[-period-1:-1])
                if (cum_close > sma_now) and (prev_day_close <= sma_prev):
                    ma_cross = True
                    break

        if ma_cross:
            # RSI(14) Crossover
            rsi_series = compute_rsi(c_arr, length=14)
            rsi_val = float(rsi_series[-1])
            rsi_prev = float(rsi_series[-2])
            rsi_cross = any((rsi_val > lvl and rsi_prev <= lvl) for lvl in range(11, 56))

            if rsi_cross:
                # Volume Surge >= ANY SMA(5..20)
                v_arr = np.array(vol_hist, dtype=float)
                vol_surge = any(cum_vol >= np.mean(v_arr[-vp:]) for vp in range(5, 21) if len(v_arr) >= vp)
                if vol_surge:
                    min_v_sma = np.mean(v_arr[-10:]) if len(v_arr) >= 10 else 1.0
                    vol_surge_ratio = float(cum_vol / min_v_sma) if min_v_sma > 0 else 1.0
                    matched_subs.append("MA + RSI + Vol Surge (Sub 3)")

    if not matched_subs:
        return False, [], diff_pct, turnover_cr, vol_surge_ratio

    return True, matched_subs, diff_pct, turnover_cr, vol_surge_ratio


def simulate_all_models(
    bars_5m: pd.DataFrame,
    trigger_idx: int,
    subsequent_daily: Optional[pd.DataFrame] = None,
) -> Dict[str, dict]:
    """
    Simulates execution across 4 distinct institutional trade models:
    - Model 1: Fixed 1.5% TP / 0.8% SL (1.875:1 R:R, 15:15 EOD)
    - Model 2: Trailing Stop Loss (+1.0% activation, trail 0.6%, 15:15 EOD)
    - Model 3: Candle-Low SL (Capped at 2.5%, Target 2.0%, 15:15 EOD)
    - Model 4: Multi-Day Swing (Target 3.0%, SL 1.5%, held up to 3 days)
    """
    trigger_bar = bars_5m.iloc[trigger_idx]
    entry_price = float(trigger_bar["close"]) * 1.0005  # 0.05% cash equity slippage
    entry_candle_low = float(trigger_bar["low"])
    entry_time = trigger_bar["timestamp"]

    remaining_bars = bars_5m.iloc[trigger_idx + 1:]
    n_rem = len(remaining_bars)

    # ─────────────────────────────────────────────────────────────
    # Model 1: Fixed 1.5% TP / 0.8% SL
    # ─────────────────────────────────────────────────────────────
    tp_1 = entry_price * 1.015
    sl_1 = entry_price * (1.0 - 0.008)
    exit_p1 = None
    exit_r1 = "EOD"
    exit_t1 = None
    holding_1 = 0

    for i in range(n_rem):
        b = remaining_bars.iloc[i]
        b_time = b["timestamp"].time()
        holding_1 += 1
        if float(b["low"]) <= sl_1:
            exit_p1 = sl_1 * 0.9995
            exit_r1 = "SL"
            exit_t1 = b["timestamp"]
            break
        if float(b["high"]) >= tp_1:
            exit_p1 = tp_1 * 0.9995
            exit_r1 = "TP"
            exit_t1 = b["timestamp"]
            break
        if b_time >= dtime(15, 15):
            exit_p1 = float(b["close"]) * 0.9995
            exit_r1 = "EOD"
            exit_t1 = b["timestamp"]
            break

    if exit_p1 is None:
        last_b = bars_5m.iloc[-1]
        exit_p1 = float(last_b["close"]) * 0.9995
        exit_t1 = last_b["timestamp"]

    pnl_1 = ((exit_p1 - entry_price) / entry_price) * 100.0

    # ─────────────────────────────────────────────────────────────
    # Model 2: Trailing Stop Loss (+1.0% activation, trail 0.6%)
    # ─────────────────────────────────────────────────────────────
    sl_2 = entry_price * 0.990
    exit_p2 = None
    exit_r2 = "EOD"
    exit_t2 = None
    holding_2 = 0
    peak_p = entry_price
    trail_active = False

    for i in range(n_rem):
        b = remaining_bars.iloc[i]
        b_time = b["timestamp"].time()
        b_h = float(b["high"])
        b_l = float(b["low"])
        holding_2 += 1

        if b_h > peak_p:
            peak_p = b_h

        if not trail_active and (peak_p >= entry_price * 1.010):
            trail_active = True
            sl_2 = entry_price * 1.002

        if trail_active:
            trail_sl = peak_p * (1.0 - 0.006)
            if trail_sl > sl_2:
                sl_2 = trail_sl

        if b_l <= sl_2:
            exit_p2 = sl_2 * 0.9995
            exit_r2 = "TRAIL_SL" if trail_active else "SL"
            exit_t2 = b["timestamp"]
            break

        if b_time >= dtime(15, 15):
            exit_p2 = float(b["close"]) * 0.9995
            exit_r2 = "EOD"
            exit_t2 = b["timestamp"]
            break

    if exit_p2 is None:
        last_b = bars_5m.iloc[-1]
        exit_p2 = float(last_b["close"]) * 0.9995
        exit_t2 = last_b["timestamp"]

    pnl_2 = ((exit_p2 - entry_price) / entry_price) * 100.0

    # ─────────────────────────────────────────────────────────────
    # Model 3: Candle-Low SL (Capped at 2.5%, Target 2.0%)
    # ─────────────────────────────────────────────────────────────
    sl_3 = entry_candle_low * 0.999
    if (entry_price - sl_3) / entry_price > 0.025:
        sl_3 = entry_price * 0.975
    tp_3 = entry_price * 1.020
    exit_p3 = None
    exit_r3 = "EOD"
    exit_t3 = None
    holding_3 = 0

    for i in range(n_rem):
        b = remaining_bars.iloc[i]
        b_time = b["timestamp"].time()
        holding_3 += 1
        if float(b["low"]) <= sl_3:
            exit_p3 = sl_3 * 0.9995
            exit_r3 = "SL"
            exit_t3 = b["timestamp"]
            break
        if float(b["high"]) >= tp_3:
            exit_p3 = tp_3 * 0.9995
            exit_r3 = "TP"
            exit_t3 = b["timestamp"]
            break
        if b_time >= dtime(15, 15):
            exit_p3 = float(b["close"]) * 0.9995
            exit_r3 = "EOD"
            exit_t3 = b["timestamp"]
            break

    if exit_p3 is None:
        last_b = bars_5m.iloc[-1]
        exit_p3 = float(last_b["close"]) * 0.9995
        exit_t3 = last_b["timestamp"]

    pnl_3 = ((exit_p3 - entry_price) / entry_price) * 100.0

    # ─────────────────────────────────────────────────────────────
    # Model 4: Multi-Day Swing (Target 3.0%, SL 1.5%, held up to 3 days)
    # ─────────────────────────────────────────────────────────────
    tp_4 = entry_price * 1.030
    sl_4 = entry_price * (1.0 - 0.015)
    exit_p4 = None
    exit_r4 = "HOLDING_END"
    exit_t4 = None

    # Check remaining intraday day 1 first
    for i in range(n_rem):
        b = remaining_bars.iloc[i]
        if float(b["low"]) <= sl_4:
            exit_p4 = sl_4 * 0.9995
            exit_r4 = "SL"
            exit_t4 = b["timestamp"]
            break
        if float(b["high"]) >= tp_4:
            exit_p4 = tp_4 * 0.9995
            exit_r4 = "TP"
            exit_t4 = b["timestamp"]
            break

    if exit_p4 is None and subsequent_daily is not None and not subsequent_daily.empty:
        # Check next 3 daily bars
        for d_idx in range(min(3, len(subsequent_daily))):
            d_bar = subsequent_daily.iloc[d_idx]
            d_low = float(d_bar["low"])
            d_high = float(d_bar["high"])
            if d_low <= sl_4:
                exit_p4 = sl_4 * 0.9995
                exit_r4 = "SL"
                exit_t4 = d_bar["timestamp"]
                break
            if d_high >= tp_4:
                exit_p4 = tp_4 * 0.9995
                exit_r4 = "TP"
                exit_t4 = d_bar["timestamp"]
                break
        if exit_p4 is None:
            last_d = subsequent_daily.iloc[min(2, len(subsequent_daily) - 1)]
            exit_p4 = float(last_d["close"]) * 0.9995
            exit_t4 = last_d["timestamp"]
            exit_r4 = "EOD_3D"
    elif exit_p4 is None:
        last_b = bars_5m.iloc[-1]
        exit_p4 = float(last_b["close"]) * 0.9995
        exit_t4 = last_b["timestamp"]
        exit_r4 = "EOD_1D"

    pnl_4 = ((exit_p4 - entry_price) / entry_price) * 100.0

    return {
        "entry_price": entry_price,
        "entry_time": entry_time,
        "model_1": {"pnl": pnl_1, "exit_reason": exit_r1, "exit_time": exit_t1, "bars": holding_1},
        "model_2": {"pnl": pnl_2, "exit_reason": exit_r2, "exit_time": exit_t2, "bars": holding_2},
        "model_3": {"pnl": pnl_3, "exit_reason": exit_r3, "exit_time": exit_t3, "bars": holding_3},
        "model_4": {"pnl": pnl_4, "exit_reason": exit_r4, "exit_time": exit_t4, "bars": 0},
    }


def run_full_backtest() -> pd.DataFrame:
    """Executes full historical simulation across Nifty 500 universe."""
    daily_map = load_all_daily_candles()
    sector_map = load_nifty500_sectors()

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute("PRAGMA cache_size = -64000")

    # Get all distinct trading dates
    trading_dates = [
        r[0] for r in conn.execute(
            "SELECT DISTINCT substr(timestamp, 1, 10) FROM candles_history_1m ORDER BY 1"
        ).fetchall()
    ]
    logger.info(f"Identified {len(trading_dates)} distinct trading dates: {trading_dates[0]} to {trading_dates[-1]}")

    all_symbols = [
        r[0] for r in conn.execute("SELECT DISTINCT symbol FROM candles_history_1m ORDER BY 1").fetchall()
    ]
    logger.info(f"Target screening universe: {len(all_symbols)} symbols.")

    trade_records = []
    t0 = time.time()
    total_evals = 0

    for s_idx, sym in enumerate(all_symbols):
        if sym not in daily_map:
            continue
        df_sym_daily = daily_map[sym]
        sector = sector_map.get(sym, "Other")

        # Load all 1m candles for this symbol
        df_1m_sym = pd.read_sql_query(
            f"SELECT timestamp, open, high, low, close, volume FROM candles_history_1m WHERE symbol='{sym}' ORDER BY timestamp",
            conn
        )
        if df_1m_sym.empty:
            continue

        df_1m_sym["timestamp"] = pd.to_datetime(df_1m_sym["timestamp"]).dt.tz_localize(None)
        df_1m_sym["date"] = df_1m_sym["timestamp"].dt.date

        # Group by trading date
        for d_str in trading_dates:
            d_obj = datetime.strptime(d_str, "%Y-%m-%d").date()
            df_day_1m = df_1m_sym[df_1m_sym["date"] == d_obj]
            if len(df_day_1m) < 15:
                continue

            # Prior daily context strictly prior to today
            prior_daily = df_sym_daily[df_sym_daily["date"] < d_obj]
            if len(prior_daily) < 15:
                continue

            subsequent_daily = df_sym_daily[df_sym_daily["date"] > d_obj]

            # Resample today's 1m candles to 5m
            bars_5m = resample_to_5m(df_day_1m)
            if len(bars_5m) < 5:
                continue

            total_evals += 1

            # Chronological first-detection sweep
            for b_idx in range(1, len(bars_5m)):
                cum_bars = bars_5m.iloc[:b_idx + 1]
                c_open = float(cum_bars["open"].iloc[0])
                c_high = float(cum_bars["high"].max())
                c_low = float(cum_bars["low"].min())
                c_close = float(cum_bars["close"].iloc[-1])
                c_vol = float(cum_bars["volume"].sum())

                triggered, matched_subs, pivot_diff, turnover_cr, vol_surge = evaluate_screener_at_bar(
                    cum_open=c_open,
                    cum_high=c_high,
                    cum_low=c_low,
                    cum_close=c_close,
                    cum_vol=c_vol,
                    prior_daily=prior_daily,
                    current_date=d_obj,
                )

                if triggered:
                    # Execute all 4 trade models starting from next bar
                    res_models = simulate_all_models(
                        bars_5m=bars_5m,
                        trigger_idx=b_idx,
                        subsequent_daily=subsequent_daily,
                    )

                    bar_ts = bars_5m.iloc[b_idx]["timestamp"]
                    hour = bar_ts.hour
                    minute = bar_ts.minute
                    t_mins = hour * 60 + minute
                    if t_mins < 11 * 60 + 30:
                        slot = "Morning (09:20-11:30)"
                    elif t_mins < 13 * 60 + 30:
                        slot = "Midday (11:30-13:30)"
                    else:
                        slot = "Afternoon (13:30-14:30)"

                    strategy_category = "Confluence" if len(matched_subs) > 1 else matched_subs[0]

                    trade_records.append({
                        "date": str(d_obj),
                        "symbol": sym,
                        "sector": sector,
                        "trigger_time": bar_ts.strftime("%H:%M:%S"),
                        "time_slot": slot,
                        "trigger_price": res_models["entry_price"],
                        "matched_strategies": " • ".join(matched_subs),
                        "primary_strategy": strategy_category,
                        "pivot_diff_pct": pivot_diff,
                        "turnover_cr": turnover_cr,
                        "vol_surge_ratio": vol_surge,
                        # Model 1
                        "m1_pnl": res_models["model_1"]["pnl"],
                        "m1_exit_reason": res_models["model_1"]["exit_reason"],
                        "m1_holding_bars": res_models["model_1"]["bars"],
                        # Model 2
                        "m2_pnl": res_models["model_2"]["pnl"],
                        "m2_exit_reason": res_models["model_2"]["exit_reason"],
                        "m2_holding_bars": res_models["model_2"]["bars"],
                        # Model 3
                        "m3_pnl": res_models["model_3"]["pnl"],
                        "m3_exit_reason": res_models["model_3"]["exit_reason"],
                        # Model 4
                        "m4_pnl": res_models["model_4"]["pnl"],
                        "m4_exit_reason": res_models["model_4"]["exit_reason"],
                    })
                    break  # Exactly 1 trade per stock per day (non-repainting)

        if (s_idx + 1) % 50 == 0 or (s_idx + 1) == len(all_symbols):
            logger.info(f"Processed {s_idx + 1}/{len(all_symbols)} stocks ({len(trade_records)} trades generated so far)...")

    conn.close()
    elapsed = time.time() - t0
    logger.info(f"Backtest completed in {elapsed:.2f}s! Evaluated {total_evals} stock-days. Total trades generated: {len(trade_records)}")

    df_trades = pd.DataFrame(trade_records)
    OUT_TRADES_CSV.parent.mkdir(parents=True, exist_ok=True)
    df_trades.to_csv(OUT_TRADES_CSV, index=False)
    logger.info(f"Saved trades log to {OUT_TRADES_CSV}")
    return df_trades


def compute_institutional_metrics(pnl_series: pd.Series, dates_series: pd.Series, rf_annual: float = 0.065) -> dict:
    """Computes rigorous institutional risk-adjusted metrics."""
    n = len(pnl_series)
    if n == 0:
        return {}

    wins = pnl_series[pnl_series > 0]
    losses = pnl_series[pnl_series < 0]
    win_rate = (len(wins) / n) * 100.0
    
    total_gain = wins.sum() if len(wins) > 0 else 0.0
    total_loss = abs(losses.sum()) if len(losses) > 0 else 1e-6
    profit_factor = total_gain / total_loss

    avg_win = wins.mean() if len(wins) > 0 else 0.0
    avg_loss = abs(losses.mean()) if len(losses) > 0 else 0.0
    payoff_ratio = (avg_win / avg_loss) if avg_loss > 0 else 0.0
    expectancy = (win_rate / 100.0 * avg_win) - ((100.0 - win_rate) / 100.0 * avg_loss)

    # Daily aggregation for Sharpe / Sortino
    df_d = pd.DataFrame({"date": dates_series, "pnl": pnl_series})
    daily_sums = df_d.groupby("date")["pnl"].sum()
    n_days = max(1, len(daily_sums))

    mean_daily = daily_sums.mean() / 100.0
    std_daily = daily_sums.std() / 100.0 if n_days > 1 else 0.0001
    std_daily = max(std_daily, 0.0001)

    rf_daily = rf_annual / 252.0
    sharpe = ((mean_daily - rf_daily) / std_daily) * np.sqrt(252.0)

    downside = daily_sums[daily_sums < 0] / 100.0
    downside_std = np.sqrt(np.mean(downside ** 2)) * np.sqrt(252.0) if len(downside) > 0 else 0.0001
    sortino = (mean_daily * 252.0) / downside_std if downside_std > 0 else 99.0

    # Cumulative Drawdown
    cum_ret = pnl_series.cumsum()
    peak = cum_ret.cummax()
    dd = cum_ret - peak
    max_dd = abs(dd.min())

    total_pnl = pnl_series.sum()
    cagr = ((1.0 + total_pnl / 100.0) ** (252.0 / n_days) - 1.0) * 100.0 if n_days > 0 and total_pnl > -100 else 0.0
    calmar = (cagr / max_dd) if max_dd > 0 else 99.0

    # Tail Risk Metrics
    var_95 = float(np.percentile(pnl_series, 5))
    var_99 = float(np.percentile(pnl_series, 1))
    cvar_95 = float(pnl_series[pnl_series <= var_95].mean()) if len(pnl_series[pnl_series <= var_95]) > 0 else var_95
    cvar_99 = float(pnl_series[pnl_series <= var_99].mean()) if len(pnl_series[pnl_series <= var_99]) > 0 else var_99

    skewness = float(stats.skew(pnl_series))
    kurt = float(stats.kurtosis(pnl_series))

    # Omega Ratio (threshold = 0)
    pos_ret = pnl_series[pnl_series > 0].sum()
    neg_ret = abs(pnl_series[pnl_series < 0].sum())
    omega = (pos_ret / neg_ret) if neg_ret > 0 else 99.0

    return {
        "trades": n,
        "win_rate": round(win_rate, 2),
        "profit_factor": round(profit_factor, 2),
        "total_pnl": round(total_pnl, 2),
        "expectancy": round(expectancy, 3),
        "payoff_ratio": round(payoff_ratio, 2),
        "sharpe": round(sharpe, 2),
        "sortino": round(sortino, 2),
        "calmar": round(calmar, 2),
        "omega": round(omega, 2),
        "max_dd": round(max_dd, 2),
        "cagr": round(cagr, 2),
        "var_95": round(var_95, 2),
        "var_99": round(var_99, 2),
        "cvar_95": round(cvar_95, 2),
        "cvar_99": round(cvar_99, 2),
        "skewness": round(skewness, 2),
        "kurtosis": round(kurt, 2),
    }


def run_monte_carlo(pnl_series: pd.Series, n_sims: int = 2500) -> Tuple[pd.DataFrame, dict]:
    """Runs 2,500-iteration Monte Carlo permutation stress test."""
    pnl_arr = pnl_series.to_numpy()
    n_trades = len(pnl_arr)

    equity_paths = np.zeros((n_sims, n_trades + 1))
    max_drawdowns = np.zeros(n_sims)
    win_rates = np.zeros(n_sims)
    final_pnls = np.zeros(n_sims)

    for i in range(n_sims):
        sampled = np.random.choice(pnl_arr, size=n_trades, replace=True)
        eq = np.concatenate([[0.0], np.cumsum(sampled)])
        equity_paths[i, :] = eq
        
        peak = np.maximum.accumulate(eq)
        dd = eq - peak
        max_drawdowns[i] = abs(np.min(dd))
        win_rates[i] = (np.sum(sampled > 0) / n_trades) * 100.0
        final_pnls[i] = eq[-1]

    percentiles = [5, 25, 50, 75, 95]
    mc_curves = {f"p{p}": np.percentile(equity_paths, p, axis=0) for p in percentiles}
    df_mc = pd.DataFrame(mc_curves)

    stats_summary = {
        "pnl_p5": round(float(np.percentile(final_pnls, 5)), 2),
        "pnl_p50": round(float(np.percentile(final_pnls, 50)), 2),
        "pnl_p95": round(float(np.percentile(final_pnls, 95)), 2),
        "winrate_p5": round(float(np.percentile(win_rates, 5)), 2),
        "winrate_p50": round(float(np.percentile(win_rates, 50)), 2),
        "winrate_p95": round(float(np.percentile(win_rates, 95)), 2),
        "dd_p5": round(float(np.percentile(max_drawdowns, 5)), 2),
        "dd_p50": round(float(np.percentile(max_drawdowns, 50)), 2),
        "dd_p95": round(float(np.percentile(max_drawdowns, 95)), 2),
        "prob_ruin_5pct": round(float(np.mean(max_drawdowns >= 5.0) * 100.0), 2),
        "prob_ruin_10pct": round(float(np.mean(max_drawdowns >= 10.0) * 100.0), 2),
    }

    return df_mc, stats_summary


def generate_quant_dashboard(
    df_trades: pd.DataFrame,
    metrics_summary: pd.DataFrame,
    df_mc: pd.DataFrame,
    out_path: Path,
):
    """Generates an 8-panel institutional quant dashboard chart."""
    plt.style.use("dark_background")
    fig, axes = plt.subplots(4, 2, figsize=(18, 22), dpi=150)
    fig.patch.set_facecolor("#0b0f19")

    title_text = (
        "CHARTINK INTRADAY BREAKOUT SCREENER: INSTITUTIONAL QUANT PERFORMANCE DASHBOARD\n"
        "Nifty 500 Broad Market Cash Equity | Zero-Repainting First Detection | 4 Execution Models"
    )
    fig.suptitle(title_text, fontsize=16, fontweight="bold", color="#38bdf8", y=0.99)

    # 1. Equity Curves Comparison (All 4 Models)
    ax1 = axes[0, 0]
    ax1.set_facecolor("#111827")
    ax1.plot(df_trades["m1_pnl"].cumsum(), label="Model 1: Target 1.5%, SL 0.8%", color="#10b981", lw=2.0)
    ax1.plot(df_trades["m2_pnl"].cumsum(), label="Model 2: Trailing SL (+1.0%/0.6%)", color="#38bdf8", lw=1.8)
    ax1.plot(df_trades["m3_pnl"].cumsum(), label="Model 3: Candle-Low SL (2.0% TP)", color="#f59e0b", lw=1.8)
    ax1.plot(df_trades["m4_pnl"].cumsum(), label="Model 4: Multi-Day Swing (3.0% TP)", color="#a855f7", lw=1.8)
    ax1.set_title("Cumulative Returns (%): All Execution Models", fontsize=12, fontweight="bold", color="#f3f4f6")
    ax1.set_ylabel("Cumulative PnL (%)", color="#9ca3af")
    ax1.grid(True, alpha=0.2, ls="--")
    ax1.legend(loc="upper left", framealpha=0.3, fontsize=9)

    # 2. Drawdown Underwater Curves
    ax2 = axes[0, 1]
    ax2.set_facecolor("#111827")
    for col, lbl, clr in [
        ("m1_pnl", "Model 1", "#10b981"),
        ("m2_pnl", "Model 2", "#38bdf8"),
        ("m3_pnl", "Model 3", "#f59e0b"),
        ("m4_pnl", "Model 4", "#a855f7"),
    ]:
        cum = df_trades[col].cumsum()
        peak = cum.cummax()
        dd = cum - peak
        ax2.plot(dd, label=lbl, color=clr, lw=1.5, alpha=0.85)
    ax2.set_title("Underwater Drawdown (%): Risk Comparison", fontsize=12, fontweight="bold", color="#f3f4f6")
    ax2.set_ylabel("Drawdown (%)", color="#9ca3af")
    ax2.grid(True, alpha=0.2, ls="--")
    ax2.legend(loc="lower left", framealpha=0.3, fontsize=9)

    # 3. Model 1 PnL Distribution & VaR Thresholds
    ax3 = axes[1, 0]
    ax3.set_facecolor("#111827")
    pnl = df_trades["m1_pnl"]
    ax3.hist(pnl, bins=40, color="#10b981", alpha=0.7, edgecolor="#064e3b", density=True)
    var95 = np.percentile(pnl, 5)
    var99 = np.percentile(pnl, 1)
    ax3.axvline(var95, color="#f59e0b", ls="--", lw=2, label=f"VaR 95%: {var95:.2f}%")
    ax3.axvline(var99, color="#ef4444", ls="--", lw=2, label=f"VaR 99%: {var99:.2f}%")
    ax3.axvline(pnl.mean(), color="#38bdf8", ls="-", lw=2, label=f"Mean: +{pnl.mean():.2f}%")
    ax3.set_title("Trade Return Distribution & Value at Risk (Model 1)", fontsize=12, fontweight="bold", color="#f3f4f6")
    ax3.set_xlabel("PnL per Trade (%)", color="#9ca3af")
    ax3.grid(True, alpha=0.2, ls="--")
    ax3.legend(loc="upper right", framealpha=0.3, fontsize=9)

    # 4. Monte Carlo 2,500 Simulation Confidence Bands
    ax4 = axes[1, 1]
    ax4.set_facecolor("#111827")
    x = range(len(df_mc))
    ax4.fill_between(x, df_mc["p5"], df_mc["p95"], color="#10b981", alpha=0.25, label="5% - 95% Confidence Band")
    ax4.fill_between(x, df_mc["p25"], df_mc["p75"], color="#10b981", alpha=0.45, label="25% - 75% Interquartile")
    ax4.plot(x, df_mc["p50"], color="#34d399", lw=2, label="Median Path (50th %ile)")
    ax4.plot(x, df_mc["p5"], color="#ef4444", lw=1.2, ls="--", label="Worst 5% Stress Case")
    ax4.set_title("Monte Carlo 2,500 Stress Simulation: Return Distribution", fontsize=12, fontweight="bold", color="#f3f4f6")
    ax4.set_ylabel("Simulated Cumulative Return (%)", color="#9ca3af")
    ax4.grid(True, alpha=0.2, ls="--")
    ax4.legend(loc="upper left", framealpha=0.3, fontsize=9)

    # 5. Sub-Strategy Breakdown (PnL & Win Rate)
    ax5 = axes[2, 0]
    ax5.set_facecolor("#111827")
    sub_stats = df_trades.groupby("primary_strategy").agg(
        trades=("m1_pnl", "count"),
        win_rate=("m1_pnl", lambda s: (s > 0).mean() * 100.0),
        total_pnl=("m1_pnl", "sum")
    ).reset_index()
    sub_stats = sub_stats.sort_values("total_pnl", ascending=True)
    bars = ax5.barh(sub_stats["primary_strategy"], sub_stats["total_pnl"], color="#38bdf8", alpha=0.85)
    for b, wr, tr in zip(bars, sub_stats["win_rate"], sub_stats["trades"]):
        ax5.text(b.get_width() + 1.0, b.get_y() + b.get_height()/2, f"WR: {wr:.1f}% ({tr} tr)", va="center", color="#e5e7eb", fontsize=8)
    ax5.set_title("Sub-Strategy Attribution: Total PnL & Win Rate", fontsize=12, fontweight="bold", color="#f3f4f6")
    ax5.set_xlabel("Total PnL (%)", color="#9ca3af")
    ax5.grid(True, alpha=0.2, ls="--")

    # 6. Intraday Time-of-Day Alpha Decay
    ax6 = axes[2, 1]
    ax6.set_facecolor("#111827")
    time_stats = df_trades.groupby("time_slot").agg(
        trades=("m1_pnl", "count"),
        win_rate=("m1_pnl", lambda s: (s > 0).mean() * 100.0),
        avg_pnl=("m1_pnl", "mean")
    ).reindex(["Morning (09:20-11:30)", "Midday (11:30-13:30)", "Afternoon (13:30-14:30)"]).dropna()

    x_idx = np.arange(len(time_stats))
    ax6.bar(x_idx - 0.15, time_stats["win_rate"], width=0.3, label="Win Rate (%)", color="#10b981", alpha=0.85)
    ax6_twin = ax6.twinx()
    ax6_twin.plot(x_idx + 0.15, time_stats["avg_pnl"], color="#f59e0b", marker="o", lw=2, label="Avg PnL (%)")
    ax6.set_xticks(x_idx)
    ax6.set_xticklabels(time_stats.index, fontsize=8)
    ax6.set_ylabel("Win Rate (%)", color="#10b981")
    ax6_twin.set_ylabel("Avg PnL per Trade (%)", color="#f59e0b")
    ax6.set_title("Intraday Time-of-Day Alpha Decay", fontsize=12, fontweight="bold", color="#f3f4f6")
    ax6.grid(True, alpha=0.2, ls="--")

    # 7. Sector Attribution (Top Sectors)
    ax7 = axes[3, 0]
    ax7.set_facecolor("#111827")
    sec_stats = df_trades.groupby("sector").agg(
        trades=("m1_pnl", "count"),
        win_rate=("m1_pnl", lambda s: (s > 0).mean() * 100.0),
        pnl=("m1_pnl", "sum")
    ).reset_index()
    sec_stats = sec_stats[sec_stats["trades"] >= 5].sort_values("pnl", ascending=False).head(8)
    sec_stats = sec_stats.sort_values("pnl", ascending=True)

    bars_sec = ax7.barh(sec_stats["sector"], sec_stats["pnl"], color="#a855f7", alpha=0.85)
    for b, wr in zip(bars_sec, sec_stats["win_rate"]):
        ax7.text(b.get_width() + 0.5, b.get_y() + b.get_height()/2, f"{wr:.1f}% WR", va="center", color="#e5e7eb", fontsize=8)
    ax7.set_title("Top 8 Performing Sectors across Nifty 500", fontsize=12, fontweight="bold", color="#f3f4f6")
    ax7.set_xlabel("Cumulative PnL (%)", color="#9ca3af")
    ax7.grid(True, alpha=0.2, ls="--")

    # 8. Slippage & Friction Sensitivity Stress Curve
    ax8 = axes[3, 1]
    ax8.set_facecolor("#111827")
    slippage_levels = np.linspace(0.02, 0.20, 10)  # 0.02% to 0.20% per trade
    stressed_pnls = []
    for slip in slippage_levels:
        adj = df_trades["m1_pnl"] - (slip * 2.0)  # Round-trip friction
        stressed_pnls.append(adj.sum())

    ax8.plot(slippage_levels * 100.0, stressed_pnls, color="#ef4444", marker="s", lw=2, label="Net PnL vs Friction")
    ax8.axhline(0, color="#6b7280", ls="--", lw=1)
    ax8.set_title("Slippage & Transaction Friction Stress Curve", fontsize=12, fontweight="bold", color="#f3f4f6")
    ax8.set_xlabel("One-Way Slippage / Brokerage (%)", color="#9ca3af")
    ax8.set_ylabel("Net Cumulative Return (%)", color="#9ca3af")
    ax8.grid(True, alpha=0.2, ls="--")
    ax8.legend(loc="upper right", framealpha=0.3, fontsize=9)

    plt.tight_layout(rect=[0, 0.02, 1, 0.97])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out_path, dpi=150, facecolor=fig.get_facecolor(), edgecolor="none")
    plt.close()
    logger.info(f"Saved institutional visual dashboard to {out_path}")


def main(force_rerun: bool = False):
    logger.info("Starting Full Institutional Quant Backtest for New Screener Understanding...")
    if OUT_TRADES_CSV.exists() and not force_rerun:
        logger.info(f"Loading existing trade logs from {OUT_TRADES_CSV}...")
        df_trades = pd.read_csv(OUT_TRADES_CSV)
    else:
        df_trades = run_full_backtest()

    if df_trades.empty:
        logger.warning("No breakout trades generated. Check candle data coverage.")
        return

    logger.info("Computing institutional quantitative metrics for all 4 execution models...")
    dates_col = df_trades["date"]
    m1_metrics = compute_institutional_metrics(df_trades["m1_pnl"], dates_col)
    m2_metrics = compute_institutional_metrics(df_trades["m2_pnl"], dates_col)
    m3_metrics = compute_institutional_metrics(df_trades["m3_pnl"], dates_col)
    m4_metrics = compute_institutional_metrics(df_trades["m4_pnl"], dates_col)

    df_summary = pd.DataFrame([
        {"Model": "Model 1: Target 1.5%, SL 0.8% (1.875:1 R:R)", **m1_metrics},
        {"Model": "Model 2: Trailing SL (+1.0%/0.6% trail)", **m2_metrics},
        {"Model": "Model 3: Candle-Low SL (2.0% Target)", **m3_metrics},
        {"Model": "Model 4: Multi-Day Swing (3.0% TP, 1.5% SL)", **m4_metrics},
    ])
    df_summary.to_csv(OUT_SUMMARY_CSV, index=False)
    logger.info(f"Saved performance summary to {OUT_SUMMARY_CSV}")

    print("\n" + "=" * 80)
    print("      CHARTINK INTRADAY SCREENER: QUANT-GRADE PERFORMANCE REPORT")
    print("=" * 80)
    print(df_summary.to_string(index=False))
    print("=" * 80 + "\n")

    logger.info("Running 2,500-iteration Monte Carlo Permutation Stress Test on Model 1...")
    df_mc, mc_summary = run_monte_carlo(df_trades["m1_pnl"], n_sims=2500)
    df_mc.to_csv(OUT_MC_CSV, index=False)
    logger.info(f"Monte Carlo results saved to {OUT_MC_CSV}")

    print("--- MONTE CARLO 2,500 PERMUTATION STRESS TEST (MODEL 1) ---")
    for k, v in mc_summary.items():
        print(f"  {k:25s}: {v}")
    print("----------------------------------------------------------\n")

    logger.info("Generating high-resolution institutional dashboard visualization...")
    generate_quant_dashboard(
        df_trades=df_trades,
        metrics_summary=df_summary,
        df_mc=df_mc,
        out_path=OUT_DASHBOARD_PNG,
    )
    logger.info("Quant backtest execution complete!")


if __name__ == "__main__":
    main()
