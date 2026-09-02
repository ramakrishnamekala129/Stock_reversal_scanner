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
    context: str = "downtrend",
    max_body_ratio: float = config.HARAMI_MAX_BODY_RATIO,
) -> bool:
    """
    Bullish Harami:
    1. Previous candle is bearish.
    2. Current candle has a smaller real body and is bullish (buyers taking control).
    3. Current real body is contained completely within previous candle's real body.
    4. Meaningful preceding downtrend or at support (not in established uptrend).
    """
    p = _to_candle_item(prev_candle)
    c = _to_candle_item(candle)

    # 1. Previous must be bearish
    if not p.is_bearish or p.body <= 0:
        return False

    # 2. Current must be bullish (or tiny doji) - a red candle inside a red candle is NOT a bullish reversal
    if c.is_bearish and c.body > p.body * 0.15:
        return False

    # 3. Current body must be smaller than previous body
    if c.body > p.body * max_body_ratio:
        return False

    # 4. Current real body completely inside previous real body
    curr_body_top = max(c.open, c.close)
    curr_body_bottom = min(c.open, c.close)

    tolerance = p.body * 0.05  # 5% tolerance for slight boundary overlap
    if curr_body_top > (p.open + tolerance) or curr_body_bottom < (p.close - tolerance):
        return False

    # 5. Trend context: Reject if explicit uptrend
    if config.ENABLE_TREND_CONTEXT and context == "uptrend":
        return False

    return True


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


def is_pin_bar(
    candle: Union[CandleItem, pd.Series, dict],
    context: str = "downtrend",
    min_lower_wick_pct: float = 0.45,
) -> bool:
    """
    Bullish Pin Bar / Long Lower Shadow Rejection:
    1. Long lower wick (at least 45% of the total candle range).
    2. Close finishes in the upper half of the candle range (buyers pushing price up).
    3. Upper wick is moderate (<= 35% of total range).
    4. Trend context: Not in an established strong uptrend.
    """
    c = _to_candle_item(candle)
    if c.total_range <= 0:
        return False

    # Lower shadow must be at least min_lower_wick_pct of total candle range
    lower_wick_pct = c.lower_wick / c.total_range
    if lower_wick_pct < min_lower_wick_pct:
        return False

    # Close must be in the upper 55% of the candle range (rejection of lows)
    close_pos = (c.close - c.low) / c.total_range
    if close_pos < 0.40:
        return False

    # Upper wick should not dominate
    if c.upper_wick > c.total_range * 0.35:
        return False

    # Context filter: reject in explicit uptrend
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


def is_bearish_engulfing(
    prev_candle: Union[CandleItem, pd.Series, dict],
    candle: Union[CandleItem, pd.Series, dict],
) -> bool:
    """
    Bearish Engulfing:
    1. Previous candle is bullish.
    2. Current candle is bearish.
    3. Current candle's real body engulfs previous candle's real body.
    """
    p = _to_candle_item(prev_candle)
    c = _to_candle_item(candle)

    # 1. Previous bullish, Current bearish
    if not p.is_bullish or not c.is_bearish:
        return False

    # 2. Body of current must be meaningful (not a doji)
    if c.body / c.total_range < config.ENGULFING_MIN_BODY_PCT:
        return False

    # 3. Engulfs previous body
    tolerance = p.body * 0.05
    return (c.open >= p.close - tolerance) and (c.close <= p.open + tolerance)


