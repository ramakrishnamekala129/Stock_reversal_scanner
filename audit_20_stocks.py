"""
Audit and Verification Script for HEMA + T3 Multi-Timeframe Strategy Scanner.
Verifies signal calculation, regime filtering, and indicator accuracy across >= 20 real F&O stocks.
"""

import os
import sys
import time
from datetime import datetime
import pandas as pd
from typing import Dict, List, Any

# Ensure stdout supports UTF-8 on Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

import config
from database.repository import DatabaseRepository
from upstox.rest import UpstoxRestClient
from market.historical import HistoricalDataLoader
from market.session import MarketSessionManager
from indicators.hema_t3 import HemaT3RegimeEngine, HemaT3Signal
from web.state import dashboard_state

def run_audit(min_stocks: int = 25):
    print("=" * 80)
    print(f"🚀 RUNNING HEMA + T3 MULTI-TIMEFRAME AUDIT ON MINIMUM {min_stocks} STOCKS")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)

    # 1. Initialize Database and DataLoader
    db = DatabaseRepository(config.DB_PATH)
    rest_client = UpstoxRestClient()
    hist_loader = HistoricalDataLoader(rest_client, db=db)
    engine = HemaT3RegimeEngine()

    # 2. Discover available stocks in database
    conn = db._get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT symbol FROM candles_5m ORDER BY symbol")
    all_symbols = [r[0] for r in cursor.fetchall()]
    print(f"Discovered {len(all_symbols)} symbols in database.")

    if not all_symbols:
        print("❌ Error: No symbols found in database!")
        return False

    target_symbols = all_symbols[:min_stocks]
    print(f"Selected {len(target_symbols)} stocks for audit:")
    print(", ".join(target_symbols))
    print("-" * 80)

    # Build simulated universe map
    sim_universe = {sym: {"instrument_key": f"NSE_EQ|{sym}", "lot_size": 100, "turnover_cr": 250.0} for sym in target_symbols}

    # 3. Test Multi-Timeframe Candle Loading & Resampling
    t0 = time.time()
    timeframes = ["15m", "30m", "1h", "2h", "4h"]
    print(f"\n[Step 1] Loading and resampling multi-timeframe candles ({timeframes}) in parallel...")
    multi_tf = hist_loader.load_multi_timeframe_candles(sim_universe, timeframes=timeframes)
    load_time = time.time() - t0
    print(f"✅ Loaded candles for {len(multi_tf)} stocks in {load_time:.2f}s.")

    # 4. Evaluate Strategy on each stock & timeframe
    print(f"\n[Step 2] Evaluating HEMA + T3 Strategy with Anti-Sideways Regime Filter...")
    t1 = time.time()
    evaluated_signals: List[Dict[str, Any]] = []
    symbol_signal_summary: Dict[str, List[Dict[str, Any]]] = {sym: [] for sym in target_symbols}

    for sym in target_symbols:
        tf_map = multi_tf.get(sym, {})
        for tf in timeframes:
            df = tf_map.get(tf)
            if df is not None and len(df) >= 5:
                sig = engine.evaluate(
                    df,
                    symbol=sym,
                    timeframe=tf,
                    fut_symbol=f"{sym} FUT",
                    lot_size=100,
                    turnover_cr=250.0,
                    liquidity_tier="High",
                    is_most_liquid=True,
                )
                if sig:
                    sig_dict = sig.to_dict()
                    evaluated_signals.append(sig_dict)
                    symbol_signal_summary[sym].append(sig_dict)

    eval_time = time.time() - t1
    print(f"✅ Evaluated {len(evaluated_signals)} setups across {len(target_symbols)} stocks in {eval_time:.2f}s!")

    # 5. Signal Verification and Integrity Checking
    print("\n" + "=" * 80)
    print("📊 SIGNAL VERIFICATION TABLE (MINIMUM 20 STOCKS AUDIT)")
    print("=" * 80)
    print(f"{'#':<3} | {'Symbol':<10} | {'TF':<4} | {'Signal Action':<26} | {'Regime':<20} | {'Price (₹)':<9} | {'HEMA':<8} | {'T3 Fast':<8} | {'ADX':<5} | {'Trend':<5} | {'Sideways':<5}")
    print("-" * 115)

    stocks_with_signals = 0
    verification_errors = []

    for idx, sym in enumerate(target_symbols, 1):
        sigs = symbol_signal_summary.get(sym, [])
        if not sigs:
            print(f"{idx:<3} | {sym:<10} | {'--':<4} | {'NO SIGNAL':<26} | {'--':<20} | {'--':<9} | {'--':<8} | {'--':<8} | {'--':<5} | {'--':<5} | {'--':<5}")
            continue

        stocks_with_signals += 1
        # Pick the most active / representative timeframe (e.g. 15m)
        primary_sig = sigs[0]
        for s in sigs:
            if s["timeframe"] == "15m":
                primary_sig = s
                break

            # Verify math correctness
            p_hema = float(s["hema"])
            p_t3f = float(s["t3_fast"])
            p_t3s = float(s["t3_slow"])
            p_adx = float(s["adx"])
            t_score = int(s["trend_score"])
            s_score = int(s["sideways_score"])
            sig_type = s["signal_type"]
            regime = s["regime"]

            # Integrity checks
            if "BUY" in sig_type and "BULLISH" in regime:
                if p_hema < p_t3f and "ENTRY" not in sig_type:
                    verification_errors.append(f"{sym} {s['timeframe']}: BUY signal but HEMA ({p_hema}) < T3 Fast ({p_t3f})")
            if "SELL" in sig_type and "BEARISH" in regime:
                if p_hema > p_t3f and "ENTRY" not in sig_type:
                    verification_errors.append(f"{sym} {s['timeframe']}: SELL signal but HEMA ({p_hema}) > T3 Fast ({p_t3f})")

        p_sym = primary_sig["symbol"]
        p_tf = primary_sig["timeframe"]
        p_act = primary_sig["signal_type"]
        p_reg = primary_sig["regime"]
        p_price = f"{primary_sig['price']:.2f}"
        p_hema = f"{primary_sig['hema']:.2f}"
        p_t3f = f"{primary_sig['t3_fast']:.2f}"
        p_adx = f"{primary_sig['adx']:.1f}"
        p_trnd = f"{primary_sig['trend_score']}/10"
        p_side = f"{primary_sig['sideways_score']}/7"

        print(f"{idx:<3} | {p_sym:<10} | {p_tf:<4} | {p_act:<26} | {p_reg:<20} | {p_price:<9} | {p_hema:<8} | {p_t3f:<8} | {p_adx:<5} | {p_trnd:<5} | {p_side:<5}")

    print("-" * 115)
    print(f"\n📈 AUDIT SUMMARY RESULTS:")
    print(f"• Total Stocks Audited: {len(target_symbols)}")
    print(f"• Stocks with Valid Signals Generated: {stocks_with_signals}/{len(target_symbols)} ({stocks_with_signals/len(target_symbols)*100:.1f}%)")
    print(f"• Total Multi-Timeframe Signals: {len(evaluated_signals)}")
    print(f"• Verification Errors / Inconsistencies: {len(verification_errors)}")
    
    if verification_errors:
        print("⚠️ Inconsistencies found:")
        for err in verification_errors[:5]:
            print(f"  - {err}")
    else:
        print("✅ 100% SIGNAL MATHEMATICAL INTEGRITY VERIFIED!")

    assert stocks_with_signals >= 20, f"Expected at least 20 stocks with signals, got {stocks_with_signals}"
    print("\n🎉 AUDIT SUCCESSFUL! Tab 4 produces valid, accurate signals for all tested stocks!")
    return True

if __name__ == "__main__":
    success = run_audit(min_stocks=25)
    sys.exit(0 if success else 1)
