from datetime import date, datetime, timedelta

import pandas as pd

from chartink_cash_v13_backtest import (
    BACKTEST_MONTHS,
    INSTITUTIONAL_BRANCH_REACHABLE,
    INSTITUTIONAL_VOLUME_MULTIPLIER,
    TIMEFRAME_MINUTES,
    ChartinkCashV13Backtester,
    load_quarterly_fii_history,
    load_three_month_5minute_data,
    normalize_to_5minute,
    prescreen_candidate_symbols,
    quarterly_fii_change_for_date,
    resolve_backtest_window,
    three_month_window,
)


def test_three_month_window_and_minimum_timeframe():
    start, end = three_month_window(date(2026, 9, 17))
    assert (start.isoformat(), end.isoformat()) == ("2026-06-17", "2026-09-17")
    assert BACKTEST_MONTHS == 3
    assert TIMEFRAME_MINUTES == 5


def test_sub_five_minute_source_is_rejected():
    rows = []
    for minute in range(10):
        rows.append({
            "timestamp": datetime(2026, 9, 1, 9, 15) + timedelta(minutes=minute),
            "open": 100 + minute, "high": 101 + minute, "low": 99 + minute,
            "close": 100.5 + minute, "volume": 100,
        })
    import pytest
    with pytest.raises(ValueError, match="5 minutes or higher"):
        normalize_to_5minute(pd.DataFrame(rows))


def test_backtest_runs_monthly_breakout_on_5minute_bars():
    daily_rows = []
    start = datetime(2026, 6, 1)
    for i in range(75):
        ts = start + timedelta(days=i)
        daily_rows.append({
            "timestamp": ts, "open": 120, "high": 140, "low": 115,
            "close": 125, "volume": 1_000_000,
        })
    daily = pd.DataFrame(daily_rows)

    bars = pd.DataFrame([
        {"timestamp": datetime(2026, 8, 20, 9, 15), "open": 150, "high": 154, "low": 145, "close": 153, "volume": 150_000},
        {"timestamp": datetime(2026, 8, 20, 9, 20), "open": 153, "high": 156, "low": 152, "close": 155, "volume": 150_000},
        {"timestamp": datetime(2026, 8, 20, 9, 25), "open": 155, "high": 159, "low": 154, "close": 158.2, "volume": 150_000},
    ])
    result = ChartinkCashV13Backtester().run(
        {"TEST": bars}, {"TEST": daily}, as_of=date(2026, 9, 17)
    )
    assert result["timeframe"] == "5min"
    assert result["minimum_timeframe_minutes"] == 5
    assert result["total_trades"] == 1
    assert result["daily_candidate_stock_days"] == 1
    assert result["daily_prefilter_rejected_stock_days"] == 0
    assert result["daily_prefilter_reduction_pct"] == 0.0
    assert result["calculation_seconds"] >= 0
    assert result["trades"][0]["strategy"] == "Monthly Breakout (Cash V1.3)"
    assert result["trades"][0]["timeframe"] == "5min"


def test_literal_institutional_volume_rule_is_reported_unreachable():
    assert INSTITUTIONAL_VOLUME_MULTIPLIER == 100_000
    assert INSTITUTIONAL_BRANCH_REACHABLE is False


def test_daily_prefilter_rejects_stock_day_before_five_minute_evaluation():
    daily = pd.DataFrame([
        {
            "timestamp": datetime(2026, 6, 1) + timedelta(days=i),
            "open": 120,
            "high": 200,
            "low": 110,
            "close": 125,
            "volume": 1_000_000,
        }
        for i in range(75)
    ])
    bars = pd.DataFrame([
        {
            "timestamp": datetime(2026, 8, 20, 9, 15) + timedelta(minutes=5 * i),
            "open": 100,
            "high": 102,
            "low": 99,
            "close": 101,
            "volume": 100_000,
        }
        for i in range(3)
    ])
    result = ChartinkCashV13Backtester().run(
        {"TEST": bars}, {"TEST": daily}, as_of=date(2026, 9, 17)
    )
    assert result["stock_days_total"] == 1
    assert result["daily_candidate_stock_days"] == 0
    assert result["daily_prefilter_rejected_stock_days"] == 1
    assert result["bars_evaluated"] == 0


