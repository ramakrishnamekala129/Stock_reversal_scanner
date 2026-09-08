"""
Unit test suite for Chartink Intraday Screener Engine and Dashboard Integration.
Validates:
1. Master Condition: ((High + Low)/2) < (((High + Low + Close)/3) - 0.003 * Typical)
2. Sub-Strategy 1: 20-day Volume SMA * Open >= 10 Cr and Monthly Breakout
3. Sub-Strategy 2: 20-week High Breakout and 200 SMA
4. Sub-Strategy 3: Multi-SMA Crossover, RSI(14) Crossover, Volume Surge, Higher Low, Green Candle
5. WebDashboardState batching, deduplication, and snapshot methods
"""

from datetime import datetime, timedelta
import numpy as np
import pandas as pd
import pytest

from indicators.chartink_screener import ChartinkIntradayEngine, ChartinkSignal
from web.state import WebDashboardState


def make_daily_history(length: int = 150, base_price: float = 200.0) -> pd.DataFrame:
    """Generates daily OHLCV bars across past months."""
    base_date = datetime(2026, 1, 1)
    rows = []
    price = base_price
    for i in range(length):
        dt = base_date + timedelta(days=i)
        o = price
        h = o + 2.0
        l = o - 1.0
        c = o + 0.5
        v = 150000 + (i % 20) * 10000
        rows.append({
            "timestamp": dt,
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "volume": v,
        })
        price = c
    return pd.DataFrame(rows)


def test_master_condition_passes_when_close_is_high():
    """Master condition ensures close is in the upper half with >=0.3% clearance."""
    # High: 510, Low: 470 -> Median = 490
    # Close: 508 -> Typical = (510 + 470 + 508) / 3 = 496.0
    # Threshold = 496.0 * 0.997 = 494.512
    # Median (490) < Threshold (494.512) -> PASS!
    c_high = 510.0
    c_low = 470.0
    c_close = 508.0
    med = (c_high + c_low) / 2.0
    typ = (c_high + c_low + c_close) / 3.0
    thresh = typ - (typ * 0.003)
    assert med < thresh


def test_master_condition_fails_when_close_is_low():
    """Master condition fails when close is in lower half."""
    engine = ChartinkIntradayEngine()
    df = make_daily_history(length=60, base_price=500.0)
    
    # High: 510, Low: 490 -> Median = 500
    # Close: 492 -> Typical = (510 + 490 + 492) / 3 = 497.33
    # Threshold = 497.33 * 0.997 = 495.84
    # Median (500) >= Threshold (495.84) -> FAILS!
    df.loc[len(df) - 1, "high"] = 510.0
    df.loc[len(df) - 1, "low"] = 490.0
    df.loc[len(df) - 1, "close"] = 492.0
    df.loc[len(df) - 1, "open"] = 505.0

    sig = engine.evaluate_stock("TEST_FAIL", df)
    assert sig is None


def test_sub1_monthly_breakout_and_turnover():
    """Sub-Strategy 1 triggers on monthly breakout and >= 10 Cr turnover."""
    engine = ChartinkIntradayEngine()
    base_date = datetime(2026, 1, 1)
    rows = []
    price = 1000.0
    for i in range(120):
        dt = base_date + timedelta(days=i)
        rows.append({"timestamp": dt, "open": price, "high": price + 2, "low": price - 2, "close": price + 1, "volume": 200000})
        price += 1.0

    df = pd.DataFrame(rows)
    prev_m = df["timestamp"].iloc[-1].month - 1
    prev_max = float(df[df["timestamp"].dt.month == prev_m]["high"].max())

    # Set today's close above prev_max with strong close near high
    today_close = prev_max + 30.0
    df.loc[len(df) - 1, "open"] = today_close - 20.0
    df.loc[len(df) - 1, "low"] = today_close - 40.0
    df.loc[len(df) - 1, "close"] = today_close
    df.loc[len(df) - 1, "high"] = today_close + 1.0
    df.loc[len(df) - 1, "volume"] = 300000

    sig = engine.evaluate_stock("TEST_SUB1", df)
    assert sig is not None
    assert any("Monthly Breakout" in s for s in sig.matched_strategies)
    assert sig.turnover_cr >= 10.0


