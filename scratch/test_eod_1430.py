import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime, time as dtime

TRADES_CSV = "data/quant_new_screener_trades.csv"
DB_PATH = "data/historical_candles.db"

df_trades = pd.read_csv(TRADES_CSV)
df_trades["orig_idx"] = range(len(df_trades))

conn = sqlite3.connect(DB_PATH)
symbols = df_trades["symbol"].unique()
results = {}

for sym, group in df_trades.groupby("symbol"):
    df_1m = pd.read_sql_query(
        f"SELECT timestamp, open, high, low, close, volume FROM candles_history_1m WHERE symbol='{sym}' ORDER BY timestamp",
        conn
    )
    if df_1m.empty:
        for _, row in group.iterrows():
            results[int(row["orig_idx"])] = (row["m1_pnl"], row["m1_exit_reason"], row["m1_holding_bars"])
        continue

    df_1m["timestamp"] = pd.to_datetime(df_1m["timestamp"]).dt.tz_localize(None)
    df_1m["date"] = df_1m["timestamp"].dt.date

    for _, row in group.iterrows():
        orig_i = int(row["orig_idx"])
        t_date = datetime.strptime(row["date"], "%Y-%m-%d").date()
        day_1m = df_1m[df_1m["date"] == t_date]
        if len(day_1m) < 10:
            results[orig_i] = (row["m1_pnl"], row["m1_exit_reason"], row["m1_holding_bars"])
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
            results[orig_i] = (row["m1_pnl"], row["m1_exit_reason"], row["m1_holding_bars"])
            continue

        entry_px = float(row["trigger_price"])
        tp = entry_px * 1.020
        sl = entry_px * (1.0 - 0.010)
        peak_p = entry_px
        trail_active = False

        exit_p = None
        exit_r = "EOD"
        exit_bars = 0
        for i in range(trig_idx + 1, len(df_5m)):
            b = df_5m.iloc[i]
            b_h = float(b["high"])
            b_l = float(b["low"])
            exit_bars += 1

            if b_h > peak_p:
                peak_p = b_h

            # Hit Target 2.0%
            if b_h >= tp:
                exit_p = tp * 0.9995
                exit_r = "TP"
                break

            # Activate trailing at +1.0%
            if not trail_active and (peak_p >= entry_px * 1.010):
                trail_active = True
                sl = entry_px * 1.002  # Lock breakeven +0.2%

            if trail_active:
                t_sl = peak_p * (1.0 - 0.004)
                if t_sl > sl:
                    sl = t_sl

            # Stop or trailing stop
            if b_l <= sl:
                exit_p = sl * 0.9995
                exit_r = "TRAIL_SL" if trail_active else "SL"
                break

            # EOD Exit set to 14:30
            if b["timestamp"].time() >= dtime(14, 30):
                exit_p = float(b["close"]) * 0.9995
                exit_r = "EOD"
                break

        if exit_p is None:
            exit_p = float(df_5m.iloc[-1]["close"]) * 0.9995
            exit_r = "EOD"

        pnl = ((exit_p - entry_px) / entry_px) * 100.0
        results[orig_i] = (pnl, exit_r, exit_bars)

conn.close()

df_sim = df_trades.copy()
df_sim["new_pnl"] = [results[i][0] for i in range(len(df_sim))]
df_sim["new_reason"] = [results[i][1] for i in range(len(df_sim))]
df_sim["new_bars"] = [results[i][2] for i in range(len(df_sim))]

print("\n--- COMPARISON (All 1390 Trades) ---")
print(f"Old EOD 15:15 Mean PnL: {df_sim['m1_pnl'].mean():.3f}%, Win Rate: {(df_sim['m1_pnl'] > 0).mean() * 100:.2f}%")
print(f"New EOD 14:30 Mean PnL: {df_sim['new_pnl'].mean():.3f}%, Win Rate: {(df_sim['new_pnl'] > 0).mean() * 100:.2f}%")

print("\n--- EXIT REASONS BREAKDOWN ---")
print("Old 15:15 Exits:")
print(df_sim["m1_exit_reason"].value_counts())
print("\nNew 14:30 Exits:")
print(df_sim["new_reason"].value_counts())

# What about trades triggered before 14:30?
df_before_1430 = df_sim[pd.to_datetime(df_sim['trigger_time']).dt.time < dtime(14, 30)]
print(f"\n--- TRADES TRIGGERED BEFORE 14:30 (n={len(df_before_1430)}) ---")
print(f"Old 15:15 Mean PnL: {df_before_1430['m1_pnl'].mean():.3f}%, Win Rate: {(df_before_1430['m1_pnl'] > 0).mean() * 100:.2f}%")
print(f"New 14:30 Mean PnL: {df_before_1430['new_pnl'].mean():.3f}%, Win Rate: {(df_before_1430['new_pnl'] > 0).mean() * 100:.2f}%")

# Quant model: Post 10:00 AM + Vol Surge >= 1.1x
df_q = df_sim[(pd.to_datetime(df_sim['trigger_time']).dt.time >= dtime(10, 0)) & (df_sim['vol_surge_ratio'] >= 1.1)]
print(f"\n--- QUANT MODEL (Post 10:00 AM + Vol >= 1.1x, n={len(df_q)}) ---")
print(f"Old 15:15 Mean PnL: {df_q['m1_pnl'].mean():.3f}%, Win Rate: {(df_q['m1_pnl'] > 0).mean() * 100:.2f}%")
print(f"New 14:30 Mean PnL: {df_q['new_pnl'].mean():.3f}%, Win Rate: {(df_q['new_pnl'] > 0).mean() * 100:.2f}%")

df_q13 = df_sim[(pd.to_datetime(df_sim['trigger_time']).dt.time >= dtime(10, 0)) & (df_sim['vol_surge_ratio'] >= 1.3)]
print(f"\n--- QUANT MODEL (Post 10:00 AM + Vol >= 1.3x, n={len(df_q13)}) ---")
print(f"Old 15:15 Mean PnL: {df_q13['m1_pnl'].mean():.3f}%, Win Rate: {(df_q13['m1_pnl'] > 0).mean() * 100:.2f}%")
print(f"New 14:30 Mean PnL: {df_q13['new_pnl'].mean():.3f}%, Win Rate: {(df_q13['new_pnl'] > 0).mean() * 100:.2f}%")