def test_loader_uses_only_native_5minute_history(tmp_path):
    class FakeRestClient:
        def __init__(self):
            self.calls = []

        def get_broker_5m_history(self, instrument_key, to_date=None, from_date=None):
            self.calls.append((instrument_key, from_date, to_date))
            return [[f"{to_date}T09:15:00+05:30", 100, 101, 99, 100.5, 1000, 0]]

    client = FakeRestClient()
    loaded = load_three_month_5minute_data(
        client,
        {"TEST": {"instrument_key": "NSE_EQ|TEST"}},
        as_of=date(2026, 9, 17),
        cache_dir=tmp_path,
        max_workers=1,
    )
    assert "TEST" in loaded
    assert len(client.calls) == 4
    assert all(call[0] == "NSE_EQ|TEST" for call in client.calls)


def test_loader_merges_current_session_into_existing_cache_without_api_call(tmp_path):
    class FakeRestClient:
        def get_broker_5m_history(self, *args, **kwargs):
            raise AssertionError("current session should be merged from the local database")

    as_of = date(2026, 9, 17)
    start, end = three_month_window(as_of)
    cached = pd.DataFrame([{
        "timestamp": datetime(2026, 9, 16, 15, 25),
        "open": 100,
        "high": 101,
        "low": 99,
        "close": 100.5,
        "volume": 1000,
    }])
    cached.to_parquet(tmp_path / f"TEST_{start}_{end}_5m.parquet", index=False)
    current = pd.DataFrame([{
        "timestamp": datetime(2026, 9, 17, 9, 15),
        "open": 102,
        "high": 103,
        "low": 101,
        "close": 102.5,
        "volume": 2000,
    }])
    current["timestamp"] = pd.to_datetime(current["timestamp"]).dt.tz_localize("Asia/Kolkata")
    loaded = load_three_month_5minute_data(
        FakeRestClient(),
        {"TEST": {"instrument_key": "NSE_EQ|TEST"}},
        as_of=as_of,
        cache_dir=tmp_path,
        current_session_by_symbol={"TEST": current},
        max_workers=1,
    )
    assert pd.to_datetime(loaded["TEST"]["timestamp"]).max().date() == as_of


def test_quarterly_fii_change_uses_only_completed_reporting_quarters():
    history = [
        {"period": "Sep 2026", "value": 15.0},
        {"period": "Jun 2026", "value": 12.0},
        {"period": "Mar 2026", "value": 10.0},
        {"period": "Dec 2025", "value": 8.0},
    ]
    assert quarterly_fii_change_for_date(history, date(2026, 6, 17)) == 2.0
    assert quarterly_fii_change_for_date(history, date(2026, 7, 1)) == 2.0
    assert quarterly_fii_change_for_date(history, date(2026, 9, 17)) == 2.0
    assert quarterly_fii_change_for_date(history, date(2025, 12, 31)) is None


def test_fii_loader_uses_cash_isin_and_cache(tmp_path):
    class FakeRestClient:
        def __init__(self):
            self.calls = []

        def get_quarterly_share_holdings(self, isin):
            self.calls.append(isin)
            return [
                {"category": "promoters", "history": []},
                {"category": "fii", "history": [
                    {"period": "Jun 2026", "value": 12.0},
                    {"period": "Mar 2026", "value": 10.0},
                ]},
            ]

    client = FakeRestClient()
    universe = {"TEST": {"instrument_key": "NSE_EQ|INE000A01001"}}
    first = load_quarterly_fii_history(client, universe, cache_dir=tmp_path)
    second = load_quarterly_fii_history(client, universe, cache_dir=tmp_path)

    assert client.calls == ["INE000A01001"]
    assert first == second
    assert first["TEST"][0]["period"] == "Jun 2026"


