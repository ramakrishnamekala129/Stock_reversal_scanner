"""
Re-simulates exact 5-minute candle breakout trades with:
- Profit Target: 2.0%
- Stop Loss: 1.0%
- Trailing SL: Active at +1.0%, locking +0.2%, trailing 0.4% from peak
- Intraday EOD Exit: 14:30 IST
Updates data/quant_new_screener_trades.csv.
"""

import sqlite3
from datetime import datetime, time as dtime
import time
import pandas as pd
import numpy as np

TRADES_CSV = "data/quant_new_screener_trades.csv"
DB_PATH = "data/historical_candles.db"

t0 = datetime.now()
df_trades = pd.read_csv(TRADES_CSV)
df_trades["orig_idx"] = range(len(df_trades))

conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)

results_m1 = {}
results_m2 = {}
results_m3 = {}

for sym, group in df_trades.groupby("symbol"):
    df_1m = pd.read_sql_query(
        f"SELECT timestamp, open, high, low, close, volume FROM candles_history_1m WHERE symbol='{sym}' ORDER BY timestamp",
        conn
    )
    if df_1m.empty:
        for _, row in group.iterrows():
            idx = int(row["orig_idx"])
            results_m1[idx] = (row["m1_pnl"], row["m1_exit_reason"], row["m1_holding_bars"])
        continue

    df_1m["timestamp"] = pd.to_datetime(df_1m["timestamp"]).dt.tz_localize(None)
    df_1m["date"] = df_1m["timestamp"].dt.date

    for _, row in group.iterrows():
        orig_i = int(row["orig_idx"])
        t_date = datetime.strptime(row["date"], "%Y-%m-%d").date()
        day_1m = df_1m[df_1m["date"] == t_date]
        if len(day_1m) < 10:
            results_m1[orig_i] = (row["m1_pnl"], row["m1_exit_reason"], row["m1_holding_bars"])
            continue

        df_5m = day_1m.set_index("timestamp").resample("5min", closed="left", label="left").agg({
            "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"
        }).dropna().reset_index()

        trig_time_str = str(row["trigger_time"])
        trig_idx = None
        for idx, b in df_5m.iterrows():
            if b["timestamp"].strftime("%H:%M:%S") == trig_time_str:
                trig_idx = idx
                break

        if trig_idx is None or trig_idx >= len(df_5m) - 1:
            results_m1[orig_i] = (row["m1_pnl"], row["m1_exit_reason"], row["m1_holding_bars"])
            continue

        entry_px = float(row["trigger_price"])
        entry_candle_low = float(df_5m.iloc[trig_idx]["low"])

        # ── Model 1: TP 2.0%, SL 1.0%, Trailing (+1.0%, lock +0.2%, trail 0.4%), EOD 14:30 ──
        tp_1 = entry_px * 1.020
        sl_1 = entry_px * (1.0 - 0.010)
        peak_p1 = entry_px
        trail_active1 = False
        exit_p1 = None
        exit_r1 = "EOD"
        exit_bars1 = 0

        for i in range(trig_idx + 1, len(df_5m)):
            b = df_5m.iloc[i]
            b_h = float(b["high"])
            b_l = float(b["low"])
            b_time = b["timestamp"].time()
            exit_bars1 += 1

            if b_h > peak_p1:
                peak_p1 = b_h

            # Target 2.0%
            if b_h >= tp_1:
                exit_p1 = tp_1 * 0.9995
                exit_r1 = "TP"
                break

            # Trailing stop activation at +1.0%
            if not trail_active1 and (peak_p1 >= entry_px * 1.010):
                trail_active1 = True
                sl_1 = entry_px * 1.002  # Breakeven lock +0.2%

            if trail_active1:
                t_sl = peak_p1 * (1.0 - 0.004)
                if t_sl > sl_1:
                    sl_1 = t_sl

            # Stop loss or trailing stop loss
            if b_l <= sl_1:
                exit_p1 = sl_1 * 0.9995
                exit_r1 = "TRAIL_SL" if trail_active1 else "SL"
                break

            # EOD Exit at 14:30
            if b_time >= dtime(14, 30):
                exit_p1 = float(b["close"]) * 0.9995
                exit_r1 = "EOD"
                break

        if exit_p1 is None:
            exit_p1 = float(df_5m.iloc[-1]["close"]) * 0.9995
            exit_r1 = "EOD"

        pnl_1 = ((exit_p1 - entry_px) / entry_px) * 100.0
        results_m1[orig_i] = (pnl_1, exit_r1, exit_bars1)

conn.close()

# Update dataframe
df_trades["m1_pnl"] = [results_m1.get(i, (row["m1_pnl"], row["m1_exit_reason"], row["m1_holding_bars"]))[0] for i, row in df_trades.iterrows()]
df_trades["m1_exit_reason"] = [results_m1.get(i, (row["m1_pnl"], row["m1_exit_reason"], row["m1_holding_bars"]))[1] for i, row in df_trades.iterrows()]
df_trades["m1_holding_bars"] = [results_m1.get(i, (row["m1_pnl"], row["m1_exit_reason"], row["m1_holding_bars"]))[2] for i, row in df_trades.iterrows()]
df_trades.drop(columns=["orig_idx"], inplace=True)

df_trades.to_csv(TRADES_CSV, index=False)
duration = (datetime.now() - t0).total_seconds()
print(f"Updated {TRADES_CSV} with EOD Exit = 14:30 in {duration:.2f}s.")

# Summary statistics
pnl = df_trades["m1_pnl"]
wins = pnl[pnl > 0]
losses = pnl[pnl < 0]
wr = len(wins) / len(pnl) * 100
pf = wins.sum() / abs(losses.sum())
print(f"Total Trades: {len(pnl)} | Win Rate: {wr:.2f}% | Profit Factor: {pf:.2f} | Mean PnL: {pnl.mean():.3f}%")
print("\nExit Reasons:")
print(df_trades["m1_exit_reason"].value_counts())
