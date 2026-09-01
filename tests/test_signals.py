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
