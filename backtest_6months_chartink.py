"""
Full 6-Month Intraday (5m & 15m) Entry/Exit Profitability Backtest for Chartink Screener.
Evaluates 2,123 alerts across 126 trading days from March 1, 2026 to September 8, 2026.
Uses compressed parquet disk caching per symbol.
"""

import asyncio
from datetime import datetime, date, time as dtime
import json
import logging
import os
from pathlib import Path
import time
from typing import Dict, List, Optional, Set, Tuple

import httpx
import numpy as np
import pandas as pd

from indicators.chartink_screener import ChartinkIntradayEngine
from market.instruments import InstrumentManager
from upstox.auth import UpstoxAuth
from upstox.rest import UpstoxRestClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backtest_6m")


# 7 consecutive 30-day chunks covering 6+ months from 2026-02-25 to 2026-09-08
CHUNKS_6M = [
    ("2026-09-08", "2026-08-08"),
    ("2026-08-08", "2026-07-08"),
    ("2026-07-08", "2026-06-08"),
    ("2026-06-08", "2026-05-08"),
    ("2026-05-08", "2026-04-08"),
    ("2026-04-08", "2026-03-08"),
    ("2026-03-08", "2026-02-25"),
]


async def download_symbol_6m(client: httpx.AsyncClient, sem: asyncio.Semaphore, key: str, sym: str, out_file: Path) -> Optional[pd.DataFrame]:
    """Downloads 6 months of 1-minute data for a symbol and saves to compressed parquet."""
    if out_file.exists():
        try:
            return pd.read_parquet(out_file)
        except Exception:
            pass

    all_candles = []
    for to_d, from_d in CHUNKS_6M:
        url = f"https://api.upstox.com/v2/historical-candle/{key}/1minute/{to_d}/{from_d}"
        for attempt in range(4):
            async with sem:
                try:
                    await asyncio.sleep(0.04)
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        data = resp.json().get("data", {})
                        candles = data.get("candles", []) if isinstance(data, dict) else []
                        if candles:
                            all_candles.extend(candles)
                        break
                    elif resp.status_code == 429:
                        await asyncio.sleep(1.5 * (attempt + 1))
                except Exception:
                    await asyncio.sleep(0.5)

    if not all_candles:
        return None

    df = pd.DataFrame(all_candles, columns=["timestamp", "open", "high", "low", "close", "volume", "oi"])
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values("timestamp").drop_duplicates(subset=["timestamp"]).reset_index(drop=True)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_file, index=False)
    return df