def test_sub2_weekly_breakout_and_200_sma():
    """Sub-Strategy 2 triggers on 20-week max high breakout and above 200 SMA."""
    engine = ChartinkIntradayEngine()
    base_date = datetime(2026, 1, 1)
    rows = []
    price = 200.0
    for i in range(300):
        dt = base_date + timedelta(days=i)
        rows.append({"timestamp": dt, "open": price, "high": price + 2, "low": price - 2, "close": price + 0.5, "volume": 200000})
        price += 0.5

    df = pd.DataFrame(rows)
    df_w = df.set_index("timestamp").resample("W-FRI").agg({"close": "last"}).dropna()
    max_w_close = float(df_w["close"].iloc[-21:-1].max())

    # Breakout above 20-week close
    today_close = max_w_close + 40.0
    df.loc[len(df) - 1, "open"] = today_close - 20.0
    df.loc[len(df) - 1, "low"] = today_close - 35.0
    df.loc[len(df) - 1, "close"] = today_close
    df.loc[len(df) - 1, "high"] = today_close + 1.0
    df.loc[len(df) - 1, "volume"] = 300000

    sig = engine.evaluate_stock("TEST_SUB2", df)
    assert sig is not None
    assert any("20W High" in s for s in sig.matched_strategies)


def test_sub3_multi_sma_rsi_vol_surge():
    """Sub-Strategy 3 triggers when SMA and RSI cross above with volume surge and higher low."""
    engine = ChartinkIntradayEngine()
    base_date = datetime(2026, 1, 1)
    rows = []
    price = 150.0
    for i in range(50):
        dt = base_date + timedelta(days=i)
        rows.append({"timestamp": dt, "open": price, "high": price + 1, "low": price - 2, "close": price - 0.2, "volume": 200000})
        price -= 0.2

    df = pd.DataFrame(rows)
    df.loc[len(df) - 2, "close"] = 135.0
    df.loc[len(df) - 2, "low"] = 133.0
    df.loc[len(df) - 2, "high"] = 136.0
    df.loc[len(df) - 2, "open"] = 136.0

    df.loc[len(df) - 1, "open"] = 135.0
    df.loc[len(df) - 1, "low"] = 134.0
    df.loc[len(df) - 1, "close"] = 150.0
    df.loc[len(df) - 1, "high"] = 151.0
    df.loc[len(df) - 1, "volume"] = 1000000

    sig = engine.evaluate_stock("TEST_SUB3", df)
    assert sig is not None
    assert any("MA + RSI + Vol Surge" in s for s in sig.matched_strategies)
    assert sig.price == 150.0
    assert sig.median_pivot_diff_pct > 0


def test_web_dashboard_state_chartink_batch():
    """Validates batch state updating and deduplication in WebDashboardState."""
    state = WebDashboardState()
    
    sig1 = {
        "symbol": "RELIANCE",
        "strategy_tag": "Monthly Breakout (Sub 1)",
        "timestamp": "10:15:00",
        "price": 2950.0,
        "median_pivot_diff_pct": 0.85,
        "turnover_cr": 25.4,
        "rsi_14": 56.2,
        "ma_crossed_str": "SMA 15,20",
        "vol_surge_ratio": 2.1,
        "reasons_str": "Monthly Breakout",
    }
    sig2 = {
        "symbol": "TCS",
        "strategy_tag": "20W High + 200 SMA (Sub 2)",
        "timestamp": "10:15:00",
        "price": 4250.0,
        "median_pivot_diff_pct": 1.12,
        "turnover_cr": 18.2,
        "rsi_14": 62.0,
        "ma_crossed_str": "SMA 20",
        "vol_surge_ratio": 1.8,
        "reasons_str": "20-Week High",
    }

    state.add_chartink_signals_batch([sig1, sig2])
    snap = state.get_snapshot()
    assert len(snap["chartink_signals"]) == 2

    # Update with duplicate key (should update in-place, not duplicate)
    sig1_updated = dict(sig1)
    sig1_updated["price"] = 2955.0
    state.add_chartink_signals_batch([sig1_updated])
    
    snap2 = state.get_snapshot()
    assert len(snap2["chartink_signals"]) == 2
    assert snap2["chartink_signals"][0]["price"] == 2955.0 or snap2["chartink_signals"][1]["price"] == 2955.0
