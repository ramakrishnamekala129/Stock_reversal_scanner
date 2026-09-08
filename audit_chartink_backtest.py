"""
Comprehensive Historical Backtest of Chartink Intraday Screener Engine
Compares engine predictions against ground-truth Chartink alerts from 16-01-2026 to 08-09-2026.
"""

import asyncio
from datetime import datetime, date
import json
import logging
import os
from pathlib import Path
import time
from typing import Dict, List, Set, Tuple

import httpx
import pandas as pd

from indicators.chartink_screener import ChartinkIntradayEngine
from market.instruments import InstrumentManager
from upstox.auth import UpstoxAuth
from upstox.rest import UpstoxRestClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("chartink_backtest")


async def download_all_daily_candles(universe: Dict[str, dict], token: str, cache_file: Path) -> Dict[str, pd.DataFrame]:
    """Downloads multi-year daily candles for all symbols asynchronously with disk cache."""
    if cache_file.exists():
        logger.info(f"Loading daily candle cache from {cache_file}...")
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                raw_data = json.load(f)
            dfs = {}
            for sym, candles in raw_data.items():
                if candles:
                    df = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume", "oi"])
                    df["timestamp"] = pd.to_datetime(df["timestamp"])
                    df = df.sort_values("timestamp").reset_index(drop=True)
                    dfs[sym] = df
            logger.info(f"Loaded {len(dfs)} cached daily dataframes.")
            return dfs
        except Exception as ex:
            logger.warning(f"Cache load error: {ex}. Re-downloading...")

    logger.info(f"Fetching daily candles for {len(universe)} symbols via Upstox Async API...")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    results_raw = {}
    sem = asyncio.Semaphore(20)

    async with httpx.AsyncClient(headers=headers, timeout=12.0) as client:
        async def fetch_sym(sym: str, item: dict):
            key = item["instrument_key"]
            url = f"https://api.upstox.com/v2/historical-candle/{key}/day/2026-09-08/2024-01-01"
            for _ in range(2):
                async with sem:
                    try:
                        resp = await client.get(url)
                        if resp.status_code == 200:
                            data = resp.json().get("data", {}).get("candles", [])
                            if data:
                                results_raw[sym] = data
                            return
                        elif resp.status_code == 429:
                            await asyncio.sleep(1.0)
                    except Exception:
                        await asyncio.sleep(0.5)

        tasks = [fetch_sym(s, item) for s, item in universe.items()]
        t0 = time.time()
        await asyncio.gather(*tasks)
        logger.info(f"Downloaded {len(results_raw)} symbols in {time.time() - t0:.2f}s.")

    # Save to cache
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(results_raw, f)

    dfs = {}
    for sym, candles in results_raw.items():
        df = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume", "oi"])
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.sort_values("timestamp").reset_index(drop=True)
        dfs[sym] = df
    return dfs