async def prepare_all_6m_candles(univ: Dict[str, dict], target_symbols: Set[str], token: str, cache_dir: Path) -> Dict[str, pd.DataFrame]:
    """Downloads or loads 6-month 1m parquet files for all target symbols."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    sem = asyncio.Semaphore(6)
    dfs = {}

    symbols_to_fetch = [s for s in target_symbols if s in univ]
    cached_count = 0
    needed_fetch = []

    for sym in symbols_to_fetch:
        p_file = cache_dir / f"{sym}.parquet"
        if p_file.exists():
            try:
                dfs[sym] = pd.read_parquet(p_file)
                cached_count += 1
                continue
            except Exception:
                pass
        needed_fetch.append(sym)

    logger.info(f"6-Month Cache Status: {cached_count} already cached, {len(needed_fetch)} to download.")

    if needed_fetch:
        t0 = time.time()
        logger.info(f"Downloading 6-month intraday data for {len(needed_fetch)} symbols...")
        async with httpx.AsyncClient(headers=headers, timeout=15.0) as client:
            async def worker(sym: str):
                p_file = cache_dir / f"{sym}.parquet"
                df = await download_symbol_6m(client, sem, univ[sym]["instrument_key"], sym, p_file)
                if df is not None and not df.empty:
                    dfs[sym] = df

            tasks = [worker(s) for s in needed_fetch]
            await asyncio.gather(*tasks)

        logger.info(f"Completed 6-month candle download in {time.time() - t0:.2f}s. Total symbols loaded: {len(dfs)}")

    return dfs


def resample_ohlcv(df_1m: pd.DataFrame, freq: str) -> pd.DataFrame:
    """Resamples 1-minute dataframe to 5m or 15m bars."""
    if df_1m.empty:
        return pd.DataFrame()
    df = df_1m.copy()
    df.set_index("timestamp", inplace=True)
    res = df.resample(freq, closed="left", label="left").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna()
    res.reset_index(inplace=True)
    return res


def evaluate_intraday_screener_at_bar(bar_idx: int, df_day_bars: pd.DataFrame, prior_daily_df: pd.DataFrame, engine: ChartinkIntradayEngine) -> bool:
    """Evaluates Chartink screener logic on bar_idx with prior daily context."""
    sub_df = df_day_bars.iloc[:bar_idx + 1]
    d_open = float(sub_df["open"].iloc[0])
    d_high = float(sub_df["high"].max())
    d_low = float(sub_df["low"].min())
    d_close = float(sub_df["close"].iloc[-1])
    d_vol = float(sub_df["volume"].sum())

    # Master condition: (H + L)/2 < (H + L + C)/3 * 0.997
    mid = (d_high + d_low) / 2.0
    typical = (d_high + d_low + d_close) / 3.0
    if not (mid < (typical * 0.997)):
        return False

    if len(prior_daily_df) < 15:
        return False

    p_high = float(prior_daily_df["high"].iloc[-1])
    p_low = float(prior_daily_df["low"].iloc[-1])
    p_close = float(prior_daily_df["close"].iloc[-1])

    # 1. Sub-Strategy 1: Monthly close >= 1 month ago high AND SMA(vol, 20)*open >= 10 Cr
    vol_hist = prior_daily_df["volume"].tolist() + [d_vol]
    sma_vol_20 = np.mean(vol_hist[-20:]) if len(vol_hist) >= 20 else np.mean(vol_hist)
    turnover_pass = (sma_vol_20 * d_open) >= 100_000_000.0

    if len(prior_daily_df) >= 42:
        m_prev_high = float(prior_daily_df["high"].iloc[-42:-21].max())
        monthly_breakout = d_close >= m_prev_high
    else:
        monthly_breakout = False

    if turnover_pass and monthly_breakout:
        return True

    # 2. Sub-Strategy 2: Weekly breakout (20-week max) + Close > SMA(200)
    if len(prior_daily_df) >= 105:
        w_prev_high = float(prior_daily_df["high"].iloc[-105:-5].max())
        sma_200 = float(prior_daily_df["close"].iloc[-200:].mean()) if len(prior_daily_df) >= 200 else float(prior_daily_df["close"].mean())
        if (d_close > w_prev_high) and (d_close > sma_200):
            return True

    # 3. Sub-Strategy 3: SMA(vol, 7) > 100k, Close >= 100, Low > Prev Low, Green Candle, SMA crossover
    sma_vol_7 = np.mean(vol_hist[-7:]) if len(vol_hist) >= 7 else np.mean(vol_hist)
    sub3_vol = sma_vol_7 > 100_000.0
    sub3_price = d_close >= 100.0
    sub3_higher_low = d_low > p_low
    sub3_green = (d_close > d_low) and (d_close > d_open)

    if sub3_vol and sub3_price and sub3_higher_low and sub3_green:
        close_hist = prior_daily_df["close"].tolist() + [d_close]
        for period in range(11, 36):
            if len(close_hist) > period:
                sma_now = np.mean(close_hist[-period:])
                sma_prev = np.mean(close_hist[-period-1:-1])
                if (d_close > sma_now) and (p_close <= sma_prev):
                    return True

    return False


def simulate_trade(
    entry_idx: int,
    bars: pd.DataFrame,
    entry_price: float,
    entry_candle_low: float,
    model_name: str,
    target_pct: float,
    stop_pct: float,
    use_candle_low_sl: bool = False,
    use_trailing: bool = False,
) -> dict:
    """Simulates trade execution with realistic slippage and conservative exit checking."""
    # 0.05% entry slippage
    effective_entry = entry_price * 1.0005

    if use_candle_low_sl:
        sl_price = entry_candle_low * 0.999
        if (effective_entry - sl_price) / effective_entry > 0.025:
            sl_price = effective_entry * (1.0 - 0.025)
    else:
        sl_price = effective_entry * (1.0 - stop_pct)

    tp_price = effective_entry * (1.0 + target_pct) if target_pct > 0 else 999999.0

    peak_price = effective_entry
    trailing_active = False

    exit_time = None
    exit_price = None
    exit_reason = "EOD"
    holding_bars = 0

    for i in range(entry_idx + 1, len(bars)):
        bar = bars.iloc[i]
        b_time = bar["timestamp"].time()
        b_high = float(bar["high"])
        b_low = float(bar["low"])
        b_close = float(bar["close"])
        holding_bars += 1

        if use_trailing:
            if b_high > peak_price:
                peak_price = b_high
            if not trailing_active and (peak_price >= effective_entry * 1.010):
                trailing_active = True
                sl_price = effective_entry * 1.002
            if trailing_active:
                trail_sl = peak_price * (1.0 - 0.006)
                if trail_sl > sl_price:
                    sl_price = trail_sl

        # Check Stop Loss first (conservative)
        if b_low <= sl_price:
            exit_time = bar["timestamp"]
            exit_price = sl_price * 0.9995
            exit_reason = "SL" if not trailing_active else "TRAIL_SL"
            break

        # Check Target
        if target_pct > 0 and b_high >= tp_price:
            exit_time = bar["timestamp"]
            exit_price = tp_price * 0.9995
            exit_reason = "TP"
            break

        # 15:15 EOD square-off
        if b_time >= dtime(15, 15):
            exit_time = bar["timestamp"]
            exit_price = b_close * 0.9995
            exit_reason = "EOD"
            break

    if exit_price is None:
        last_bar = bars.iloc[-1]
        exit_time = last_bar["timestamp"]
        exit_price = float(last_bar["close"]) * 0.9995
        exit_reason = "EOD"

    pnl_pct = ((exit_price - effective_entry) / effective_entry) * 100.0

    return {
        "entry_time": bars.iloc[entry_idx]["timestamp"],
        "entry_price": effective_entry,
        "exit_time": exit_time,
        "exit_price": exit_price,
        "exit_reason": exit_reason,
        "pnl_pct": pnl_pct,
        "holding_bars": holding_bars,
        "is_win": pnl_pct > 0.0,
    }


def compute_metrics(trades: List[dict]) -> dict:
    """Computes comprehensive performance statistics for a set of trades."""
    if not trades:
        return {
            "total_trades": 0, "win_rate": 0.0, "profit_factor": 0.0,
            "avg_pnl": 0.0, "total_pnl": 0.0, "max_drawdown": 0.0,
            "tp_count": 0, "sl_count": 0, "eod_count": 0,
            "avg_win": 0.0, "avg_loss": 0.0, "payoff_ratio": 0.0
        }

    df = pd.DataFrame(trades)
    n = len(df)
    wins = df[df["pnl_pct"] > 0]
    losses = df[df["pnl_pct"] <= 0]

    win_rate = (len(wins) / n * 100.0) if n > 0 else 0.0
    gross_profit = wins["pnl_pct"].sum() if not wins.empty else 0.0
    gross_loss = abs(losses["pnl_pct"].sum()) if not losses.empty else 0.0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)

    avg_win = wins["pnl_pct"].mean() if not wins.empty else 0.0
    avg_loss = abs(losses["pnl_pct"].mean()) if not losses.empty else 0.0
    payoff_ratio = (avg_win / avg_loss) if avg_loss > 0 else 0.0

    total_pnl = df["pnl_pct"].sum()
    avg_pnl = df["pnl_pct"].mean()

    # Cumulative equity curve & Max Drawdown
    cum_returns = (1.0 + df["pnl_pct"] / 100.0).cumprod()
    peak = cum_returns.cummax()
    drawdown = (cum_returns - peak) / peak * 100.0
    max_dd = abs(drawdown.min()) if not drawdown.empty else 0.0

    tp_count = len(df[df["exit_reason"] == "TP"])
    sl_count = len(df[df["exit_reason"].isin(["SL", "TRAIL_SL"])])
    eod_count = len(df[df["exit_reason"] == "EOD"])

    return {
        "total_trades": n,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "avg_pnl": avg_pnl,
        "total_pnl": total_pnl,
        "max_drawdown": max_dd,
        "tp_count": tp_count,
        "sl_count": sl_count,
        "eod_count": eod_count,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "payoff_ratio": payoff_ratio,
    }


def run_6month_backtest():
    truth_file = Path("data/chartink_ground_truth.csv")
    if not truth_file.exists():
        logger.error("Ground truth file missing.")
        return

    df_truth = pd.read_csv(truth_file)
    df_truth["dt"] = pd.to_datetime(df_truth["Date"], format="%d-%m-%Y")
    
    # 6 Months Window: 2026-03-01 to 2026-09-08
    df_truth_6m = df_truth[df_truth["dt"] >= "2026-03-01"].copy()
    logger.info(f"Loaded 6-Month Ground Truth: {len(df_truth_6m)} alerts across {len(df_truth_6m['Date'].unique())} trading sessions.")

    # Load Universe
    auth = UpstoxAuth()
    client = UpstoxRestClient(auth.get_api_client())
    mgr = InstrumentManager(client)
    univ = mgr.load_fno_universe(mode="SPOT")

    # Load Daily Historical Context from SQLite DB
    from database.historical_db import HistoricalCandleDatabase
    hist_db = HistoricalCandleDatabase()
    daily_dfs = hist_db.get_all_daily_candles_map()
    if not daily_dfs:
        daily_cache = Path("data/cache/all_daily_candles_2026.json")
        if daily_cache.exists():
            with open(daily_cache, "r", encoding="utf-8") as f:
                raw_daily = json.load(f)
            hist_db.save_all_daily_candles_bulk(raw_daily)
            daily_dfs = hist_db.get_all_daily_candles_map()

    # Prepare 6-Month 1m Parquet Cache
    target_symbols = set(df_truth_6m["Symbol"].str.strip().str.upper().unique())
    target_symbols = {s for s in target_symbols if s in univ}
    logger.info(f"Active F&O Target Symbols: {len(target_symbols)}")

    cache_dir = Path("data/cache/1m")
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    dfs_1m = loop.run_until_complete(prepare_all_6m_candles(univ, target_symbols, auth.access_token, cache_dir))

    engine = ChartinkIntradayEngine()

    models_config = [
        {"name": "Model 1: Target 1.5%, SL 1.0%", "tp": 0.015, "sl": 0.010, "candle_sl": False, "trail": False},
        {"name": "Model 2: Target 2.0%, SL 1.0%", "tp": 0.020, "sl": 0.010, "candle_sl": False, "trail": False},
        {"name": "Model 3: Trailing Momentum (SL 1.0%, trail > 1.0%)", "tp": 0.0, "sl": 0.010, "candle_sl": False, "trail": True},
        {"name": "Model 4: Target 2.0%, Entry Candle Low SL", "tp": 0.020, "sl": 0.015, "candle_sl": True, "trail": False},
    ]

    timeframes = ["5min", "15min"]
    all_results = {tf: {m["name"]: [] for m in models_config} for tf in timeframes}

    unique_dates = sorted(df_truth_6m["dt"].dt.strftime("%Y-%m-%d").unique())
    logger.info(f"Beginning 6-month simulation across {len(unique_dates)} trading sessions ({unique_dates[0]} to {unique_dates[-1]})...")

    t_sim_start = time.time()

    for d_str in unique_dates:
        t_date = pd.to_datetime(d_str).date()
        alerts_on_date = df_truth_6m[df_truth_6m["dt"].dt.strftime("%Y-%m-%d") == d_str]["Symbol"].str.strip().str.upper().tolist()
        alerts_on_date = [s for s in alerts_on_date if s in dfs_1m and s in daily_dfs]

        for sym in alerts_on_date:
            df_sym_1m = dfs_1m[sym]
            day_mask = df_sym_1m["timestamp"].dt.date == t_date
            day_1m = df_sym_1m[day_mask]
            if len(day_1m) < 15:
                continue

            df_sym_daily = daily_dfs[sym]
            prior_daily = df_sym_daily[df_sym_daily["timestamp"].dt.date < t_date]
            if len(prior_daily) < 15:
                continue

            for tf in timeframes:
                df_tf = resample_ohlcv(day_1m, tf)
                if len(df_tf) < 3:
                    continue

                trigger_idx = None
                for b_idx in range(len(df_tf)):
                    bar_time = df_tf.iloc[b_idx]["timestamp"].time()
                    if bar_time < dtime(9, 20) or bar_time > dtime(14, 30):
                        continue

                    is_triggered = evaluate_intraday_screener_at_bar(b_idx, df_tf, prior_daily, engine)
                    if is_triggered:
                        trigger_idx = b_idx
                        break

                if trigger_idx is None:
                    continue

                entry_bar = df_tf.iloc[trigger_idx]
                entry_px = float(entry_bar["close"])
                candle_low = float(entry_bar["low"])

                for m_cfg in models_config:
                    trade = simulate_trade(
                        entry_idx=trigger_idx,
                        bars=df_tf,
                        entry_price=entry_px,
                        entry_candle_low=candle_low,
                        model_name=m_cfg["name"],
                        target_pct=m_cfg["tp"],
                        stop_pct=m_cfg["sl"],
                        use_candle_low_sl=m_cfg["candle_sl"],
                        use_trailing=m_cfg["trail"],
                    )
                    trade["symbol"] = sym
                    trade["timeframe"] = tf
                    trade["model"] = m_cfg["name"]
                    all_results[tf][m_cfg["name"]].append(trade)

    sim_elapsed = time.time() - t_sim_start
    logger.info(f"6-Month Simulation completed in {sim_elapsed:.2f}s.")

    # Generate Performance Report
    print("\n" + "=" * 86)
    print("      CHARTINK INTRADAY SCREENER: FULL 6-MONTH PROFITABILITY BACKTEST")
    print("=" * 86)
    print(f"Sample Window           : 2026-03-01 to 2026-09-08 (126 Active Trading Sessions)")
    print(f"Universe Evaluated      : Active NSE F&O Stocks (5m & 15m Candle Resolutions)")
    print(f"Simulation Execution    : {sim_elapsed:.2f}s")
    print("-" * 86)

    all_summary_rows = []
    all_trade_records = []

    for tf in timeframes:
        print(f"\n[{tf.upper()} TIMEFRAME 6-MONTH EXECUTION PERFORMANCE]")
        print(f"{'Strategy Model':<36} | {'Trades':<6} | {'Win %':<6} | {'PF':<5} | {'Avg PnL':<7} | {'Total PnL':<10} | {'Max DD':<6}")
        print("-" * 86)
        for m_cfg in models_config:
            m_name = m_cfg["name"]
            trades = all_results[tf][m_name]
            all_trade_records.extend(trades)
            stats = compute_metrics(trades)

            print(
                f"{m_name[:36]:<36} | "
                f"{stats['total_trades']:<6} | "
                f"{stats['win_rate']:>5.1f}% | "
                f"{stats['profit_factor']:>5.2f} | "
                f"{stats['avg_pnl']:>+6.2f}% | "
                f"{stats['total_pnl']:>+9.2f}% | "
                f"{stats['max_drawdown']:>5.1f}%"
            )

            all_summary_rows.append({
                "timeframe": tf,
                "model": m_name,
                "total_trades": stats["total_trades"],
                "win_rate": stats["win_rate"],
                "profit_factor": stats["profit_factor"],
                "avg_pnl": stats["avg_pnl"],
                "total_pnl": stats["total_pnl"],
                "max_drawdown": stats["max_drawdown"],
                "tp_count": stats["tp_count"],
                "sl_count": stats["sl_count"],
                "eod_count": stats["eod_count"],
                "payoff_ratio": stats["payoff_ratio"],
            })

    # Save detailed trade logs and summary metrics
    trades_df = pd.DataFrame(all_trade_records)
    if not trades_df.empty:
        trades_df.to_csv("data/chartink_6month_trades.csv", index=False)
        print(f"\nSaved {len(trades_df)} individual trade executions to data/chartink_6month_trades.csv")

    summary_df = pd.DataFrame(all_summary_rows)
    summary_df.to_csv("data/chartink_6month_performance_summary.csv", index=False)
    print(f"Saved 6-month performance summary to data/chartink_6month_performance_summary.csv")
    print("=" * 86)


if __name__ == "__main__":
    run_6month_backtest()
