import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, date
from indicators.screener_57960 import Screener57960Engine, Screener57960Signal


def generate_synthetic_daily_data(passes: bool = True) -> pd.DataFrame:
    """Generates synthetic daily dataframe designed to pass or fail Screener 57960 conditions."""
    base_date = datetime(2026, 8, 1)
    rows = []
    price = 1000.0
    for i in range(30):
        dt = base_date + timedelta(days=i)
        # Increasing volume and price to give MFI > 60 and rising ATR
        tr_expand = 10.0 + (i * 0.5)
        rows.append({
            "timestamp": dt,
            "open": price,
            "high": price + tr_expand,
            "low": price - 2.0,
            "close": price + (tr_expand * 0.8),
            "volume": 500_000 + (i * 50_000),
            "oi": 0,
        })
        price += 2.0

    df = pd.DataFrame(rows)

    if passes:
        # Tune last day:
        # open = 1000, gann_level = (sqrt(1000) + 0.125)**2 = (31.6227 + 0.125)**2 = 1007.92
        # close = 1025 (> 1007.92, > 350, < 3000)
        # high = 1030, low = 998 -> median = 1014, typical = (1030 + 998 + 1025)/3 = 1017.67
        # threshold = 1017.67 * 0.997 = 1014.61 -> median(1014) < threshold(1014.61) -> PASS!
        df.loc[len(df) - 1, "open"] = 1000.0
        df.loc[len(df) - 1, "high"] = 1030.0
        df.loc[len(df) - 1, "low"] = 998.0
        df.loc[len(df) - 1, "close"] = 1025.0
        df.loc[len(df) - 1, "volume"] = 2_000_000

    return df


def generate_synthetic_5m_data(passes: bool = True) -> pd.DataFrame:
    base_time = datetime(2026, 9, 1, 9, 15)
    rows = []
    price = 1000.0
    for i in range(40):
        dt = base_time + timedelta(minutes=5 * i)
        if passes:
            price += 1.0  # Steady uptrend makes EMA13 > SMA13(EMA13)
        else:
            price -= 1.0  # Downtrend makes EMA13 < SMA13(EMA13)
        rows.append({
            "timestamp": dt,
            "open": price - 0.5,
            "high": price + 1.0,
            "low": price - 1.0,
            "close": price,
            "volume": 20_000,
        })
    return pd.DataFrame(rows)


def test_screener_57960_indicators():
    df_daily = generate_synthetic_daily_data(passes=True)
    engine = Screener57960Engine()
    mfi, atr = engine.calculate_daily_indicators(df_daily)
    assert len(mfi) == len(df_daily)
    assert len(atr) == len(df_daily)
    assert float(mfi.iloc[-1]) > 50.0
    assert float(atr.iloc[-1]) > 0.0


def test_screener_57960_5m_trigger():
    engine = Screener57960Engine()
    df_5m_pass = generate_synthetic_5m_data(passes=True)
    is_trg, ema, sma, idx = engine.check_5m_trigger(df_5m_pass)
    assert is_trg is True
    assert ema > sma

    df_5m_fail = generate_synthetic_5m_data(passes=False)
    is_trg_fail, ema_fail, sma_fail, _ = engine.check_5m_trigger(df_5m_fail)
    assert is_trg_fail is False
    assert ema_fail < sma_fail


def test_screener_57960_evaluate_stock():
    engine = Screener57960Engine()
    df_daily = generate_synthetic_daily_data(passes=True)
    df_5m = generate_synthetic_5m_data(passes=True)

    today_date = pd.to_datetime(df_daily["timestamp"].iloc[-1]).date()
    sig = engine.evaluate_stock("TEST_STOCK", df_daily, df_5m, target_date=today_date)
    assert sig is not None
    assert sig.symbol == "TEST_STOCK"
    assert sig.price == 1025.0
    assert sig.gann_level < sig.price
    assert sig.mfi_14 > 60.0
    assert sig.atr_14 > sig.prev_atr_14
    assert sig.ema_13 > sig.sma_ema_13
    assert len(sig.confluence_factors) > 0


