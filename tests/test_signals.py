"""
Unit tests for Signal Engine scoring and Event Deduplication.
"""

from datetime import datetime
import pandas as pd
import pytest
from indicators.pivots import calculate_daily_pivots
from scanner.dedup import EventDeduplicator
from scanner.signal_engine import SignalEngine


def test_signal_scoring_and_generation():
    # Setup Pivots: PP=1000, PDH=1020, PDL=980, R1=1020, S1=980
    pivots = calculate_daily_pivots(
        symbol="SBIN",
        date_str="2026-08-31",
        open_p=990.0,
        high_p=1020.0,
        low_p=980.0,
        close_p=1000.0,
        volume=500000,
    )

    # Bullish engulfing closing above PDH (1020) and Pivot (1000)
    # High volume (2500 vs avg 1000)
    df_data = [
        {"timestamp": datetime(2026, 9, 1, 9, 15), "open": 1010.0, "high": 1012.0, "low": 1000.0, "close": 1002.0, "volume": 1000},
        {"timestamp": datetime(2026, 9, 1, 9, 20), "open": 1000.0, "high": 1025.0, "low": 998.0, "close": 1023.0, "volume": 2500},
    ]
    df = pd.DataFrame(df_data)

    engine = SignalEngine(min_signal_score=4)
    signals = engine.evaluate_candle("SBIN", df, pivots)

    assert len(signals) == 1
    sig = signals[0]
    assert sig.symbol == "SBIN"
    assert sig.pattern == "BULLISH ENGULFING"
    assert sig.direction == "BULLISH SETUP"
    assert sig.score >= 4
    assert "Price > Pivot" in sig.conditions_met
    assert "Price > PDH" in sig.conditions_met
    assert sig.zone != ""
    assert "Above" in sig.zone or "R1" in sig.zone or "CPR" in sig.zone


def test_event_deduplication():
    dedup = EventDeduplicator()
    ts = datetime(2026, 9, 1, 9, 20, 0)

    # First time -> not duplicate
    assert dedup.is_duplicate("INFY", ts, "BULLISH ENGULFING") is False
    dedup.mark_seen("INFY", ts, "BULLISH ENGULFING")

    # Second time with same (symbol, ts, pattern) -> duplicate!
    assert dedup.is_duplicate("INFY", ts, "BULLISH ENGULFING") is True

    # Different pattern for same symbol/ts -> not duplicate
    assert dedup.is_duplicate("INFY", ts, "HAMMER") is False

    # Reset
    dedup.reset()
    assert dedup.is_duplicate("INFY", ts, "BULLISH ENGULFING") is False


def test_conflict_resolution_and_duplicate_suppression():
    pivots = calculate_daily_pivots(
        symbol="HINDALCO",
        date_str="2026-08-31",
        open_p=1010.0,
        high_p=1030.0,
        low_p=990.0,
        close_p=1000.0,
        volume=500000,
    )

    # 1. Low volume small body candle matching both Inverse Hammer & Bearish Harami
    # Should suppress low-volume conflicting signals
    df_low_vol = pd.DataFrame([
        {"timestamp": datetime(2026, 9, 1, 9, 15), "open": 1005.0, "high": 1010.0, "low": 995.0, "close": 998.0, "volume": 10000},
        {"timestamp": datetime(2026, 9, 1, 9, 20), "open": 999.0, "high": 1004.0, "low": 998.5, "close": 999.5, "volume": 500},
    ])
    engine = SignalEngine(min_signal_score=4)
    signals = engine.evaluate_candle("HINDALCO", df_low_vol, pivots)
    # Because rel_vol is very low (<1.0x) and both fire with close scores, it suppresses conflicting chop!
    assert len(signals) <= 1


def test_no_bullish_signal_on_cpr_breakdown():
    # Setup Pivots: PP=2193.67, CPR=2190 to 2197, S1=2137.34, PDL=2156.20
    pivots = calculate_daily_pivots(
        symbol="HYUNDAI",
        date_str="2026-09-01",
        open_p=2250.0,
        high_p=2260.0,
        low_p=2156.2,
        close_p=2165.0,
        volume=500000,
    )

    # Bearish candle breaking down below CPR: Open 2145.6, High 2156.2, Low 2137.7, Close 2140.3
    df = pd.DataFrame([
        {"timestamp": datetime(2026, 9, 2, 9, 20), "open": 2144.8, "high": 2149.1, "low": 2141.2, "close": 2144.2, "volume": 12000},
        {"timestamp": datetime(2026, 9, 2, 9, 25), "open": 2145.6, "high": 2156.2, "low": 2137.7, "close": 2140.3, "volume": 17600},
    ])
    engine = SignalEngine(min_signal_score=4)
    signals = engine.evaluate_candle("HYUNDAI", df, pivots)

    # Must NOT produce any Bullish Setup during a CPR breakdown!
    bullish_signals = [s for s in signals if "BULLISH" in s.direction]
    assert len(bullish_signals) == 0


