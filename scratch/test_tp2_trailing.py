import os
import sqlite3
from datetime import datetime, time as dtime
from pathlib import Path
import pandas as pd
import numpy as np

TRADES_CSV = Path("data/quant_new_screener_trades.csv")
DB_PATH = Path("data/historical_candles.db")

df_trades = pd.read_csv(TRADES_CSV)
print(f"Loaded {len(df_trades)} trades.")

conn = sqlite3.connect(DB_PATH)
conn.execute("PRAGMA temp_store = MEMORY")
conn.execute("PRAGMA cache_size = -64000")

# Index trades by row index to preserve order
df_trades["orig_idx"] = np.arange(len(df_trades))

configs = [
    {"name": "TP 2.0% + Trail 0.4% (SL 1.0%)", "tp": 0.020, "sl": 0.010, "trail_act": 0.010, "trail_offset": 0.004},
    {"name": "TP 2.0% + Trail 0.5% (SL 1.0%)", "tp": 0.020, "sl": 0.010, "trail_act": 0.010, "trail_offset": 0.005},
    {"name": "TP 2.0% + Trail 0.6% (SL 1.0%)", "tp": 0.020, "sl": 0.010, "trail_act": 0.010, "trail_offset": 0.006},
]

for cfg in configs:
    t0 = datetime.now()
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
                continue

            entry_px = float(df_5m.iloc[trig_idx]["close"]) * 1.0005
            tp = entry_px * (1.0 + cfg["tp"])
            sl = entry_px * (1.0 - cfg["sl"])
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

                # Hit Target 2%
                if b_h >= tp:
                    exit_p = tp * 0.9995
                    exit_r = "TP"
                    break

                # Activate trailing when price reaches +1.0%
                if not trail_active and (peak_p >= entry_px * (1.0 + cfg["trail_act"])):
                    trail_active = True
                    sl = entry_px * 1.002  # Lock breakeven +0.2%

                if trail_active:
                    t_sl = peak_p * (1.0 - cfg["trail_offset"])
                    if t_sl > sl:
                        sl = t_sl

                # Stop or trailing stop
                if b_l <= sl:
                    exit_p = sl * 0.9995
                    exit_r = "TRAIL_SL" if trail_active else "SL"
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

    pnls = [results.get(i, (0, "NONE", 0))[0] for i in range(len(df_trades))]
    exits = [results.get(i, (0, "NONE", 0))[1] for i in range(len(df_trades))]
    s = pd.Series(pnls)
    wr = (s > 0).mean() * 100
    tot = s.sum()
    pf = s[s > 0].sum() / abs(s[s < 0].sum()) if abs(s[s < 0].sum()) > 0 else 0

    print(f"\n{'='*70}")
    print(f"CONFIGURATION: {cfg['name']}")
    print(f"  Execution Time: {(datetime.now() - t0).total_seconds():.1f}s")
    print(f"  Trades        : {len(s)}")
    print(f"  Win Rate      : {wr:.2f}%")
    print(f"  Total PnL     : {tot:.2f}%")
    print(f"  Avg PnL       : {s.mean():.3f}%")
    print(f"  Profit Factor : {pf:.2f}")
    print(f"  Exit Breakdown:")
    for r_name, r_pct in pd.Series(exits).value_counts(normalize=True).items():
        print(f"    {r_name:12s}: {r_pct*100:.2f}%")

    # Quant Model: Post-10 AM + Vol Surge >= 1.1x
    df_temp = df_trades.copy()
    df_temp["pnl"] = pnls
    q11 = df_temp[(df_temp["trigger_time"] >= "10:00:00") & (df_temp["vol_surge_ratio"] >= 1.1)]
    s11 = q11["pnl"]
    wr11 = (s11 > 0).mean() * 100
    pf11 = s11[s11 > 0].sum() / abs(s11[s11 < 0].sum()) if abs(s11[s11 < 0].sum()) > 0 else 0
    print(f"  Quant Model (Post-10 AM + Vol Surge >= 1.1x):")
    print(f"    Trades: {len(s11)} | Win Rate: {wr11:.2f}% | Total PnL: {s11.sum():.2f}% | Profit Factor: {pf11:.2f}")

conn.close()
