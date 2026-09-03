"""
Daily Pivot Levels & PDH/PDL Support/Resistance Analysis Module.
Calculates Standard Daily Pivots (PP, R1-R3, S1-S3) and evaluates price relationships.
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional
import config


@dataclass
class DailyPivots:
    """Standard Daily Pivot, CPR (Central Pivot Range), and Trap Zone Levels."""
    symbol: str
    date: str
    pdo: float  # Previous Day Open
    pdh: float  # Previous Day High
    pdl: float  # Previous Day Low
    pdc: float  # Previous Day Close
    pdv: int    # Previous Day Volume

    pp: float   # Pivot Point = (H + L + C) / 3
    tc: float   # Top Central Pivot = (PP - BC) + PP
    bc: float   # Bottom Central Pivot = (H + L) / 2
    cpr_top: float     # max(tc, bc)
    cpr_bottom: float  # min(tc, bc)
    cpr_width_pct: float  # abs(tc - bc) / pp * 100
    is_narrow_cpr: bool   # CPR width <= 0.20%

    r1: float   # Resistance 1
    r2: float   # Resistance 2
    r3: float   # Resistance 3
    s1: float   # Support 1
    s2: float   # Support 2
    s3: float   # Support 3

    # Trap Zones
    bull_trap_top: float     # max(r1, pdh)
    bull_trap_bottom: float  # min(r1, pdh)
    bear_trap_top: float     # max(s1, pdl)
    bear_trap_bottom: float  # min(s1, pdl)

    bull_trap_width_pct: float = 0.0  # abs(r1 - pdh) / pp * 100
    bear_trap_width_pct: float = 0.0  # abs(s1 - pdl) / pp * 100
    is_narrow_bull_trap: bool = False  # Bull Trap range <= 0.20% of price
    is_narrow_bear_trap: bool = False  # Bear Trap range <= 0.20% of price
    is_narrow_trap_zone: bool = False  # True if bull or bear trap <= 0.20%

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "date": self.date,
            "pdo": self.pdo,
            "pdh": self.pdh,
            "pdl": self.pdl,
            "pdc": self.pdc,
            "pdv": self.pdv,
            "pp": self.pp,
            "pivot": self.pp,
            "tc": self.tc,
            "bc": self.bc,
            "cpr_top": self.cpr_top,
            "cpr_bottom": self.cpr_bottom,
            "cpr_width_pct": self.cpr_width_pct,
            "is_narrow_cpr": self.is_narrow_cpr,
            "r1": self.r1,
            "r2": self.r2,
            "r3": self.r3,
            "s1": self.s1,
            "s2": self.s2,
            "s3": self.s3,
            "bull_trap_top": self.bull_trap_top,
            "bull_trap_bottom": self.bull_trap_bottom,
            "bear_trap_top": self.bear_trap_top,
            "bear_trap_bottom": self.bear_trap_bottom,
            "bull_trap_width_pct": self.bull_trap_width_pct,
            "bear_trap_width_pct": self.bear_trap_width_pct,
            "is_narrow_bull_trap": self.is_narrow_bull_trap,
            "is_narrow_bear_trap": self.is_narrow_bear_trap,
            "is_narrow_trap_zone": self.is_narrow_trap_zone,
        }


def is_valid_cpr_breakout(
    open_p: float,
    high_p: float,
    low_p: float,
    close_p: float,
    pivots: DailyPivots,
    min_candle_ratio: float = 0.60,
) -> bool:
    """
    Validates a decisive CPR Breakout (Bullish):
    1. Candle closes ABOVE CPR top (max(TC, BC)).
    2. Candle is bullish (close > open).
    3. Candle originated from or touched CPR band (low_p <= pivots.cpr_top * 1.004 or open_p <= pivots.cpr_top).
    4. Most of the candle (>= 60% of total range) formed and closed above CPR top.
    """
    if close_p <= pivots.cpr_top or close_p <= open_p:
        return False

    # Candle must originate from or test CPR band to be a genuine CPR breakout
    if low_p > pivots.cpr_top * 1.004 and open_p > pivots.cpr_top:
        return False

    tot_range = max(high_p - low_p, 0.0001)
    portion_above_cpr = max(high_p - max(low_p, pivots.cpr_top), 0.0)
    ratio_above = portion_above_cpr / tot_range

    return ratio_above >= min_candle_ratio


def is_valid_cpr_breakdown(
    open_p: float,
    high_p: float,
    low_p: float,
    close_p: float,
    pivots: DailyPivots,
    min_candle_ratio: float = 0.60,
) -> bool:
    """
    Validates a decisive CPR Breakdown (Bearish):
    1. Candle closes BELOW CPR bottom (min(TC, BC)).
    2. Candle is bearish (close < open).
    3. Candle originated from or touched CPR band (high_p >= pivots.cpr_bottom * 0.996 or open_p >= pivots.cpr_bottom).
    4. Most of the candle (>= 60% of total range) formed and closed below CPR bottom.
    """
    if close_p >= pivots.cpr_bottom or close_p >= open_p:
        return False

    # Candle must originate from or test CPR band to be a genuine CPR breakdown
    if high_p < pivots.cpr_bottom * 0.996 and open_p < pivots.cpr_bottom:
        return False

    tot_range = max(high_p - low_p, 0.0001)
    portion_below_cpr = max(min(high_p, pivots.cpr_bottom) - low_p, 0.0)
    ratio_below = portion_below_cpr / tot_range

    return ratio_below >= min_candle_ratio


def get_pivot_zone(
    price: float,
    pivots: DailyPivots,
    low: Optional[float] = None,
    high: Optional[float] = None,
    open_p: Optional[float] = None,
    direction: Optional[str] = None,
) -> str:
    """
    Returns high-precision technical zone classification including:
    - CPR Breakout / Breakdown (Decisive close with >=60% of candle on breakout side)
    - Bull Trap Zone (R1 - PDH Resistance) (Narrow if range <= 0.20% of price)
    - Bear Trap Zone (S1 - PDL Support) (Narrow if range <= 0.20% of price)
    - Central Pivot Range (Inside Narrow CPR / CPR Base)
    - Standard Expansion / Breakout levels
    If low and high are provided, evaluates candle wick & body touches as well.
    """
    c_low = low if low is not None else price
    c_high = high if high is not None else price
    c_open = open_p if open_p is not None else c_low
    dir_upper = direction.upper() if direction else ""
    is_bull = "BULL" in dir_upper if dir_upper else (price >= c_open)
    is_bear = "BEAR" in dir_upper if dir_upper else (price < c_open)

    # 1. Check CPR Breakout (Majority >=60% of candle closed above CPR top from CPR)
    if is_valid_cpr_breakout(c_open, c_high, c_low, price, pivots):
        if pivots.is_narrow_cpr:
            return f"🚀 Narrow CPR Breakout ({pivots.cpr_width_pct:.2f}%)"
        return "🚀 CPR Breakout (Bullish Close)"

    # 2. Check CPR Breakdown (Majority >=60% of candle closed below CPR bottom from CPR)
    if is_valid_cpr_breakdown(c_open, c_high, c_low, price, pivots):
        if pivots.is_narrow_cpr:
            return f"💥 Narrow CPR Breakdown ({pivots.cpr_width_pct:.2f}%)"
        return "💥 CPR Breakdown (Bearish Close)"

    # 3. Check Bull Trap Zone (R1 & PDH Confluence - must be <= 0.20% of price)
    if pivots.is_narrow_bull_trap:
        if price > pivots.bull_trap_top:
            if c_low <= pivots.bull_trap_top:
                return f"🚀 Bull Trap Breakout ({pivots.bull_trap_width_pct:.2f}%)"
        elif price < pivots.bull_trap_bottom and c_high >= pivots.bull_trap_bottom and price >= pivots.pp:
            return f"💥 Bull Trap Breakdown ({pivots.bull_trap_width_pct:.2f}%)"
        elif (pivots.bull_trap_bottom <= price <= pivots.bull_trap_top) or (c_high >= pivots.bull_trap_bottom and c_low <= pivots.bull_trap_top and price >= pivots.bull_trap_bottom):
            return f"🪤 Narrow Bull Trap ({pivots.bull_trap_width_pct:.2f}%)"

    # 4. Check Bear Trap Zone (S1 & PDL Confluence - must be <= 0.20% of price)
    if pivots.is_narrow_bear_trap:
        if price < pivots.bear_trap_bottom:
            if c_high >= pivots.bear_trap_bottom:
                return f"💥 Bear Trap Breakdown ({pivots.bear_trap_width_pct:.2f}%)"
        elif price > pivots.bear_trap_top and c_low <= pivots.bear_trap_top and price <= pivots.pp:
            return f"🚀 Bear Trap Breakout ({pivots.bear_trap_width_pct:.2f}%)"
        elif (pivots.bear_trap_bottom <= price <= pivots.bear_trap_top) or (c_low <= pivots.bear_trap_top and c_high >= pivots.bear_trap_bottom and price <= pivots.bear_trap_top):
            return f"🪤 Narrow Bear Trap ({pivots.bear_trap_width_pct:.2f}%)"

    # 5. Check Candlestick Pattern at CPR Zone (Central Pivot Range) - price inside or candle wick touches
    if (pivots.cpr_bottom <= price <= pivots.cpr_top) or (c_low <= pivots.cpr_top and c_high >= pivots.cpr_bottom):
        if pivots.is_narrow_cpr:
            return f"🎯 Pattern at Narrow CPR ({pivots.cpr_width_pct:.2f}%)"
        return "🎯 Pattern at CPR Zone (Retest / Bounce)"

    # 6. S2 Support Bounce (Bullish only) & R2 Resistance Rejection (Bearish only)
    if is_bull and (abs(c_low - pivots.s2) / pivots.s2 <= 0.0035 or (c_low <= pivots.s2 and price >= pivots.s2)):
        if (price - c_low) / max(c_high - c_low, 0.001) >= 0.35:
            return "🛡️ Bounce near S2 Support"
    if is_bear and (abs(c_high - pivots.r2) / pivots.r2 <= 0.0035 or (c_high >= pivots.r2 and price <= pivots.r2)):
        if (c_high - price) / max(c_high - c_low, 0.001) >= 0.35:
            return "🛡️ Rejection near R2 Resistance"

    # 7. Expansion / Breakout Levels
    if price >= pivots.r3:
        return "Above R3 (Super Breakout)"
    elif price >= pivots.r2:
        return "R2 - R3 (Bullish Extension)"
    elif price > pivots.bull_trap_top:
        return "Above R1/PDH (Strong Bullish)"
    elif price >= pivots.pp:
        return "PP - R1 (Bullish Territory)"
    elif price <= pivots.s3:
        return "Below S3 (Extreme Breakdown)"
    elif price <= pivots.s2:
        return "Below S2 (Oversold / Crash)"
    elif price < pivots.bear_trap_bottom:
        return "Below S1/PDL (Strong Breakdown)"
    else:
        return "S1 - PP (Support / Retest)"


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
    Calculates Standard Floor Pivots, Central Pivot Range (CPR), and Trap Zones.
    Narrow CPR is flagged when CPR width <= 0.20% of price.
    Narrow Trap Zone is flagged when Trap Range (R1-PDH or S1-PDL) <= 0.20% of price.
    PP = (H + L + C) / 3
    BC = (H + L) / 2
    TC = (PP - BC) + PP
    """
    pp = round((high_p + low_p + close_p) / 3.0, 2)
    bc = round((high_p + low_p) / 2.0, 2)
    tc = round((pp - bc) + pp, 2)

    cpr_top = max(tc, bc)
    cpr_bottom = min(tc, bc)
    cpr_width = round((abs(tc - bc) / pp) * 100.0, 3) if pp > 0 else 0.0
    cpr_thresh = getattr(config, "NARROW_CPR_THRESHOLD_PCT", 0.21)
    is_narrow_cpr = cpr_width <= cpr_thresh

    r1 = round(2.0 * pp - low_p, 2)
    s1 = round(2.0 * pp - high_p, 2)

    r2 = round(pp + (high_p - low_p), 2)
    s2 = round(pp - (high_p - low_p), 2)

    r3 = round(high_p + 2.0 * (pp - low_p), 2)
    s3 = round(low_p - 2.0 * (high_p - pp), 2)

    bull_trap_top = max(r1, high_p)
    bull_trap_bottom = min(r1, high_p)
    bull_trap_width_pct = round((abs(r1 - high_p) / pp) * 100.0, 3) if pp > 0 else 0.0
    trap_thresh = getattr(config, "NARROW_TRAP_ZONE_THRESHOLD_PCT", 0.21)
    is_narrow_bull_trap = bull_trap_width_pct <= trap_thresh

    bear_trap_top = max(s1, low_p)
    bear_trap_bottom = min(s1, low_p)
    bear_trap_width_pct = round((abs(s1 - low_p) / pp) * 100.0, 3) if pp > 0 else 0.0
    is_narrow_bear_trap = bear_trap_width_pct <= trap_thresh

    is_narrow_trap_zone = is_narrow_bull_trap or is_narrow_bear_trap

    return DailyPivots(
        symbol=symbol,
        date=date_str,
        pdo=round(open_p, 2),
        pdh=round(high_p, 2),
        pdl=round(low_p, 2),
        pdc=round(close_p, 2),
        pdv=volume,
        pp=pp,
        tc=tc,
        bc=bc,
        cpr_top=cpr_top,
        cpr_bottom=cpr_bottom,
        cpr_width_pct=cpr_width,
        is_narrow_cpr=is_narrow_cpr,
        r1=r1,
        r2=r2,
        r3=r3,
        s1=s1,
        s2=s2,
        s3=s3,
        bull_trap_top=bull_trap_top,
        bull_trap_bottom=bull_trap_bottom,
        bear_trap_top=bear_trap_top,
        bear_trap_bottom=bear_trap_bottom,
        bull_trap_width_pct=bull_trap_width_pct,
        bear_trap_width_pct=bear_trap_width_pct,
        is_narrow_bull_trap=is_narrow_bull_trap,
        is_narrow_bear_trap=is_narrow_bear_trap,
        is_narrow_trap_zone=is_narrow_trap_zone,
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

    cpr_breakout: bool = False
    cpr_breakdown: bool = False

    distance_from_pivot: float = 0.0
    distance_from_pdh: float = 0.0
    distance_from_pdl: float = 0.0
    distance_from_r1: float = 0.0
    distance_from_s1: float = 0.0


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

    cpr_breakout = is_valid_cpr_breakout(curr_open, curr_high, curr_low, curr_close, pivots)
    cpr_breakdown = is_valid_cpr_breakdown(curr_open, curr_high, curr_low, curr_close, pivots)

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
        cpr_breakout=cpr_breakout,
        cpr_breakdown=cpr_breakdown,
        distance_from_pivot=distance_from_pivot,
        distance_from_pdh=distance_from_pdh,
        distance_from_pdl=distance_from_pdl,
        distance_from_r1=distance_from_r1,
        distance_from_s1=distance_from_s1,
    )
