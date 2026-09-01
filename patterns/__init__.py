"""
Candlestick pattern detection package.
"""

from patterns.candlestick import (
    is_bullish_harami,
    is_bullish_engulfing,
    is_hanging_man,
    is_inverse_hammer,
    is_hammer,
    detect_candlestick_patterns,
    CandleItem,
    PatternResult,
)

__all__ = [
    "is_bullish_harami",
    "is_bullish_engulfing",
    "is_hanging_man",
    "is_inverse_hammer",
    "is_hammer",
    "detect_candlestick_patterns",
    "CandleItem",
    "PatternResult",
]
