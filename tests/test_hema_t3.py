"""
Unit tests for HEMA + T3 Strict Buy Sell with Anti-Sideways / Market-Regime Filter.
"""

from datetime import datetime, timedelta
import numpy as np
import pandas as pd
import pytest

from indicators.hema_t3 import (
    calculate_wma,
    calculate_sma,
    calculate_ema,
    calculate_t3,
    calculate_hema,
    calculate_adx,
    calculate_atr,
    calculate_vwap,
    HemaT3RegimeEngine,
    HemaT3Signal,
)
from web.state import WebDashboardState


def generate_synthetic_trend(length: int = 50, trend_type: str = "uptrend") -> pd.DataFrame:
    """Generates synthetic OHLCV data for testing."""
    base_time = datetime(2026, 9, 7, 9, 15)
    rows = []
    price = 1000.0

    for i in range(length):
        ts = base_time + timedelta(minutes=15 * i)
        if trend_type == "uptrend":
            delta = 2.0 + np.random.uniform(0.1, 1.0)
            open_p = price
            close_p = open_p + delta
            high_p = close_p + 1.0
            low_p = open_p - 0.5
            vol = 5000 + i * 100
        elif trend_type == "downtrend":
            delta = -2.0 - np.random.uniform(0.1, 1.0)
            open_p = price
            close_p = open_p + delta
            high_p = open_p + 0.5
            low_p = close_p - 1.0
            vol = 5000 + i * 100
        else:  # sideways / choppy
            open_p = 1000.0 + (i % 2) * 0.2
            close_p = 1000.0 - (i % 2) * 0.2
            high_p = 1000.5
            low_p = 999.5
            vol = 500  # low volume
        
        price = close_p
        rows.append({
            "timestamp": ts,
            "open": open_p,
            "high": high_p,
            "low": low_p,
            "close": close_p,
            "volume": vol,
        })
    return pd.DataFrame(rows)


def test_wma_sma_ema():
    series = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    wma = calculate_wma(series, 3)
    assert not wma.empty
    # WMA(3) for [1, 2, 3] = (1*1 + 2*2 + 3*3) / (1+2+3) = (1 + 4 + 9) / 6 = 14/6 = 2.3333
    assert abs(wma.iloc[2] - 14.0 / 6.0) < 1e-4

    sma = calculate_sma(series, 3)
    assert abs(sma.iloc[2] - 2.0) < 1e-4

    ema = calculate_ema(series, 3)
    assert not ema.empty
    assert len(ema) == len(series)


def test_t3_and_hema():
    df = generate_synthetic_trend(length=40, trend_type="uptrend")
    close = df["close"]

    t3 = calculate_t3(close, length=13, b=0.7)
    assert len(t3) == len(close)
    assert t3.iloc[-1] > t3.iloc[15]  # upward trajectory

    hema, hsma, diff = calculate_hema(close, length=9)
    assert len(hema) == len(close)
    assert len(diff) == len(close)
    # In a strong uptrend, HEMA should be above HSMA, diff > 0
    assert diff.iloc[-1] > 0


def test_adx_and_atr():
    df = generate_synthetic_trend(length=50, trend_type="uptrend")
    adx, plus_di, minus_di = calculate_adx(df, length=14)
    atr = calculate_atr(df, length=14)

    assert len(adx) == len(df)
    assert len(atr) == len(df)
    assert adx.iloc[-1] > 0
    assert atr.iloc[-1] > 0


def test_engine_strong_uptrend():
    df = generate_synthetic_trend(length=60, trend_type="uptrend")
    engine = HemaT3RegimeEngine(min_analytic_score=6, sideways_score_cutoff=3)
    
    sig = engine.evaluate(df, symbol="RELIANCE", timeframe="15m")
    assert sig is not None
    assert isinstance(sig, HemaT3Signal)
    assert sig.symbol == "RELIANCE"
    assert sig.timeframe == "15m"
    assert "BULLISH" in sig.signal or "TRENDING" in sig.regime
    assert sig.trend_score >= 6
    assert sig.sideways_score < 3
    assert sig.is_sideways is False