def test_resolve_backtest_window():
    # current_day mode
    start, end = resolve_backtest_window("current_day", as_of=date(2026, 9, 17))
    assert start == date(2026, 9, 17)
    assert end == date(2026, 9, 17)

    # 3_months mode
    start_3m, end_3m = resolve_backtest_window("3_months", as_of=date(2026, 9, 17))
    assert start_3m == date(2026, 6, 17)
    assert end_3m == date(2026, 9, 17)

    # fallback to available_dates if off-market / weekend
    past_date = date(2026, 9, 10)
    start_fb, end_fb = resolve_backtest_window("current_day", as_of=None, available_dates=[past_date], live_session=False)
    assert start_fb == past_date
    assert end_fb == past_date

    # live session targets today
    start_live, end_live = resolve_backtest_window("current_day", as_of=None, live_session=True)
    assert start_live == date.today()
    assert end_live == date.today()


def test_prescreen_candidate_symbols_filters_untriggered_stocks():
    # Stock A: Hits breakout level with adequate turnover
    daily_a = pd.DataFrame([
        {
            "timestamp": datetime(2026, 6, 1) + timedelta(days=i),
            "open": 100, "high": 120, "low": 95, "close": 110, "volume": 1_000_000,
        }
        for i in range(70)
    ] + [
        {
            "timestamp": datetime(2026, 9, 10),
            "open": 115, "high": 150, "low": 110, "close": 145, "volume": 2_000_000,
        }
    ])

    # Stock B: Never reaches previous month high (stays low)
    daily_b = pd.DataFrame([
        {
            "timestamp": datetime(2026, 6, 1) + timedelta(days=i),
            "open": 200, "high": 250, "low": 190, "close": 210, "volume": 1_000_000,
        }
        for i in range(70)
    ] + [
        {
            "timestamp": datetime(2026, 9, 10),
            "open": 100, "high": 105, "low": 95, "close": 102, "volume": 500_000,
        }
    ])

    universe = {
        "STOCK_A": {"instrument_key": "NSE_EQ|A"},
        "STOCK_B": {"instrument_key": "NSE_EQ|B"},
    }
    daily_map = {"STOCK_A": daily_a, "STOCK_B": daily_b}

    screened, candidate_days = prescreen_candidate_symbols(
        universe, daily_map, start_date=date(2026, 9, 10), end_date=date(2026, 9, 10)
    )

    # STOCK_A qualifies, STOCK_B has 0 candidate days and must be skipped
    assert "STOCK_A" in screened
    assert "STOCK_B" not in screened
    assert candidate_days["STOCK_A"] == 1


def test_backtest_runs_current_day_mode():
    daily = pd.DataFrame([
        {
            "timestamp": datetime(2026, 6, 1) + timedelta(days=i),
            "open": 100, "high": 120, "low": 95, "close": 110, "volume": 1_000_000,
        }
        for i in range(75)
    ])
    bars_today = pd.DataFrame([
        {"timestamp": datetime(2026, 8, 20, 9, 15), "open": 130, "high": 135, "low": 128, "close": 132, "volume": 150_000},
        {"timestamp": datetime(2026, 8, 20, 9, 20), "open": 132, "high": 136, "low": 131, "close": 134, "volume": 150_000},
        {"timestamp": datetime(2026, 8, 20, 9, 25), "open": 134, "high": 140, "low": 133, "close": 139, "volume": 150_000},
    ])
    result = ChartinkCashV13Backtester().run(
        {"TEST": bars_today}, {"TEST": daily}, as_of=date(2026, 8, 20), mode="current_day"
    )
    assert result["mode"] == "current_day"
    assert result["from_date"] == "2026-08-20"
    assert result["to_date"] == "2026-08-20"
    assert result["total_trades"] == 1