def test_screener_57960_price_bounds_rejection():
    engine = Screener57960Engine()
    df_daily = generate_synthetic_daily_data(passes=True)
    today_date = pd.to_datetime(df_daily["timestamp"].iloc[-1]).date()

    # Price < 350
    df_daily.loc[len(df_daily) - 1, "close"] = 300.0
    sig = engine.evaluate_stock("PENNY_STOCK", df_daily, target_date=today_date)
    assert sig is None

    # Price > 3000
    df_daily.loc[len(df_daily) - 1, "close"] = 3500.0
    sig = engine.evaluate_stock("EXPENSIVE_STOCK", df_daily, target_date=today_date)
    assert sig is None


def test_screener_57960_yesterday_target_date():
    engine = Screener57960Engine()
    df_daily = generate_synthetic_daily_data(passes=True)

    # Make yesterday's bar also meet the conditions
    yest_date = pd.to_datetime(df_daily["timestamp"].iloc[-2]).date()
    df_daily.loc[len(df_daily) - 2, "open"] = 1000.0
    df_daily.loc[len(df_daily) - 2, "high"] = 1030.0
    df_daily.loc[len(df_daily) - 2, "low"] = 998.0
    df_daily.loc[len(df_daily) - 2, "close"] = 1025.0
    df_daily.loc[len(df_daily) - 2, "volume"] = 2_000_000

    sig_yest = engine.evaluate_stock("HIST_STOCK", df_daily, target_date=yest_date)
    assert sig_yest is not None
    assert sig_yest.symbol == "HIST_STOCK"
    assert sig_yest.price == 1025.0
    assert sig_yest.date == str(yest_date)


def test_screener_57960_chartink_alerts_csv_loaded():
    from indicators.screener_57960 import get_chartink_57960_alerts
    alerts = get_chartink_57960_alerts()
    assert "2026-09-18" in alerts
    assert "2026-09-17" in alerts
    assert "2026-09-16" in alerts
    assert len(alerts["2026-09-18"]) == 35
    assert len(alerts["2026-09-17"]) == 17
    assert len(alerts["2026-09-16"]) == 12

    # Check alert fields
    b_alert = alerts["2026-09-18"]["BEML"]
    assert b_alert["sector"] == "Aerospace & Defence"
    assert b_alert["market_cap"] == "Midcap"
    assert b_alert["first_time"] == "09:15"


def test_screener_57960_scanner_exact_sessions_match():
    from scanner.scanner import FNOIntradayScanner
    from web.state import dashboard_state

    s = FNOIntradayScanner(enable_web=False, enable_excel=False)
    # Today session (18-Sep) -> 35
    _, n_tasks18, n_sigs18 = s.scan_57960_universe(session_mode="today", target_date=date(2026, 9, 18))
    sigs18 = dashboard_state.get_screener_57960_signals(target_date="2026-09-18")
    assert n_sigs18 == 35
    assert len(sigs18) == 35

    # Yesterday session (17-Sep) -> 17
    _, n_tasks17, n_sigs17 = s.scan_57960_universe(session_mode="yesterday", target_date=date(2026, 9, 17))
    sigs17 = dashboard_state.get_screener_57960_signals(target_date="2026-09-17")
    assert n_sigs17 == 17
    assert len(sigs17) == 17

    # 16-Sep session -> 12
    _, n_tasks16, n_sigs16 = s.scan_57960_universe(session_mode="16-sep", target_date=date(2026, 9, 16))
    sigs16 = dashboard_state.get_screener_57960_signals(target_date="2026-09-16")
    assert n_sigs16 == 12
    assert len(sigs16) == 12


def test_screener_57960_full_history_dataset():
    from pathlib import Path
    from indicators.screener_57960 import get_chartink_57960_alerts
    import pandas as pd

    csv_p = Path("data/chartink_57960_daily_history.csv")
    assert csv_p.exists()
    df = pd.read_csv(csv_p)
    assert len(df) == 2529
    assert df["Date"].nunique() == 160

    alerts_map = get_chartink_57960_alerts()
    assert len(alerts_map) >= 160
    assert "2026-01-28" in alerts_map
    assert "2026-04-22" in alerts_map
    assert "2026-09-18" in alerts_map

    # Check 28-01-2026 BEL
    bel_alert = alerts_map["2026-01-28"].get("BEL")
    assert bel_alert is not None
    assert bel_alert["sector"] == "Aerospace & Defence"
    assert bel_alert["market_cap"] == "Largecap"

    # Check 18-09-2026 BEML
    beml_alert = alerts_map["2026-09-18"].get("BEML")
    assert beml_alert is not None
    assert beml_alert["sector"] == "Aerospace & Defence"
    assert beml_alert["first_time"] == "09:15"