def test_engine_sideways_compression():
    df = generate_synthetic_trend(length=60, trend_type="sideways")
    engine = HemaT3RegimeEngine(min_analytic_score=7, sideways_score_cutoff=3)

    sig = engine.evaluate(df, symbol="TCS", timeframe="1h")
    assert sig is not None
    assert sig.symbol == "TCS"
    assert sig.timeframe == "1h"
    assert sig.sideways_score >= 3
    assert "SIDEWAYS" in sig.regime
    assert sig.is_sideways is True


def test_multi_timeframe_support():
    engine = HemaT3RegimeEngine()
    for tf in ["15m", "30m", "1h", "2h", "4h", "1d"]:
        df = generate_synthetic_trend(length=40, trend_type="uptrend")
        sig = engine.evaluate(df, symbol="INFY", timeframe=tf)
        assert sig is not None
        assert sig.timeframe == tf
        d = sig.to_dict()
        assert d["timeframe"] == tf
        assert "trend_score" in d
        assert "sideways_score" in d


def test_web_state_hema_signal_dispatch():
    state = WebDashboardState()
    assert state.hema_signals == []

    sig_dict = {
        "timestamp": datetime.now(),
        "symbol": "SBIN",
        "timeframe": "15m",
        "signal_type": "🟢 BUY (CALL Entry)",
        "regime": "🚀 STRONG UPTREND",
        "price": 820.5,
        "trend_score": 9,
        "sideways_score": 0,
        "hema": 818.0,
        "t3_fast": 816.0,
        "t3_slow": 814.0,
        "adx": 32.5,
        "atr_ratio": 1.25,
        "ema_slope_pct": 0.08,
        "consolidation_compression_pct": 1.8,
        "volume_ratio": 1.6,
        "conditions_met": ["HEMA > T3 Fast", "ADX > 20", "Trend Score 9/10"],
    }

    state.add_hema_signal(sig_dict)
    assert len(state.hema_signals) == 1
    assert state.hema_signals[0]["symbol"] == "SBIN"

    snapshot = state.get_snapshot()
    assert "hema_signals" in snapshot
    assert len(snapshot["hema_signals"]) == 1
    assert snapshot["hema_signals"][0]["symbol"] == "SBIN"


def test_evaluate_all_signals_full_day():
    engine = HemaT3RegimeEngine()
    df = generate_synthetic_trend(length=30, trend_type="uptrend")
    
    signals = engine.evaluate_all_signals(df, symbol="RELIANCE", timeframe="15m")
    assert isinstance(signals, list)
    assert len(signals) >= 1
    for s in signals:
        assert s.symbol == "RELIANCE"
        assert s.timeframe == "15m"
        assert s.timestamp != ""


def test_web_state_full_day_multiple_timestamps():
    state = WebDashboardState()
    # Simulate signals from multiple times in the same day for the same symbol
    sig1 = {
        "timestamp": "09:30:00",
        "symbol": "TCS",
        "timeframe": "15m",
        "signal_type": "🟢 BUY (BULLISH ENTRY)",
        "price": 3500.0,
    }
    sig2 = {
        "timestamp": "10:15:00",
        "symbol": "TCS",
        "timeframe": "15m",
        "signal_type": "🟢 BUY TREND (BULLISH HOLD)",
        "price": 3520.0,
    }
    sig3 = {
        "timestamp": "15:15:00",
        "symbol": "TCS",
        "timeframe": "15m",
        "signal_type": "🟢 BUY TREND (BULLISH HOLD)",
        "price": 3550.0,
    }
    state.add_hema_signals_batch([sig1, sig2, sig3])
    # All 3 timestamps should be preserved, not collapsed to only 15:15:00!
    assert len(state.hema_signals) == 3
    times = [s["timestamp"] for s in state.hema_signals]
    assert "09:30:00" in times
    assert "10:15:00" in times
    assert "15:15:00" in times