def run_backtest():
    truth_file = Path("data/chartink_ground_truth.csv")
    if not truth_file.exists():
        logger.error(f"Ground truth file {truth_file} not found!")
        return

    df_truth = pd.read_csv(truth_file)
    logger.info(f"Loaded ground truth file with {len(df_truth)} rows across {len(df_truth['Date'].unique())} dates.")

    # Parse truth dates to YYYY-MM-DD
    # Format in file is DD-MM-YYYY
    df_truth["dt"] = pd.to_datetime(df_truth["Date"], format="%d-%m-%Y")
    df_truth["date_str"] = df_truth["dt"].dt.strftime("%Y-%m-%d")
    df_truth["Symbol"] = df_truth["Symbol"].str.strip().str.upper()

    # Build truth map: {date_str: set(symbols)}
    truth_by_date: Dict[str, Set[str]] = {}
    for _, row in df_truth.iterrows():
        truth_by_date.setdefault(row["date_str"], set()).add(row["Symbol"])

    # Load Universe
    auth = UpstoxAuth()
    client = UpstoxRestClient(auth.get_api_client())
    mgr = InstrumentManager(client)
    univ = mgr.load_fno_universe(mode="SPOT")
    logger.info(f"Active F&O Spot Universe: {len(univ)} stocks.")

    # Fetch daily data
    cache_file = Path("data/cache/all_daily_candles_2026.json")
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    
    daily_dfs = loop.run_until_complete(download_all_daily_candles(univ, auth.access_token, cache_file))

    engine = ChartinkIntradayEngine()

    total_truth_events = 0
    total_matched_events = 0
    total_engine_events = 0
    date_results = []

    sub_strat_counts = {"Sub 1": 0, "Sub 2": 0, "Sub 3": 0}
    strategy_details = []

    # Sort dates ascending
    sorted_dates = sorted(truth_by_date.keys())
    logger.info(f"Beginning backtest across {len(sorted_dates)} trading sessions from {sorted_dates[0]} to {sorted_dates[-1]}...")

    t_start = time.time()

    for d_str in sorted_dates:
        target_date = pd.to_datetime(d_str).date()
        truth_symbols = truth_by_date[d_str]
        
        # Only evaluate symbols in the F&O universe
        fno_truth_symbols = {s for s in truth_symbols if s in univ}
        non_fno_symbols = truth_symbols - fno_truth_symbols

        detected_symbols = set()
        matched_symbols = set()

        for sym, df in daily_dfs.items():
            # Slice df up to target_date
            mask = df["timestamp"].dt.date <= target_date
            df_slice = df[mask]
            if len(df_slice) < 15:
                continue
            
            # Check if last bar in df_slice is indeed target_date
            if df_slice["timestamp"].iloc[-1].date() != target_date:
                continue

            sig = engine.evaluate_stock(sym, df_slice)
            if sig:
                detected_symbols.add(sym)
                for st in sig.matched_strategies:
                    if "Sub 1" in st:
                        sub_strat_counts["Sub 1"] += 1
                    if "Sub 2" in st:
                        sub_strat_counts["Sub 2"] += 1
                    if "Sub 3" in st:
                        sub_strat_counts["Sub 3"] += 1

                if sym in fno_truth_symbols:
                    matched_symbols.add(sym)
                    strategy_details.append({
                        "date": d_str,
                        "symbol": sym,
                        "price": sig.price,
                        "strategies": sig.matched_strategies,
                    })

        n_truth = len(fno_truth_symbols)
        n_det = len(detected_symbols)
        n_match = len(matched_symbols)

        total_truth_events += n_truth
        total_engine_events += n_det
        total_matched_events += n_match

        day_match_rate = (n_match / n_truth * 100.0) if n_truth > 0 else 0.0
        date_results.append({
            "date": d_str,
            "truth_count": n_truth,
            "detected_count": n_det,
            "matched_count": n_match,
            "match_rate": day_match_rate,
            "non_fno_in_truth": len(non_fno_symbols),
        })

    elapsed = time.time() - t_start
    overall_match_rate = (total_matched_events / total_truth_events * 100.0) if total_truth_events > 0 else 0.0

    # Save detailed backtest results to CSV first
    res_df = pd.DataFrame(date_results)
    res_df.to_csv("data/chartink_backtest_daily_metrics.csv", index=False)
    
    strat_df = pd.DataFrame(strategy_details)
    strat_df.to_csv("data/chartink_backtest_matched_trades.csv", index=False)

    print("\n" + "=" * 78)
    print("      CHARTINK INTRADAY SCREENER HISTORICAL BACKTEST AUDIT REPORT")
    print("=" * 78)
    print(f"Trading Sessions Evaluated : {len(sorted_dates)} days ({sorted_dates[0]} to {sorted_dates[-1]})")
    print(f"Total F&O Ground Truth Alerts : {total_truth_events}")
    print(f"Total Engine Generated Alerts: {total_engine_events}")
    print(f"Direct Matches (True Positives): {total_matched_events}")
    print(f"Overall Match Rate          : {overall_match_rate:.2f}%")
    print(f"Backtest Execution Time     : {elapsed:.2f}s ({elapsed / len(sorted_dates):.3f}s / day)")
    print("-" * 78)
    print("SUB-STRATEGY BREAKDOWN OF MATCHES:")
    for k, v in sub_strat_counts.items():
        print(f"  * {k:<15} : {v} occurrences")
    print("-" * 78)

    # Print Recent 15 Sessions Match Summary
    print(f"\nRECENT 15 SESSIONS AUDIT TABLE:")
    print(f"{'Date':<12} | {'Chartink Truth':<15} | {'Engine Detected':<16} | {'Matched':<10} | {'Match %':<10}")
    print("-" * 72)
    for r in date_results[-15:]:
        print(f"{r['date']:<12} | {r['truth_count']:<15} | {r['detected_count']:<16} | {r['matched_count']:<10} | {r['match_rate']:>6.1f}%")

    print(f"\nSaved daily metrics to data/chartink_backtest_daily_metrics.csv")
    print(f"Saved matched trades list to data/chartink_backtest_matched_trades.csv")
    print("=" * 78)


if __name__ == "__main__":
    run_backtest()