def test_harami_low_volume_and_breakout_protection():
    from scanner.signal_engine import SignalEngine
    pivots = calculate_daily_pivots(
        symbol="SOLARINDS",
        date_str="2026-09-01",
        open_p=20060.0,
        high_p=20270.0,
        low_p=19930.0,
        close_p=20060.0,
        volume=10000,
    )
    # R1 = 20243.34, PDH = 20270.0
    # 1. Breakout above PDH and R1 at 20290: Must NOT produce Bearish Warning
    df_breakout = pd.DataFrame([
        {"timestamp": datetime(2026, 9, 2, 12, 30), "open": 20260.0, "high": 20295.0, "low": 20255.0, "close": 20295.0, "volume": 1230},
        {"timestamp": datetime(2026, 9, 2, 12, 35), "open": 20290.0, "high": 20295.0, "low": 20275.0, "close": 20290.0, "volume": 1099},
    ])
    engine = SignalEngine(min_signal_score=4)
    sig_breakout = engine.evaluate_candle("SOLARINDS", df_breakout, pivots)
    bear_sig = [s for s in sig_breakout if "BEARISH" in s.direction]
    assert len(bear_sig) == 0

    # 2. Low-volume Harami inside chop (Rel Vol < 0.75x) must be suppressed
    df_chop = pd.DataFrame([
        {"timestamp": datetime(2026, 9, 2, 10, 20), "open": 19900.0, "high": 19930.0, "low": 19890.0, "close": 19920.0, "volume": 10000},
        {"timestamp": datetime(2026, 9, 2, 10, 25), "open": 19915.0, "high": 19920.0, "low": 19905.0, "close": 19910.0, "volume": 2500},
    ])
    # Relative volume ~ 0.25x
    sig_chop = engine.evaluate_candle("SOLARINDS", df_chop, pivots)
    assert len(sig_chop) == 0


def test_bear_trap_breakout_detection():
    from scanner.signal_engine import SignalEngine
    # SOLARINDS S1 = 19903.34, PDL = 19930.0 -> Bear trap = 19903.34 to 19930.0
    pivots = calculate_daily_pivots(
        symbol="SOLARINDS",
        date_str="2026-09-01",
        open_p=20060.0,
        high_p=20270.0,
        low_p=19930.0,
        close_p=20060.0,
        volume=10000,
    )
    # 11:35 candle: opens at 19895 (in trap), surges to 20030 (above PDL) with massive volume (2368)
    df = pd.DataFrame([
        {"timestamp": datetime(2026, 9, 2, 11, 30), "open": 19900.0, "high": 19915.0, "low": 19900.0, "close": 19910.0, "volume": 399},
        {"timestamp": datetime(2026, 9, 2, 11, 35), "open": 19895.0, "high": 20035.0, "low": 19890.0, "close": 20030.0, "volume": 2368},
    ])
    engine = SignalEngine(min_signal_score=4)
    signals = engine.evaluate_candle("SOLARINDS", df, pivots)

    assert len(signals) >= 1
    sig = signals[0]
    assert sig.direction == "BULLISH SETUP"
    assert "Bear Trap Breakout" in sig.zone
    assert sig.score >= 10
    assert sig.candle_high == 20035.0
    assert sig.candle_low == 19890.0
    assert sig.trigger_status == "PENDING"
    assert sig.trigger_price == 20035.0


