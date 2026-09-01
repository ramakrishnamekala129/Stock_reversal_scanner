"""
Unit tests for Candlestick Pattern Recognition with synthetic OHLC candles.
"""

import pandas as pd
import pytest
from patterns.candlestick import (
    CandleItem,
    is_bullish_engulfing,
    is_bullish_harami,
    is_hammer,
    is_inverse_hammer,
    is_hanging_man,
    detect_candlestick_patterns,
)


def test_bullish_engulfing():
    # Previous: Bearish (Open 100, Close 95, High 101, Low 94)
    # Current: Bullish (Open 94, Close 102, High 103, Low 93) -> engulfs previous body (95 to 100)
    prev = CandleItem(open=100.0, high=101.0, low=94.0, close=95.0, volume=1000)
    curr = CandleItem(open=94.0, high=103.0, low=93.0, close=102.0, volume=2000)

    assert is_bullish_engulfing(prev, curr) is True

    # Counter example: Current is bearish
    curr_bearish = CandleItem(open=102.0, high=103.0, low=93.0, close=94.0, volume=2000)
    assert is_bullish_engulfing(prev, curr_bearish) is False


def test_bullish_harami():
    # Previous: Large Bearish (Open 100, Close 90, High 101, Low 89)
    # Current: Small Bullish inside previous body (Open 92, Close 96, High 97, Low 91)
    prev = CandleItem(open=100.0, high=101.0, low=89.0, close=90.0, volume=1000)
    curr = CandleItem(open=92.0, high=97.0, low=91.0, close=96.0, volume=1500)

    assert is_bullish_harami(prev, curr) is True

    # Counter example: Current exceeds previous top
    curr_overflow = CandleItem(open=92.0, high=105.0, low=91.0, close=104.0, volume=1500)
    assert is_bullish_harami(prev, curr_overflow) is False


def test_hammer():
    # Small body at top: Open 100, Close 101, High 101.5, Low 95.0
    # Body = 1.0, Lower Wick = 5.0 (>= 2x body), Upper Wick = 0.5
    c = CandleItem(open=100.0, high=101.5, low=95.0, close=101.0, volume=5000)

    assert is_hammer(c, context="downtrend") is True
    # Should reject if trend is explicit uptrend
    assert is_hammer(c, context="uptrend") is False


def test_inverse_hammer():
    # Small body at bottom: Open 95.0, Close 96.0, High 102.0, Low 94.8
    # Body = 1.0, Upper Wick = 6.0 (>= 2x body), Lower Wick = 0.2
    c = CandleItem(open=95.0, high=102.0, low=94.8, close=96.0, volume=5000)

    assert is_inverse_hammer(c, context="downtrend") is True


def test_hanging_man():
    # Hammer shape at top of uptrend: Open 150, Close 151, High 151.3, Low 145.0
    # Context must be 'uptrend'
    prev = CandleItem(open=145.0, high=149.0, low=144.0, close=148.0, volume=1000)
    curr = CandleItem(open=150.0, high=151.3, low=145.0, close=151.0, volume=3000)

    assert is_hanging_man(prev, curr, context="uptrend") is True
    assert is_hanging_man(prev, curr, context="downtrend") is False


def test_detect_candlestick_patterns_dataframe():
    data = [
        {"timestamp": "2026-09-01 09:15:00", "open": 100.0, "high": 101.0, "low": 94.0, "close": 95.0, "volume": 1000},
        {"timestamp": "2026-09-01 09:20:00", "open": 94.0, "high": 103.0, "low": 93.0, "close": 102.0, "volume": 2000},
    ]
    df = pd.DataFrame(data)
    result = detect_candlestick_patterns(df, context="neutral")

    assert result["bullish_engulfing"] is True
    assert len(result["details"]) == 1
    assert result["details"][0].pattern_name == "BULLISH ENGULFING"


def test_bearish_engulfing():
    # Previous: Bullish (Open 95.0, Close 100.0, High 101.0, Low 94.0)
    # Current: Bearish (Open 102.0, Close 93.0, High 103.0, Low 92.0) -> engulfs previous body (95 to 100)
    prev = CandleItem(open=95.0, high=101.0, low=94.0, close=100.0, volume=1000)
    curr = CandleItem(open=102.0, high=103.0, low=92.0, close=93.0, volume=2000)

    from patterns.candlestick import is_bearish_engulfing
    assert is_bearish_engulfing(prev, curr) is True


def test_bearish_harami():
    # Previous: Large Bullish (Open 90.0, Close 100.0, High 101.0, Low 89.0)
    # Current: Small Bearish inside previous body (Open 97.0, Close 93.0, High 98.0, Low 92.0)
    prev = CandleItem(open=90.0, high=101.0, low=89.0, close=100.0, volume=1000)
    curr = CandleItem(open=97.0, high=98.0, low=92.0, close=93.0, volume=1500)

    from patterns.candlestick import is_bearish_harami
    assert is_bearish_harami(prev, curr) is True


def test_shooting_star():
    # Small body at bottom of range: Open 100.0, Close 99.0, High 106.0, Low 98.8
    # Body = 1.0, Upper Wick = 6.0 (>= 2x body), Lower Wick = 0.2
    c = CandleItem(open=100.0, high=106.0, low=98.8, close=99.0, volume=5000)

    from patterns.candlestick import is_shooting_star
    assert is_shooting_star(c, context="uptrend") is True
    assert is_shooting_star(c, context="downtrend") is False
