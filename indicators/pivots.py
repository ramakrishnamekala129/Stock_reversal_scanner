"""
Daily Pivot Levels & PDH/PDL Support/Resistance Analysis Module.
Calculates Standard Daily Pivots (PP, R1-R3, S1-S3) and evaluates price relationships.
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class DailyPivots:
    """Standard Daily Pivot & Previous Day OHLCV Levels."""
    symbol: str
    date: str
    pdo: float  # Previous Day Open
    pdh: float  # Previous Day High
    pdl: float  # Previous Day Low
    pdc: float  # Previous Day Close
    pdv: int    # Previous Day Volume

    pp: float   # Pivot Point
    r1: float   # Resistance 1
    r2: float   # Resistance 2
    r3: float   # Resistance 3
    s1: float   # Support 1
    s2: float   # Support 2
    s3: float   # Support 3

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "date": self.date,
            "pdo": self.pdo,
            "pdh": self.pdh,
            "pdl": self.pdl,
            "pdc": self.pdc,
            "pdv": self.pdv,
            "pivot": self.pp,
            "r1": self.r1,
            "r2": self.r2,
            "r3": self.r3,
            "s1": self.s1,
            "s2": self.s2,
            "s3": self.s3,
        }


def calculate_daily_pivots(
    symbol: str,
    date_str: str,
    open_p: float,
    high_p: float,
    low_p: float,
    close_p: float,
    volume: int,
) -> DailyPivots:
    """
    Calculates standard floor pivot levels:
    PP = (H + L + C) / 3
    R1 = 2 * PP - L
    S1 = 2 * PP - H
    R2 = PP + (H - L)
    S2 = PP - (H - L)
    R3 = H + 2 * (PP - L)
    S3 = L - 2 * (H - PP)
    """
    pp = (high_p + low_p + close_p) / 3.0

    r1 = 2.0 * pp - low_p
    s1 = 2.0 * pp - high_p

    r2 = pp + (high_p - low_p)
    s2 = pp - (high_p - low_p)

    r3 = high_p + 2.0 * (pp - low_p)
    s3 = low_p - 2.0 * (high_p - pp)

    return DailyPivots(
        symbol=symbol,
        date=date_str,
        pdo=round(open_p, 2),
        pdh=round(high_p, 2),
        pdl=round(low_p, 2),
        pdc=round(close_p, 2),
        pdv=volume,
        pp=round(pp, 2),
        r1=round(r1, 2),
        r2=round(r2, 2),
        r3=round(r3, 2),
        s1=round(s1, 2),
        s2=round(s2, 2),
        s3=round(s3, 2),
    )


@dataclass
class PivotRelationship:
    """Detailed relationship of a closed 5M candle with Pivot and PDH/PDL levels."""
    price: float
    above_pivot: bool
    below_pivot: bool
    above_pdh: bool
    below_pdl: bool

    crossed_pivot: bool
    crossed_pdh: bool
    crossed_pdl: bool

    crossed_r1: bool
    crossed_r2: bool
    crossed_r3: bool

    crossed_s1: bool
    crossed_s2: bool
    crossed_s3: bool

    distance_from_pivot: float
    distance_from_pdh: float
    distance_from_pdl: float
    distance_from_r1: float
    distance_from_s1: float


def evaluate_pivot_relationship(
    curr_close: float,
    curr_open: float,
    curr_high: float,
    curr_low: float,
    pivots: DailyPivots,
    prev_close: Optional[float] = None,
) -> PivotRelationship:
    """
    Evaluates current candle relationship and crossovers against standard pivots and PDH/PDL.
    """
    ref_prev = prev_close if prev_close is not None else curr_open

    def has_crossed_above(level: float) -> bool:
        # Crosses up if either previous close was below and current close is above,
        # or low was below and close is above
        return (ref_prev <= level and curr_close > level) or (curr_low <= level and curr_close > level)

    def has_crossed_below(level: float) -> bool:
        return (ref_prev >= level and curr_close < level) or (curr_high >= level and curr_close < level)

    above_pivot = curr_close > pivots.pp
    below_pivot = curr_close < pivots.pp
    above_pdh = curr_close > pivots.pdh
    below_pdl = curr_close < pivots.pdl

    crossed_pivot = has_crossed_above(pivots.pp) or has_crossed_below(pivots.pp)
    crossed_pdh = has_crossed_above(pivots.pdh)
    crossed_pdl = has_crossed_below(pivots.pdl)

    crossed_r1 = has_crossed_above(pivots.r1)
    crossed_r2 = has_crossed_above(pivots.r2)
    crossed_r3 = has_crossed_above(pivots.r3)

    crossed_s1 = has_crossed_below(pivots.s1)
    crossed_s2 = has_crossed_below(pivots.s2)
    crossed_s3 = has_crossed_below(pivots.s3)

    distance_from_pivot = round(curr_close - pivots.pp, 2)
    distance_from_pdh = round(curr_close - pivots.pdh, 2)
    distance_from_pdl = round(curr_close - pivots.pdl, 2)
    distance_from_r1 = round(curr_close - pivots.r1, 2)
    distance_from_s1 = round(curr_close - pivots.s1, 2)

    return PivotRelationship(
        price=curr_close,
        above_pivot=above_pivot,
        below_pivot=below_pivot,
        above_pdh=above_pdh,
        below_pdl=below_pdl,
        crossed_pivot=crossed_pivot,
        crossed_pdh=crossed_pdh,
        crossed_pdl=crossed_pdl,
        crossed_r1=crossed_r1,
        crossed_r2=crossed_r2,
        crossed_r3=crossed_r3,
        crossed_s1=crossed_s1,
        crossed_s2=crossed_s2,
        crossed_s3=crossed_s3,
        distance_from_pivot=distance_from_pivot,
        distance_from_pdh=distance_from_pdh,
        distance_from_pdl=distance_from_pdl,
        distance_from_r1=distance_from_r1,
        distance_from_s1=distance_from_s1,
    )
