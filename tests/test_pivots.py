"""
Unit tests for Daily Pivot Level calculations and Crossover detection.
"""

import pytest
from indicators.pivots import calculate_daily_pivots, evaluate_pivot_relationship, DailyPivots


def test_daily_pivot_formulas():
    # Test values: H=1550, L=1500, C=1525, O=1510, V=1000000
    # PP = (1550 + 1500 + 1525) / 3 = 4575 / 3 = 1525.0
    # R1 = 2 * 1525 - 1500 = 3050 - 1500 = 1550.0
    # S1 = 2 * 1525 - 1550 = 3050 - 1550 = 1500.0
    # R2 = 1525 + (1550 - 1500) = 1525 + 50 = 1575.0
    # S2 = 1525 - (1550 - 1500) = 1525 - 50 = 1475.0
    # R3 = 1550 + 2 * (1525 - 1500) = 1550 + 50 = 1600.0
    # S3 = 1500 - 2 * (1550 - 1525) = 1500 - 50 = 1450.0

    p = calculate_daily_pivots(
        symbol="RELIANCE",
        date_str="2026-08-31",
        open_p=1510.0,
        high_p=1550.0,
        low_p=1500.0,
        close_p=1525.0,
        volume=1000000,
    )

    assert p.symbol == "RELIANCE"
    assert p.pp == pytest.approx(1525.0, abs=0.01)
    assert p.r1 == pytest.approx(1550.0, abs=0.01)
    assert p.s1 == pytest.approx(1500.0, abs=0.01)
    assert p.r2 == pytest.approx(1575.0, abs=0.01)
    assert p.s2 == pytest.approx(1475.0, abs=0.01)
    assert p.r3 == pytest.approx(1600.0, abs=0.01)
    assert p.s3 == pytest.approx(1450.0, abs=0.01)
    assert p.pdh == 1550.0
    assert p.pdl == 1500.0
    assert p.pdc == 1525.0
    assert p.pdo == 1510.0


def test_pivot_relationship_crossovers():
    p = calculate_daily_pivots(
        symbol="TCS",
        date_str="2026-08-31",
        open_p=3500.0,
        high_p=3550.0,
        low_p=3450.0,
        close_p=3500.0,
        volume=500000,
    )
    # PP = 3500, R1 = 3550, S1 = 3450, PDH = 3550, PDL = 3450

    # Case 1: Candle closes above PDH & R1
    rel1 = evaluate_pivot_relationship(
        curr_close=3560.0,
        curr_open=3540.0,
        curr_high=3565.0,
        curr_low=3535.0,
        pivots=p,
        prev_close=3540.0,
    )
    assert rel1.above_pivot is True
    assert rel1.above_pdh is True
    assert rel1.crossed_pdh is True
    assert rel1.crossed_r1 is True
    assert rel1.distance_from_pivot == pytest.approx(60.0, abs=0.01)

    # Case 2: Candle closes below S1 & PDL
    rel2 = evaluate_pivot_relationship(
        curr_close=3440.0,
        curr_open=3460.0,
        curr_high=3465.0,
        curr_low=3435.0,
        pivots=p,
        prev_close=3460.0,
    )
    assert rel2.below_pivot is True
    assert rel2.below_pdl is True
    assert rel2.crossed_pdl is True
    assert rel2.crossed_s1 is True


def test_narrow_cpr_and_trap_zone():
    # H=1000.5, L=999.5, C=1000.0 (Extremely narrow range)
    # PP = 1000.0, BC = 1000.0, TC = 1000.0 -> CPR width = 0.0% <= 0.20%
    # R1 = 2000 - 999.5 = 1000.5, PDH = 1000.5 -> Bull Trap width = 0.0 <= 0.20%
    # S1 = 2000 - 1000.5 = 999.5, PDL = 999.5 -> Bear Trap width = 0.0 <= 0.20%
    p = calculate_daily_pivots(
        symbol="NIFTY",
        date_str="2026-09-01",
        open_p=1000.0,
        high_p=1000.5,
        low_p=999.5,
        close_p=1000.0,
        volume=100000,
    )
    assert p.is_narrow_cpr is True
    assert p.is_narrow_bull_trap is True
    assert p.is_narrow_bear_trap is True
    assert p.is_narrow_trap_zone is True
    assert p.bull_trap_width_pct <= 0.20
    assert p.bear_trap_width_pct <= 0.20


def test_cpr_breakout_and_breakdown_validation():
    from indicators.pivots import is_valid_cpr_breakout, is_valid_cpr_breakdown
    # H=100.0, L=90.0, C=95.0 -> PP=95.0, BC=95.0, TC=95.0 (cpr_top=95.0, cpr_bottom=95.0)
    p = calculate_daily_pivots('TEST', '2026-09-01', 92.0, 100.0, 90.0, 95.0, 1000)

    # 1. Valid CPR Breakout: Open 94.0, High 100.0, Low 93.0, Close 99.0 (Bullish, >60% range above 95.0)
    assert is_valid_cpr_breakout(94.0, 100.0, 93.0, 99.0, p) is True
    # Inward failure: Close is below CPR top
    assert is_valid_cpr_breakout(94.0, 96.0, 93.0, 94.5, p) is False
    # Wick only failure: Bearish candle closing below open
    assert is_valid_cpr_breakout(98.0, 100.0, 93.0, 96.0, p) is False

    # 2. Valid CPR Breakdown: Open 96.0, High 97.0, Low 90.0, Close 91.0 (Bearish, >60% range below 95.0)
    assert is_valid_cpr_breakdown(96.0, 97.0, 90.0, 91.0, p) is True
    # Inward failure: Close is above CPR bottom
    assert is_valid_cpr_breakdown(96.0, 97.0, 94.0, 95.5, p) is False
