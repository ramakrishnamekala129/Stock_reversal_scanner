import os
import sqlite3
from datetime import datetime, time as dtime
from pathlib import Path
import pandas as pd
import numpy as np

TRADES_CSV = Path("data/quant_new_screener_trades.csv")
DB_PATH = Path("data/historical_candles.db")

t0 = datetime.now()
df_trades = pd.read_csv(TRADES_CSV)
print(f"Loaded {len(df_trades)} trades.")

conn = sqlite3.connect(DB_PATH)
conn.execute("PRAGMA temp_store = MEMORY")
conn.execute("PRAGMA cache_size = -64000")

# Index trades by row index to preserve order
df_trades["orig_idx"] = np.arange(len(df_trades))
results = {}

for sym, group in df_trades.groupby("symbol"):
    df_1m = pd.read_sql_query(
        f"SELECT timestamp, open, high, low, close, volume FROM candles_history_1m WHERE symbol='{sym}' ORDER BY timestamp",
        conn
    )
    if df_1m.empty:
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

        entry_px = float(df_5m.iloc[trig_idx]["close"]) * 1.0005
        tp = entry_px * 1.015
        sl = entry_px * (1.0 - 0.010)  # STOP LOSS 1.0%

        exit_p = None
        exit_r = "EOD"
        exit_bars = 0
        for i in range(trig_idx + 1, len(df_5m)):
            b = df_5m.iloc[i]
            exit_bars += 1
            if float(b["low"]) <= sl:
                exit_p = sl * 0.9995
                exit_r = "SL"
                break
            if float(b["high"]) >= tp:
                exit_p = tp * 0.9995
                exit_r = "TP"
                break
            if b["timestamp"].time() >= dtime(15, 15):
                exit_p = float(b["close"]) * 0.9995
                exit_r = "EOD"
                break

        if exit_p is None:
            exit_p = float(df_5m.iloc[-1]["close"]) * 0.9995
            exit_r = "EOD"

        pnl = ((exit_p - entry_px) / entry_px) * 100.0
        results[orig_i] = (pnl, exit_r, exit_bars)

conn.close()

# Update dataframe columns
df_trades["m1_pnl"] = [results.get(i, (row["m1_pnl"], row["m1_exit_reason"], row["m1_holding_bars"]))[0] for i, row in df_trades.iterrows()]
df_trades["m1_exit_reason"] = [results.get(i, (row["m1_pnl"], row["m1_exit_reason"], row["m1_holding_bars"]))[1] for i, row in df_trades.iterrows()]
df_trades["m1_holding_bars"] = [results.get(i, (row["m1_pnl"], row["m1_exit_reason"], row["m1_holding_bars"]))[2] for i, row in df_trades.iterrows()]
df_trades.drop(columns=["orig_idx"], inplace=True)

df_trades.to_csv(TRADES_CSV, index=False)
print(f"Updated {TRADES_CSV} with Stop Loss = 1.0% in {(datetime.now() - t0).total_seconds():.2f}s.")

# Display comparative results
pnl_all = df_trades["m1_pnl"]
wr_all = (pnl_all > 0).mean() * 100.0
print(f"\nALL 1,390 TRADES (SL 1.0%):")
print(f"  Win Rate      : {wr_all:.2f}%")
print(f"  Total PnL     : {pnl_all.sum():.2f}%")
print(f"  Avg PnL       : {pnl_all.mean():.3f}%")
pf = pnl_all[pnl_all > 0].sum() / abs(pnl_all[pnl_all < 0].sum())
print(f"  Profit Factor : {pf:.2f}")

# Exit Breakdown
print(f"\nEXIT REASONS (SL 1.0%):")
print(df_trades['m1_exit_reason'].value_counts(normalize=True)*100)

# Quant Filter: Post-10 AM + Vol Surge >= 1.1x
q11 = df_trades[(df_trades["trigger_time"] >= "10:00:00") & (df_trades["vol_surge_ratio"] >= 1.1)]
pnl_11 = q11["m1_pnl"]
wr_11 = (pnl_11 > 0).mean() * 100.0
pf_11 = pnl_11[pnl_11 > 0].sum() / abs(pnl_11[pnl_11 < 0].sum()) if abs(pnl_11[pnl_11 < 0].sum()) > 0 else 0
print(f"\nQUANT FILTER (Post-10 AM + Vol Surge >= 1.1x) with SL 1.0%:")
print(f"  Trades        : {len(q11)}")
print(f"  Win Rate      : {wr_11:.2f}%")
print(f"  Total PnL     : {pnl_11.sum():.2f}%")
print(f"  Profit Factor : {pf_11:.2f}")

# Quant Filter: Post-10 AM + Vol Surge >= 1.3x
q13 = df_trades[(df_trades["trigger_time"] >= "10:00:00") & (df_trades["vol_surge_ratio"] >= 1.3)]
pnl_13 = q13["m1_pnl"]
wr_13 = (pnl_13 > 0).mean() * 100.0
pf_13 = pnl_13[pnl_13 > 0].sum() / abs(pnl_13[pnl_13 < 0].sum()) if abs(pnl_13[pnl_13 < 0].sum()) > 0 else 0
print(f"\nQUANT FILTER (Post-10 AM + Vol Surge >= 1.3x) with SL 1.0%:")
print(f"  Trades        : {len(q13)}")
print(f"  Win Rate      : {wr_13:.2f}%")
print(f"  Total PnL     : {pnl_13.sum():.2f}%")
print(f"  Profit Factor : {pf_13:.2f}")
