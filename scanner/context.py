"""
Market Context Analyzer Module.
Determines short-term trend direction (uptrend, downtrend, neutral) for pattern validation.
"""

import pandas as pd
import config


def determine_market_context(
    df_candles: pd.DataFrame,
    lookback: int = config.TREND_LOOKBACK_CANDLES,
) -> str:
    """
    Analyzes preceding candles to determine trend context:
    'uptrend', 'downtrend', or 'neutral'.
    """
    if df_candles.empty or len(df_candles) < 3:
        return "neutral"

    # Use the preceding closed candles (excluding current one)
    subset = df_candles.iloc[:-1].tail(lookback)
    if len(subset) < 2:
        return "neutral"

    first_close = float(subset.iloc[0]["close"])
    last_close = float(subset.iloc[-1]["close"])
    pct_change = (last_close - first_close) / first_close * 100.0

    # Count higher highs / lower lows
    highs = subset["high"].tolist()
    lows = subset["low"].tolist()

    is_higher_highs = all(highs[i] >= highs[i - 1] for i in range(1, len(highs)))
    is_lower_lows = all(lows[i] <= lows[i - 1] for i in range(1, len(lows)))

    if pct_change > 0.25 or is_higher_highs:
        return "uptrend"
    elif pct_change < -0.25 or is_lower_lows:
        return "downtrend"
    else:
        return "neutral"