def test_signal_trigger_tracker():
    from scanner.trigger_tracker import SignalTriggerTracker

    tracker = SignalTriggerTracker()

    # 1. Register a Bullish Setup
    bull_sig = {
        "symbol": "RELIANCE",
        "timestamp": "2026-09-02T10:00:00",
        "pattern": "HAMMER",
        "direction": "BULLISH SETUP",
        "candle_high": 1290.0,
        "candle_low": 1275.0,
        "trigger_status": "PENDING",
    }
    tracker.register_signal(bull_sig)

    # Next candle doesn't break high or low -> still pending
    res1 = tracker.check_candle_triggers(
        symbol="RELIANCE",
        candle_high=1288.0,
        candle_low=1280.0,
        candle_close=1285.0,
        candle_timestamp="2026-09-02T10:05:00",
    )
    assert len(res1) == 0
    assert bull_sig["trigger_status"] == "PENDING"

    # Next candle crosses above 1290.0 -> TRIGGERED!
    res2 = tracker.check_candle_triggers(
        symbol="RELIANCE",
        candle_high=1295.0,
        candle_low=1282.0,
        candle_close=1292.0,
        candle_timestamp="2026-09-02T10:10:00",
    )
    assert len(res2) == 1
    assert bull_sig["trigger_status"] == "TRIGGERED"
    assert bull_sig["trigger_time"] == "10:10:00"

    # 2. Register a Bearish Setup that gets invalidated
    bear_sig = {
        "symbol": "TCS",
        "timestamp": "2026-09-02T11:00:00",
        "pattern": "SHOOTING STAR",
        "direction": "BEARISH WARNING",
        "candle_high": 3550.0,
        "candle_low": 3530.0,
        "trigger_status": "PENDING",
    }
    tracker.register_signal(bear_sig)

    # Next candle breaks above setup high (3550.0) -> INVALIDATED!
    res3 = tracker.check_candle_triggers(
        symbol="TCS",
        candle_high=3555.0,
        candle_low=3535.0,
        candle_close=3552.0,
        candle_timestamp="2026-09-02T11:05:00",
    )
    assert len(res3) == 1
    assert bear_sig["trigger_status"] == "INVALIDATED"


def test_s2_support_bounce_pin_bar():
    from scanner.signal_engine import SignalEngine
    # 360ONE pivots on Sep 02: PP=1165.8, S1=1147.3, S2=1129.0
    pivots = calculate_daily_pivots(
        symbol="360ONE",
        date_str="2026-09-01",
        open_p=1175.0,
        high_p=1184.3,
        low_p=1147.5,
        close_p=1165.6,
        volume=2175285,
    )
    # 09:45 candle testing S2 (1129.6 vs 1129.0) with Pin Bar lower wick
    df = pd.DataFrame([
        {"timestamp": datetime(2026, 9, 2, 9, 40), "open": 1133.1, "high": 1136.3, "low": 1132.8, "close": 1135.8, "volume": 5548},
        {"timestamp": datetime(2026, 9, 2, 9, 45), "open": 1135.2, "high": 1135.9, "low": 1129.6, "close": 1132.6, "volume": 16317},
    ])
    engine = SignalEngine(min_signal_score=4)
    signals = engine.evaluate_candle("360ONE", df, pivots)
    assert len(signals) >= 1
    sig = signals[0]
    assert sig.direction == "BULLISH SETUP"
    assert sig.pattern == "PIN_BAR"
    assert "S2 Support" in sig.zone or any("S2 Support" in c for c in sig.conditions_met)
    assert sig.score >= 7
    assert sig.candle_high == 1135.9
    assert sig.candle_low == 1129.6
    assert sig.trigger_status == "PENDING"
    assert sig.trigger_price == 1135.9


def test_r2_resistance_rejection_bearish_pin_bar():
    from scanner.signal_engine import SignalEngine
    # H=100.0, L=90.0, C=95.0 -> PP=95.0, R1=2*95-90=100.0, R2=95+(100-90)=105.0
    pivots = calculate_daily_pivots(
        symbol="TEST_R2",
        date_str="2026-09-01",
        open_p=92.0,
        high_p=100.0,
        low_p=90.0,
        close_p=95.0,
        volume=10000,
    )
    # Candle tests R2 (High 105.2 vs R2 105.0) and gets rejected with long upper wick
    df = pd.DataFrame([
        {"timestamp": datetime(2026, 9, 2, 10, 0), "open": 102.0, "high": 104.0, "low": 101.5, "close": 103.5, "volume": 5000},
        {"timestamp": datetime(2026, 9, 2, 10, 5), "open": 103.8, "high": 105.2, "low": 103.0, "close": 103.2, "volume": 12000},
    ])
    engine = SignalEngine(min_signal_score=4)
    signals = engine.evaluate_candle("TEST_R2", df, pivots)
    assert len(signals) >= 1
    sig = signals[0]
    assert sig.direction == "BEARISH WARNING"
    assert sig.pattern in ["SHOOTING STAR", "BEARISH_PIN_BAR", "INVERSE HAMMER"]
    assert "R2 Resistance" in sig.zone or any("R2 Resistance" in c for c in sig.conditions_met)
    assert sig.score >= 7
    assert sig.candle_high == 105.2
    assert sig.candle_low == 103.0
    assert sig.trigger_status == "PENDING"
    assert sig.trigger_price == 103.0
