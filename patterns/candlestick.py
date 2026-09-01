"""
Candlestick Pattern Recognition Engine.
Detects Bullish Harami, Bullish Engulfing, Hammer, Inverse Hammer, and Hanging Man.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union
import pandas as pd

import config


@dataclass
class CandleItem:
    """Lightweight representation of a candle for pattern analysis."""
    open: float
    high: float
    low: float
    close: float
    volume: int = 0
    timestamp: Optional[Any] = None

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def total_range(self) -> float:
        return max(self.high - self.low, 0.0001)

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open

    @property
    def is_bearish(self) -> bool:
        return self.close < self.open

    @property
    def upper_wick(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> float:
        return min(self.open, self.close) - self.low


@dataclass
class PatternResult:
    """Detailed pattern detection output."""
    pattern_name: str
    pattern_direction: str  # BULLISH or BEARISH
    timestamp: Any
    pattern_strength: float  # 1.0 to 3.0
    description: str


def _to_candle_item(candle: Union[CandleItem, pd.Series, dict]) -> CandleItem:
    """Converts varying candle formats to CandleItem."""
    if isinstance(candle, CandleItem):
        return candle
    if isinstance(candle, pd.Series) or isinstance(candle, dict):
        return CandleItem(
            open=float(candle.get("open", candle.get("Open", 0.0))),
            high=float(candle.get("high", candle.get("High", 0.0))),
            low=float(candle.get("low", candle.get("Low", 0.0))),
            close=float(candle.get("close", candle.get("Close", 0.0))),
            volume=int(candle.get("volume", candle.get("Volume", 0))),
            timestamp=candle.get("timestamp", candle.get("Timestamp")),
        )
    raise ValueError(f"Unsupported candle type: {type(candle)}")


def is_bullish_harami(
    prev_candle: Union[CandleItem, pd.Series, dict],
    candle: Union[CandleItem, pd.Series, dict],
    max_body_ratio: float = config.HARAMI_MAX_BODY_RATIO,
) -> bool:
    """
    Bullish Harami:
    1. Previous candle is bearish.
    2. Current candle has a smaller real body.
    3. Current real body is contained completely within previous candle's real body.
    """
    p = _to_candle_item(prev_candle)
    c = _to_candle_item(candle)

    # 1. Previous must be bearish
    if not p.is_bearish or p.body <= 0:
        return False

    # 2. Current body must be smaller than previous body
    if c.body > p.body * max_body_ratio:
        return False

    # 3. Current real body completely inside previous real body
    # For bearish previous: upper body is p.open, lower body is p.close
    curr_body_top = max(c.open, c.close)
    curr_body_bottom = min(c.open, c.close)

    tolerance = p.body * 0.05  # 5% tolerance for slight boundary overlap
    if curr_body_top <= (p.open + tolerance) and curr_body_bottom >= (p.close - tolerance):
        return True

    return False


def is_bullish_engulfing(
    prev_candle: Union[CandleItem, pd.Series, dict],
    candle: Union[CandleItem, pd.Series, dict],
) -> bool:
    """
    Bullish Engulfing:
    1. Previous candle is bearish.
    2. Current candle is bullish.
    3. Current candle's real body engulfs previous candle's real body.
    """
    p = _to_candle_item(prev_candle)
    c = _to_candle_item(candle)

    # 1. Previous bearish, Current bullish
    if not p.is_bearish or not c.is_bullish:
        return False

    # 2. Body of current must be meaningful (not a doji)
    if c.body / c.total_range < config.ENGULFING_MIN_BODY_PCT:
        return False

    # 3. Engulfs previous body
    # c.open <= p.close and c.close >= p.open
    tolerance = p.body * 0.05
    return (c.open <= p.close + tolerance) and (c.close >= p.open - tolerance)


def is_hammer(
    candle: Union[CandleItem, pd.Series, dict],
    context: str = "downtrend",
    wick_ratio: float = config.HAMMER_WICK_BODY_RATIO,
) -> bool:
    """
    Hammer:
    1. Small real body near top of candle range.
    2. Long lower wick (at least wick_ratio * real body).
    3. Very small upper wick.
    4. Trend context: Occurs after downtrend or neutral-downtrend.
    """
    c = _to_candle_item(candle)
    if c.total_range <= 0:
        return False

    body = max(c.body, c.total_range * 0.05)  # Handle doji hammer without zero division

    # Lower wick must be at least 2x body
    if c.lower_wick < wick_ratio * body:
        return False

    # Upper wick should be small (<= 30% of total range or <= 50% of body)
    if c.upper_wick > max(body * 0.75, c.total_range * 0.2):
        return False

    # Context filter: if context is enabled and explicit uptrend, reject
    if config.ENABLE_TREND_CONTEXT and context == "uptrend":
        return False

    return True


def is_inverse_hammer(
    candle: Union[CandleItem, pd.Series, dict],
    context: str = "downtrend",
    wick_ratio: float = config.INVERSE_HAMMER_WICK_BODY_RATIO,
) -> bool:
    """
    Inverse Hammer:
    1. Small real body near bottom of candle range.
    2. Long upper wick (at least wick_ratio * real body).
    3. Very small lower wick.
    4. Trend context: Occurs after downtrend.
    """
    c = _to_candle_item(candle)
    if c.total_range <= 0:
        return False

    body = max(c.body, c.total_range * 0.05)

    # Upper wick must be at least 2x body
    if c.upper_wick < wick_ratio * body:
        return False

    # Lower wick should be small
    if c.lower_wick > max(body * 0.75, c.total_range * 0.2):
        return False

    # Context filter
    if config.ENABLE_TREND_CONTEXT and context == "uptrend":
        return False

    return True


def is_hanging_man(
    prev_candle: Union[CandleItem, pd.Series, dict],
    candle: Union[CandleItem, pd.Series, dict],
    context: str = "uptrend",
    wick_ratio: float = config.HANGING_MAN_WICK_BODY_RATIO,
) -> bool:
    """
    Hanging Man:
    1. Hammer-shaped candle with long lower shadow and small body.
    2. Crucially, occurs in an UPTREND (bearish reversal warning).
    """
    c = _to_candle_item(candle)
    if c.total_range <= 0:
        return False

    body = max(c.body, c.total_range * 0.05)

    # Lower wick at least 2x body
    if c.lower_wick < wick_ratio * body:
        return False

    # Small upper wick
    if c.upper_wick > max(body * 0.75, c.total_range * 0.2):
        return False

    # Must be in uptrend context
    if config.ENABLE_TREND_CONTEXT and context != "uptrend":
        return False

    return True


def detect_candlestick_patterns(
    df: pd.DataFrame,
    context: str = "neutral",
) -> Dict[str, Any]:
    """
    Evaluates closed candle history and returns pattern detection dictionary.
    
    Expected result format:
    {
        "bullish_harami": False,
        "bullish_engulfing": True,
        "hanging_man": False,
        "inverse_hammer": False,
        "hammer": False,
        "details": [PatternResult(...)]
    }
    """
    results: Dict[str, Any] = {
        "bullish_harami": False,
        "bullish_engulfing": False,
        "hanging_man": False,
        "inverse_hammer": False,
        "hammer": False,
        "details": [],
    }

    if df.empty or len(df) < 2:
        return results

    prev_row = df.iloc[-2]
    curr_row = df.iloc[-1]

    prev_c = _to_candle_item(prev_row)
    curr_c = _to_candle_item(curr_row)
    ts = curr_c.timestamp

    # 1. Bullish Engulfing
    if is_bullish_engulfing(prev_c, curr_c):
        results["bullish_engulfing"] = True
        results["details"].append(PatternResult(
            pattern_name="BULLISH ENGULFING",
            pattern_direction="BULLISH",
            timestamp=ts,
            pattern_strength=3.0,
            description="Bullish candle engulfs previous bearish candle.",
        ))

    # 2. Bullish Harami
    if is_bullish_harami(prev_c, curr_c):
        results["bullish_harami"] = True
        results["details"].append(PatternResult(
            pattern_name="BULLISH HARAMI",
            pattern_direction="BULLISH",
            timestamp=ts,
            pattern_strength=2.0,
            description="Small candle contained inside previous bearish candle.",
        ))

    # 3. Hammer (in downtrend/neutral)
    if is_hammer(curr_c, context=context):
        results["hammer"] = True
        results["details"].append(PatternResult(
            pattern_name="HAMMER",
            pattern_direction="BULLISH",
            timestamp=ts,
            pattern_strength=2.0,
            description="Hammer candlestick with long lower shadow at support/downtrend.",
        ))

    # 4. Inverse Hammer (in downtrend/neutral)
    if is_inverse_hammer(curr_c, context=context):
        results["inverse_hammer"] = True
        results["details"].append(PatternResult(
            pattern_name="INVERSE HAMMER",
            pattern_direction="BULLISH",
            timestamp=ts,
            pattern_strength=2.0,
            description="Inverse Hammer with long upper shadow.",
        ))

    # 5. Hanging Man (in uptrend)
    if is_hanging_man(prev_c, curr_c, context=context):
        results["hanging_man"] = True
        results["details"].append(PatternResult(
            pattern_name="HANGING MAN",
            pattern_direction="BEARISH",
            timestamp=ts,
            pattern_strength=2.0,
            description="Hanging Man candle with long lower shadow at top of uptrend.",
        ))

    return results
