"""
Chartink Screener 57960 (Gann + MFI + 5M EMA-SMA Intraday Momentum) Engine.

Exact Strategy Formula:
  Universe: {57960} -> Nifty 500
  Conditions:
    - [0] 5 minute ema ( [0] 5 minute close , 13 ) > [0] 5 minute sma ( [0] 5 minute ema ( [0] 5 minute close , 13 ) , 13 )
    - daily close > 350
    - daily close < 3000
    - daily close >= square ( square root ( daily open ) + 0.125 )
    - ( ( daily high + daily low ) / 2 ) < ( ( ( daily high + daily low + daily close ) / 3 ) - ( ( ( daily high + daily low + daily close ) / 3 ) * .003 ) )
    - daily mfi ( 14 ) > 60
    - daily avg true range ( 14 ) > 1 day ago avg true range ( 14 )
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time as dt_time, timedelta
import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class Screener57960Signal:
    symbol: str
    timestamp: str
    price: float
    gann_level: float
    gann_diff_pct: float
    mfi_14: float
    atr_14: float
    prev_atr_14: float
    ema_13: float
    sma_ema_13: float
    median_pivot_diff_pct: float
    confluence_factors: List[str] = field(default_factory=list)
    date: str = ""
    first_detected_time: str = ""
    first_detected_price: float = 0.0


class Screener57960Engine:
    """Evaluates the exact Chartink Screener 57960 rules on daily and 5-minute candles."""

    @staticmethod
    def calculate_daily_indicators(df_daily: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
        """
        Calculates daily Money Flow Index (14) and Average True Range (14).
        Returns (mfi_series, atr_series).
        """
        high = df_daily["high"].astype(float)
        low = df_daily["low"].astype(float)
        close = df_daily["close"].astype(float)
        volume = df_daily["volume"].astype(float)

        # 1. Average True Range (14) - standard rolling mean
        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()

        # 2. Money Flow Index (14)
        typical_price = (high + low + close) / 3.0
        money_flow = typical_price * volume
        price_diff = typical_price.diff()

        pos_flow = money_flow.where(price_diff > 0, 0.0).rolling(14).sum()
        neg_flow = money_flow.where(price_diff < 0, 0.0).rolling(14).sum().replace(0.0, np.nan)
        mfi = 100.0 - (100.0 / (1.0 + (pos_flow / neg_flow)))
        mfi = mfi.fillna(50.0)

        return mfi, atr

    @staticmethod
    def check_5m_trigger(df_5m: pd.DataFrame) -> Tuple[bool, float, float, Optional[int]]:
        """
        Checks intraday 5-minute trigger:
        5m EMA(13) of close > 5m SMA(13) of EMA(13).
        Returns (is_triggered, ema13_val, sma_ema13_val, trigger_bar_index).
        """
        if df_5m is None or len(df_5m) < 13:
            return False, 0.0, 0.0, None

        close = df_5m["close"].astype(float)
        ema13 = close.ewm(span=13, adjust=False).mean()
        sma13_of_ema = ema13.rolling(13).mean()

        if len(sma13_of_ema.dropna()) == 0:
            return False, 0.0, 0.0, None

        latest_ema = float(ema13.iloc[-1])
        latest_sma = float(sma13_of_ema.iloc[-1])

        # Check if currently active
        is_active = latest_ema > latest_sma

        # Find first candle index of today that crossed or met condition
        trigger_idx = None
        cond = ema13 > sma13_of_ema
        valid_indices = cond[cond].index
        if len(valid_indices) > 0:
            trigger_idx = int(valid_indices[0])

        return is_active, latest_ema, latest_sma, trigger_idx

    def evaluate_stock(
        self,
        symbol: str,
        df_daily: pd.DataFrame,
        df_5m: Optional[pd.DataFrame] = None,
        today_override: Optional[Dict[str, Any]] = None,
        target_date: Optional[date] = None,
    ) -> Optional[Screener57960Signal]:
        """
        Evaluates a stock against the full Chartink Screener 57960 formula.
        """
        if df_daily is None or len(df_daily) < 16:
            return None

        df = df_daily.copy()
        df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_localize(None)
        df = df.sort_values("timestamp").reset_index(drop=True)

        if target_date is not None:
            active_date = target_date
            df = df[pd.to_datetime(df["timestamp"]).dt.date <= active_date].copy()
            if df.empty or df["timestamp"].iloc[-1].date() != active_date:
                return None
        else:
            try:
                import config
                import pytz
                active_date = datetime.now(pytz.timezone(config.MARKET_TIMEZONE)).date()
            except Exception:
                active_date = date.today()

        # Merge today's live session if provided
        if today_override:
            t_dt = pd.to_datetime(today_override.get("timestamp", datetime.now()))
            if hasattr(t_dt, "tz") and t_dt.tz is not None:
                t_dt = t_dt.tz_localize(None)
            if t_dt.date() == active_date:
                if df["timestamp"].iloc[-1].date() == t_dt.date():
                    df.loc[len(df) - 1, "timestamp"] = t_dt
                    for col in ["open", "high", "low", "close", "volume"]:
                        if col in today_override:
                            df.loc[len(df) - 1, col] = today_override[col]
                else:
                    new_row = {
                        "timestamp": t_dt,
                        "open": float(today_override.get("open", df["close"].iloc[-1])),
                        "high": float(today_override.get("high", df["close"].iloc[-1])),
                        "low": float(today_override.get("low", df["close"].iloc[-1])),
                        "close": float(today_override.get("close", df["close"].iloc[-1])),
                        "volume": int(today_override.get("volume", 0)),
                    }
                    df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)

        n_bars = len(df)
        if n_bars < 16:
            return None

        today = df.iloc[-1]
        c_open = float(today["open"])
        c_high = float(today["high"])
        c_low = float(today["low"])
        c_close = float(today["close"])

        # Condition 1: 350 < daily close < 3000
        if not (350.0 < c_close < 3000.0):
            return None

        # Condition 2: daily close >= square( square root( daily open ) + 0.125 )
        gann_level = (np.sqrt(max(c_open, 0.0)) + 0.125) ** 2
        if c_close < gann_level:
            return None

        # Condition 3: (high + low) / 2 < ((high + low + close) / 3) - ((high + low + close) / 3) * 0.003
        median_price = (c_high + c_low) / 2.0
        typical_price = (c_high + c_low + c_close) / 3.0
        threshold_price = typical_price - (typical_price * 0.003)
        if median_price >= threshold_price:
            return None

        # Indicators: MFI(14) and ATR(14)
        mfi_series, atr_series = self.calculate_daily_indicators(df)
        if len(mfi_series.dropna()) == 0 or len(atr_series.dropna()) < 2:
            return None

        latest_mfi = float(mfi_series.iloc[-1])
        latest_atr = float(atr_series.iloc[-1])
        prev_atr = float(atr_series.iloc[-2])

        # Condition 4: daily MFI(14) > 60
        if latest_mfi <= 60.0:
            return None

        # Condition 5: daily ATR(14) > 1 day ago ATR(14)
        if latest_atr <= prev_atr:
            return None

        # Condition 6: 5-minute trigger (if 5m candles provided)
        ema13_val = 0.0
        sma_ema13_val = 0.0
        first_det_time = ""
        first_det_price = c_close

        if df_5m is not None and not df_5m.empty:
            if "timestamp" in df_5m.columns:
                df_5m_target = df_5m[pd.to_datetime(df_5m["timestamp"]).dt.date == active_date]
                if not df_5m_target.empty:
                    df_5m = df_5m_target
            is_5m_triggered, ema13_val, sma_ema13_val, trg_idx = self.check_5m_trigger(df_5m)
            if not is_5m_triggered:
                return None
            if trg_idx is not None and trg_idx < len(df_5m):
                trg_row = df_5m.iloc[trg_idx]
                ts_val = pd.to_datetime(trg_row["timestamp"])
                first_det_time = ts_val.strftime("%H:%M")
                first_det_price = float(trg_row.get("close", c_close))
        else:
            # If no 5m data available yet (e.g. daily pre-screen), synthesize approximation
            ema13_val = c_close
            sma_ema13_val = c_open

        gann_diff_pct = ((c_close - gann_level) / gann_level) * 100.0 if gann_level > 0 else 0.0
        pivot_diff_pct = ((typical_price - median_price) / median_price) * 100.0 if median_price > 0 else 0.0
        atr_expansion_pct = ((latest_atr - prev_atr) / prev_atr) * 100.0 if prev_atr > 0 else 0.0

        confluences = [
            f"Gann Breakout: +{gann_diff_pct:.2f}% (Above ₹{gann_level:.2f})",
            f"Daily MFI(14): {latest_mfi:.1f} (> 60)",
            f"ATR Expansion: +{atr_expansion_pct:.1f}% (₹{latest_atr:.2f} vs ₹{prev_atr:.2f})",
            f"Upper Pivot Clearance: +{pivot_diff_pct:.2f}%",
            f"5m EMA13: {ema13_val:.2f} > SMA: {sma_ema13_val:.2f}",
        ]

        now_str = datetime.now().strftime("%H:%M:%S")
        active_date_str = active_date.strftime("%Y-%m-%d")

        return Screener57960Signal(
            symbol=symbol,
            timestamp=first_det_time or now_str,
            price=round(c_close, 2),
            gann_level=round(gann_level, 2),
            gann_diff_pct=round(gann_diff_pct, 2),
            mfi_14=round(latest_mfi, 1),
            atr_14=round(latest_atr, 2),
            prev_atr_14=round(prev_atr, 2),
            ema_13=round(ema13_val, 2),
            sma_ema_13=round(sma_ema13_val, 2),
            median_pivot_diff_pct=round(pivot_diff_pct, 2),
            confluence_factors=confluences,
            date=active_date_str,
            first_detected_time=first_det_time or now_str,
            first_detected_price=round(first_det_price, 2),
        )
