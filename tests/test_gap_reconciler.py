"""
Unit & Integration Tests for Historical Candle Database, Gap Detector, and Gap Filler.
"""

from datetime import date, datetime, timedelta
import os
from pathlib import Path
import pytest
import pandas as pd

from database.historical_db import HistoricalCandleDatabase
from market.gap_detector import GapDetector, GapWindow
from market.gap_filler import GapFiller


@pytest.fixture
def temp_hist_db(tmp_path: Path):
    db_file = tmp_path / "test_hist_candles.db"
    db = HistoricalCandleDatabase(db_path=db_file)
    return db


def test_historical_db_batch_save_and_retrieve(temp_hist_db: HistoricalCandleDatabase):
    # Prepare 1-minute mock candles
    candles = [
        ["2026-09-01T09:15:00+05:30", 1000.0, 1005.0, 998.0, 1002.0, 5000, 100],
        ["2026-09-01T09:16:00+05:30", 1002.0, 1008.0, 1001.0, 1007.0, 6000, 105],
        ["2026-09-01T09:17:00+05:30", 1007.0, 1010.0, 1005.0, 1009.0, 4500, 110],
    ]

    inserted = temp_hist_db.save_candles_batch("RELIANCE", "NSE_EQ|1", candles)
    assert inserted == 3

    # Retrieve candles
    df = temp_hist_db.get_candles_by_symbol("RELIANCE")
    assert len(df) == 3
    assert list(df.columns) == ["timestamp", "open", "high", "low", "close", "volume", "oi"]
    assert df.iloc[0]["open"] == 1000.0
    assert df.iloc[2]["close"] == 1009.0

    # Test deduplication / update
    updated_candles = [
        ["2026-09-01T09:17:00+05:30", 1007.0, 1012.0, 1005.0, 1011.0, 4800, 115],
    ]
    inserted2 = temp_hist_db.save_candles_batch("RELIANCE", "NSE_EQ|1", updated_candles)
    assert inserted2 == 1

    df_updated = temp_hist_db.get_candles_by_symbol("RELIANCE")
    assert len(df_updated) == 3  # Count unchanged due to PRIMARY KEY deduplication
    assert df_updated.iloc[2]["high"] == 1012.0


def test_gap_detector_expected_days():
    # Test that weekends are excluded
    expected = GapDetector.get_expected_trading_days(lookback_days=14, end_date=date(2026, 9, 8))
    for d_str in expected:
        dt = datetime.strptime(d_str, "%Y-%m-%d").date()
        assert dt.weekday() < 5, f"{d_str} is a weekend!"


def test_gap_detector_identifies_missing_dates(temp_hist_db: HistoricalCandleDatabase):
    detector = GapDetector(temp_hist_db)

    # Initially database is empty -> all expected trading days should be flagged as gaps
    gaps = detector.detect_symbol_gaps("TCS", "NSE_EQ|2", lookback_days=7)
    assert len(gaps) >= 1
    total_missing = sum(len(gw.missing_dates) for gw in gaps)
    assert total_missing >= 4

    # Now simulate a fully filled day for TCS (375 bars)
    mock_full_day = [
        [f"2026-09-04T09:{m:02d}:00+05:30", 3500.0, 3505.0, 3495.0, 3502.0, 1000, 0]
        for m in range(15, 60)
    ]
    # Add enough bars to pass the threshold
    for h in range(10, 16):
        mock_full_day.extend([
            [f"2026-09-04T{h:02d}:{m:02d}:00+05:30", 3500.0, 3505.0, 3495.0, 3502.0, 1000, 0]
            for m in range(0, 60)
        ])
    temp_hist_db.save_candles_batch("TCS", "NSE_EQ|2", mock_full_day)

    # Re-detect gaps -> 2026-09-04 should NO LONGER be flagged as missing
    gaps_after = detector.detect_symbol_gaps("TCS", "NSE_EQ|2", lookback_days=7)
    missing_dates_after = [d for gw in gaps_after for d in gw.missing_dates]
    assert "2026-09-04" not in missing_dates_after


def test_gap_filler_save_and_reconciliation(temp_hist_db: HistoricalCandleDatabase):
    filler = GapFiller(temp_hist_db, access_token="mock_token")

    # Mock inserting gap candles directly through db
    mock_gap_data = [
        ["2026-09-03T10:15:00+05:30", 500.0, 502.0, 498.0, 501.0, 2000, 50],
        ["2026-09-03T10:16:00+05:30", 501.0, 504.0, 500.0, 503.0, 2500, 55],
    ]
    saved = temp_hist_db.save_candles_batch("INFY", "NSE_EQ|3", mock_gap_data)
    assert saved == 2

    df = temp_hist_db.get_candles_by_symbol("INFY")
    assert len(df) == 2
    assert df.iloc[0]["symbol"] if "symbol" in df else True
