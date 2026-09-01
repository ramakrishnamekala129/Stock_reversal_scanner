"""
Unit tests for 5-minute Candle Engine and aggregation logic.
"""

from datetime import datetime
import pytz
import pytest
from market.candle_engine import CandleEngine, CandleStatus
from upstox.websocket import NormalizedTick


def test_candle_bucket_alignment():
    engine = CandleEngine()
    tz = pytz.timezone("Asia/Kolkata")

    # 09:16:32 -> should map to 09:15:00
    dt1 = tz.localize(datetime(2026, 9, 1, 9, 16, 32))
    start1 = engine.get_candle_start_time(dt1)
    assert start1.hour == 9 and start1.minute == 15 and start1.second == 0

    # 09:19:59 -> should map to 09:15:00
    dt2 = tz.localize(datetime(2026, 9, 1, 9, 19, 59))
    start2 = engine.get_candle_start_time(dt2)
    assert start2.hour == 9 and start2.minute == 15 and start2.second == 0

    # 09:20:00 -> should map to 09:20:00
    dt3 = tz.localize(datetime(2026, 9, 1, 9, 20, 0))
    start3 = engine.get_candle_start_time(dt3)
    assert start3.hour == 9 and start3.minute == 20 and start3.second == 0


def test_tick_processing_and_candle_closure():
    tz = pytz.timezone("Asia/Kolkata")
    closed_events = []

    def on_closed(symbol, candle, df):
        closed_events.append((symbol, candle, df))

    engine = CandleEngine(on_candle_closed=on_closed)

    # Send ticks for 09:15-09:20 candle
    engine.process_tick(NormalizedTick(
        instrument_key="NSE_EQ|1",
        symbol="RELIANCE",
        timestamp=tz.localize(datetime(2026, 9, 1, 9, 15, 10)),
        ltp=1500.0,
        volume=100,
    ))
    engine.process_tick(NormalizedTick(
        instrument_key="NSE_EQ|1",
        symbol="RELIANCE",
        timestamp=tz.localize(datetime(2026, 9, 1, 9, 16, 0)),
        ltp=1520.0,
        volume=150,
    ))
    engine.process_tick(NormalizedTick(
        instrument_key="NSE_EQ|1",
        symbol="RELIANCE",
        timestamp=tz.localize(datetime(2026, 9, 1, 9, 18, 0)),
        ltp=1490.0,
        volume=200,
    ))
    engine.process_tick(NormalizedTick(
        instrument_key="NSE_EQ|1",
        symbol="RELIANCE",
        timestamp=tz.localize(datetime(2026, 9, 1, 9, 19, 50)),
        ltp=1510.0,
        volume=250,
    ))

    # While inside 09:15-09:20, no closed candle event should have fired
    assert len(closed_events) == 0

    # First tick of 09:20 -> should trigger closure of 09:15 candle!
    engine.process_tick(NormalizedTick(
        instrument_key="NSE_EQ|1",
        symbol="RELIANCE",
        timestamp=tz.localize(datetime(2026, 9, 1, 9, 20, 1)),
        ltp=1512.0,
        volume=300,
    ))

    assert len(closed_events) == 1
    sym, closed_c, df = closed_events[0]
    assert sym == "RELIANCE"
    assert closed_c.status == CandleStatus.CLOSED
    assert closed_c.open == 1500.0
    assert closed_c.high == 1520.0
    assert closed_c.low == 1490.0
    assert closed_c.close == 1510.0
    assert closed_c.volume == 150  # 250 - 100
    assert len(df) == 1