def is_bearish_harami(
    prev_candle: Union[CandleItem, pd.Series, dict],
    candle: Union[CandleItem, pd.Series, dict],
    context: str = "uptrend",
    max_body_ratio: float = config.HARAMI_MAX_BODY_RATIO,
) -> bool:
    """
    Bearish Harami:
    1. Previous candle is bullish.
    2. Current candle has a smaller real body and is bearish (sellers taking control).
    3. Current real body is contained completely within previous candle's real body.
    4. Meaningful preceding uptrend or at resistance (not in established downtrend).
    """
    p = _to_candle_item(prev_candle)
    c = _to_candle_item(candle)

    # 1. Previous must be bullish
    if not p.is_bullish or p.body <= 0:
        return False

    # 2. Current must be bearish (or tiny doji) - a green candle inside a green candle is NOT a bearish reversal
    if c.is_bullish and c.body > p.body * 0.15:
        return False

    # 3. Current body must be smaller than previous body
    if c.body > p.body * max_body_ratio:
        return False

    # 4. Current real body completely inside previous real body
    curr_body_top = max(c.open, c.close)
    curr_body_bottom = min(c.open, c.close)

    tolerance = p.body * 0.05
    if curr_body_top > (p.close + tolerance) or curr_body_bottom < (p.open - tolerance):
        return False

    # 5. Trend context: Reject if explicit downtrend
    if config.ENABLE_TREND_CONTEXT and context == "downtrend":
        return False

    return True