def test_get_latest_completed_session_date():
    from chartink_cash_v13_backtest import get_latest_completed_session_date
    # Saturday rolls back to Friday
    saturday = date(2026, 9, 19)
    assert get_latest_completed_session_date(as_of=saturday) == date(2026, 9, 18)
    # Sunday rolls back to Friday
    sunday = date(2026, 9, 20)
    assert get_latest_completed_session_date(as_of=sunday) == date(2026, 9, 18)


def test_load_5minute_data_reuses_sqlite_without_broker_calls(tmp_path):
    from database.historical_db import HistoricalCandleDatabase
    from chartink_cash_v13_backtest import load_three_month_5minute_data

    db = HistoricalCandleDatabase(db_path=tmp_path / "test_cache.db")
    db.init_schema()

    # Pre-populate DB with 5m candles for 2 symbols
    start_dt = datetime(2026, 9, 17, 9, 15)
    candles = [
        {
            "timestamp": (start_dt + timedelta(minutes=5 * i)).strftime("%Y-%m-%dT%H:%M:%S+05:30"),
            "open": 100, "high": 105, "low": 99, "close": 102, "volume": 1000, "oi": 0,
        }
        for i in range(70)
    ]
    db.save_candles_batch("STOCK_A", "NSE_EQ|A", candles)
    db.save_candles_batch("STOCK_B", "NSE_EQ|B", candles)

    universe = {
        "STOCK_A": {"instrument_key": "NSE_EQ|A"},
        "STOCK_B": {"instrument_key": "NSE_EQ|B"},
    }

    # Pass rest_client=None -> if it tried to call broker API, it would crash or fail.
    loaded = load_three_month_5minute_data(
        rest_client=None,
        universe=universe,
        start_date=date(2026, 9, 17),
        end_date=date(2026, 9, 17),
        hist_db=db,
    )

    assert "STOCK_A" in loaded
    assert "STOCK_B" in loaded
    assert len(loaded["STOCK_A"]) == 70
    assert len(loaded["STOCK_B"]) == 70


def test_load_5minute_data_refreshes_live_candle(tmp_path, monkeypatch):
    from unittest.mock import MagicMock
    from zoneinfo import ZoneInfo
    from database.historical_db import HistoricalCandleDatabase
    from chartink_cash_v13_backtest import load_three_month_5minute_data

    # Simulate Wednesday at 11:00 AM IST (live market session)
    simulated_now = datetime(2026, 9, 16, 11, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    monkeypatch.setattr(
        "chartink_cash_v13_backtest.datetime",
        type("MockDateTime", (datetime,), {"now": staticmethod(lambda tz=None: simulated_now)}),
    )

    db = HistoricalCandleDatabase(db_path=tmp_path / "test_cache2.db")
    db.init_schema()

    today = simulated_now.date()
    start_dt = datetime.combine(today, datetime.min.time()).replace(hour=9, minute=15)
    # Put an old candle from earlier today into SQLite
    candles = [
        {
            "timestamp": start_dt.strftime("%Y-%m-%dT%H:%M:%S+05:30"),
            "open": 100, "high": 105, "low": 99, "close": 102, "volume": 1000, "oi": 0,
        }
    ]
    db.save_candles_batch("STOCK_C", "NSE_EQ|C", candles)

    # Mock rest_client returning newer candles for today
    mock_client = MagicMock()
    newer_ts = (start_dt + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S+05:30")
    mock_client.get_broker_5m_history.return_value = [
        [newer_ts, 102, 107, 101, 106, 2500, 0]
    ]

    universe = {"STOCK_C": {"instrument_key": "NSE_EQ|C"}}
    loaded = load_three_month_5minute_data(
        rest_client=mock_client,
        universe=universe,
        start_date=today,
        end_date=today,
        hist_db=db,
    )

    assert "STOCK_C" in loaded
    # Verify rest_client was queried to refresh today's candles
    mock_client.get_broker_5m_history.assert_called()

