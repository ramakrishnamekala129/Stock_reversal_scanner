"""
Volume Confirmation & Relative Volume Analysis Module.
Calculates rolling average volume and relative volume surges.
"""

from dataclasses import dataclass
from typing import Optional
import pandas as pd

import config


@dataclass
class VolumeMetrics:
    """Volume confirmation metrics."""
    current_volume: int
    average_volume: float
    relative_volume: float
    is_high_volume: bool


def calculate_volume_metrics(
    df_candles: pd.DataFrame,
    lookback: int = config.VOLUME_LOOKBACK,
    min_rel_volume: float = config.MIN_RELATIVE_VOLUME,
) -> Optional[VolumeMetrics]:
    """
    Computes volume metrics for the latest closed candle against a rolling lookback window.
    """
    if df_candles.empty or len(df_candles) < 2:
        if not df_candles.empty:
            curr_vol = int(df_candles.iloc[-1]["volume"])
            return VolumeMetrics(
                current_volume=curr_vol,
                average_volume=float(curr_vol),
                relative_volume=1.0,
                is_high_volume=False,
            )
        return None

    # Use preceding candles (excluding current one) to calculate average
    prev_slice = df_candles.iloc[:-1].tail(lookback)
    avg_vol = float(prev_slice["volume"].mean()) if not prev_slice.empty else 1.0
    if avg_vol <= 0:
        avg_vol = 1.0

    curr_vol = int(df_candles.iloc[-1]["volume"])
    rel_vol = round(curr_vol / avg_vol, 2)
    is_high = rel_vol >= min_rel_volume

    return VolumeMetrics(
        current_volume=curr_vol,
        average_volume=round(avg_vol, 2),
        relative_volume=rel_vol,
        is_high_volume=is_high,
    )