def is_shooting_star(
    candle: Union[CandleItem, pd.Series, dict],
    context: str = "uptrend",
    wick_ratio: float = config.INVERSE_HAMMER_WICK_BODY_RATIO,
) -> bool:
    """
    Shooting Star:
    1. Small real body near bottom of candle range.
    2. Long upper wick (at least wick_ratio * real body).
    3. Very small lower wick.
    4. Trend context: Occurs at resistance or in an uptrend (bearish reversal).
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

    # Context filter: if trend context is enabled and in downtrend, reject
    if config.ENABLE_TREND_CONTEXT and context == "downtrend":
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


def is_bullish_marubozu(
    candle: Union[CandleItem, pd.Series, dict],
    min_body_ratio: float = 0.70,
) -> bool:
    """
    Bullish Marubozu (Aggressive Breakout / Expansion Bar):
    1. Candle is bullish (close > open).
    2. Real body covers >= 70% of the entire candle range.
    3. Upper and lower wicks are minimal (<= 20% each).
    """
    c = _to_candle_item(candle)
    if not c.is_bullish or c.total_range <= 0:
        return False

    body_ratio = c.body / c.total_range
    if body_ratio < min_body_ratio:
        return False

    upper_wick_ratio = c.upper_wick / c.total_range
    lower_wick_ratio = c.lower_wick / c.total_range
    return upper_wick_ratio <= 0.20 and lower_wick_ratio <= 0.20


def is_bearish_marubozu(
    candle: Union[CandleItem, pd.Series, dict],
    min_body_ratio: float = 0.70,
) -> bool:
    """
    Bearish Marubozu (Aggressive Breakdown / Expansion Bar):
    1. Candle is bearish (close < open).
    2. Real body covers >= 70% of the entire candle range.
    3. Upper and lower wicks are minimal (<= 20% each).
    """
    c = _to_candle_item(candle)
    if not c.is_bearish or c.total_range <= 0:
        return False

    body_ratio = c.body / c.total_range
    if body_ratio < min_body_ratio:
        return False

    upper_wick_ratio = c.upper_wick / c.total_range
    lower_wick_ratio = c.lower_wick / c.total_range
    return upper_wick_ratio <= 0.20 and lower_wick_ratio <= 0.20


def detect_candlestick_patterns(
    df: pd.DataFrame,
    context: str = "neutral",
) -> Dict[str, Any]:
    """
    Evaluates closed candle history and returns pattern detection dictionary.
    """
    results: Dict[str, Any] = {
        "bullish_harami": False,
        "bearish_harami": False,
        "bullish_engulfing": False,
        "bearish_engulfing": False,
        "hanging_man": False,
        "shooting_star": False,
        "inverse_hammer": False,
        "hammer": False,
        "bullish_marubozu": False,
        "bearish_marubozu": False,
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

    # 2. Bearish Engulfing
    if is_bearish_engulfing(prev_c, curr_c):
        results["bearish_engulfing"] = True
        results["details"].append(PatternResult(
            pattern_name="BEARISH ENGULFING",
            pattern_direction="BEARISH",
            timestamp=ts,
            pattern_strength=3.0,
            description="Bearish candle engulfs previous bullish candle.",
        ))

    # 3. Bullish Harami (in downtrend/neutral)
    if is_bullish_harami(prev_c, curr_c, context=context):
        results["bullish_harami"] = True
        results["details"].append(PatternResult(
            pattern_name="BULLISH HARAMI",
            pattern_direction="BULLISH",
            timestamp=ts,
            pattern_strength=2.0,
            description="Small candle contained inside previous bearish candle.",
        ))

    # 4. Bearish Harami (in uptrend/neutral)
    if is_bearish_harami(prev_c, curr_c, context=context):
        results["bearish_harami"] = True
        results["details"].append(PatternResult(
            pattern_name="BEARISH HARAMI",
            pattern_direction="BEARISH",
            timestamp=ts,
            pattern_strength=2.0,
            description="Small candle contained inside previous bullish candle.",
        ))

    # 5. Hammer & Pin Bar (in downtrend/neutral)
    if is_hammer(curr_c, context=context):
        results["hammer"] = True
        results["details"].append(PatternResult(
            pattern_name="HAMMER",
            pattern_direction="BULLISH",
            timestamp=ts,
            pattern_strength=2.0,
            description="Hammer candlestick with long lower shadow at support/downtrend.",
        ))
    elif is_pin_bar(curr_c, context=context):
        results["pin_bar"] = True
        results["details"].append(PatternResult(
            pattern_name="PIN_BAR",
            pattern_direction="BULLISH",
            timestamp=ts,
            pattern_strength=2.0,
            description="Bullish Pin Bar with strong lower shadow rejection at support.",
        ))

    # 6. Inverse Hammer (Bearish Upper Shadow Rejection)
    if is_inverse_hammer(curr_c, context=context):
        results["inverse_hammer"] = True
        results["details"].append(PatternResult(
            pattern_name="INVERSE HAMMER",
            pattern_direction="BEARISH",
            timestamp=ts,
            pattern_strength=2.0,
            description="Inverse Hammer with long upper shadow showing overhead selling rejection.",
        ))

    # 7. Shooting Star (in uptrend/resistance)
    if is_shooting_star(curr_c, context=context):
        results["shooting_star"] = True
        results["details"].append(PatternResult(
            pattern_name="SHOOTING STAR",
            pattern_direction="BEARISH",
            timestamp=ts,
            pattern_strength=2.0,
            description="Shooting Star with long upper shadow at resistance/uptrend.",
        ))

    # 8. Hanging Man (in uptrend)
    if is_hanging_man(prev_c, curr_c, context=context):
        results["hanging_man"] = True
        results["details"].append(PatternResult(
            pattern_name="HANGING MAN",
            pattern_direction="BEARISH",
            timestamp=ts,
            pattern_strength=2.0,
            description="Hanging Man candle with long lower shadow at top of uptrend.",
        ))

    # 9. Bullish Marubozu (Strong Breakout / Expansion Bar)
    if not results["bullish_engulfing"] and is_bullish_marubozu(curr_c):
        results["bullish_marubozu"] = True
        results["details"].append(PatternResult(
            pattern_name="BULLISH MARUBOZU",
            pattern_direction="BULLISH",
            timestamp=ts,
            pattern_strength=3.0,
            description="Strong bullish breakout expansion bar with minimal wicks.",
        ))

    # 10. Bearish Marubozu (Strong Breakdown / Expansion Bar)
    if not results["bearish_engulfing"] and is_bearish_marubozu(curr_c):
        results["bearish_marubozu"] = True
        results["details"].append(PatternResult(
            pattern_name="BEARISH MARUBOZU",
            pattern_direction="BEARISH",
            timestamp=ts,
            pattern_strength=3.0,
            description="Strong bearish breakdown expansion bar with minimal wicks.",
        ))

    return results
