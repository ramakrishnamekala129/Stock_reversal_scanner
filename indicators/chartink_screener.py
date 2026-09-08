"""
Chartink Intraday Screener Engine.
Implements the exact technical analysis formula from Chartink Screener:
https://chartink.com/screener/intraday-screener-27102787

Formulas:
1. Master Filter (Mandatory):
   ((High + Low) / 2) < (((High + Low + Close) / 3) - (((High + Low + Close) / 3) * 0.003))
   Ensures Close is in the upper half with at least 0.3% pivot clearance.

2. Sub-Strategy 1 (Monthly Breakout & Heavy Turnover):
   - SMA(Volume, 20) * Open >= 10,00,00,000 (10 Cr Turnover)
   - Monthly Close >= Previous Month High

3. Sub-Strategy 2 (20-Week High Breakout & 200 SMA):
   - Weekly Close > Previous 20 Weeks Max Close
   - Daily Close > Daily SMA(Close, 200)

4. Sub-Strategy 3 (Intraday Momentum, Multi-SMA Crossover & Volume Surge):
   - SMA(Volume, 7) > 100,000
   - Close >= 100
   - Low > Previous Day Low (Higher Low)
   - Close > Low and Close > Open (Green Candle)
   - Close crossed above ANY SMA(11..35)
   - RSI(14) crossed above ANY level (11..55)
   - Volume >= ANY SMA(Volume, 5..20)
"""

from dataclasses import dataclass, field
from datetime import date, datetime, time as dt_time, timedelta
import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class ChartinkSignal:
    symbol: str
    timestamp: str
    price: float
    matched_strategies: List[str]  # e.g. ['Monthly Breakout', 'MA+RSI+Vol Surge']
    primary_strategy: str
    median_pivot_diff_pct: float   # How much Typical exceeds Median %
    turnover_cr: float
    rsi_14: float
    ma_crossed: List[int]          # e.g. [15, 20]
    vol_surge_ratio: float
    confluence_factors: List[str] = field(default_factory=list)
    fut_symbol: str = ""
    lot_size: int = 0
    liquidity_tier: str = "Normal"
    is_most_liquid: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp,
            "price": round(self.price, 2),
            "matched_strategies": self.matched_strategies,
            "strategy_tag": " • ".join(self.matched_strategies),
            "primary_strategy": self.primary_strategy,
            "median_pivot_diff_pct": round(self.median_pivot_diff_pct, 2),
            "turnover_cr": round(self.turnover_cr, 2),
            "rsi_14": round(self.rsi_14, 1),
            "ma_crossed": self.ma_crossed,
            "ma_crossed_str": f"SMA {','.join(map(str, self.ma_crossed[:3]))}" if self.ma_crossed else "--",
            "vol_surge_ratio": round(self.vol_surge_ratio, 2),
            "confluence_factors": self.confluence_factors,
            "reasons_str": " • ".join(self.confluence_factors) if self.confluence_factors else "--",
            "fut_symbol": self.fut_symbol,
            "lot_size": self.lot_size,
            "liquidity_tier": self.liquidity_tier,
            "is_most_liquid": self.is_most_liquid,
        }


class ChartinkIntradayEngine:
    """Evaluates the Chartink Intraday Screener rules on daily / multi-timeframe candles."""

    def __init__(self):
        pass

    @staticmethod
    def calculate_rsi(series: pd.Series, length: int = 14) -> pd.Series:
        """Calculates Wilder's standard RSI(14)."""
        delta = series.diff()
        gain = delta.clip(lower=0.0)
        loss = -delta.clip(upper=0.0)

        alpha = 1.0 / length
        avg_gain = gain.ewm(alpha=alpha, adjust=False).mean()
        avg_loss = loss.ewm(alpha=alpha, adjust=False).mean()

        rs = avg_gain / avg_loss.replace(0.0, np.nan)
        rsi = 100.0 - (100.0 / (1.0 + rs))
        return rsi.fillna(50.0)

    def evaluate_stock(
        self,
        symbol: str,
        df_daily: pd.DataFrame,
        today_override: Optional[Dict[str, Any]] = None,
        fut_symbol: str = "",
        lot_size: int = 0,
        turnover_cr: float = 0.0,
        liquidity_tier: str = "Normal",
        is_most_liquid: bool = False,
    ) -> Optional[ChartinkSignal]:
        """
        Evaluates a stock against the Chartink Intraday Screener formula.
        df_daily: historical daily bars sorted by timestamp ascending.
        today_override: optional dict containing today's latest live candle (open, high, low, close, volume).
        """
        if df_daily is None or len(df_daily) < 15:
            return None

        df = df_daily.copy()
        if not pd.api.types.is_datetime64_any_dtype(df["timestamp"]):
            df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.sort_values("timestamp").reset_index(drop=True)

        # Merge today's live session if provided
        if today_override:
            t_dt = pd.to_datetime(today_override.get("timestamp", datetime.now()))
            # If today already exists, update last row, else append
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
        if n_bars < 2:
            return None

        today = df.iloc[-1]
        prev = df.iloc[-2]

        c_open = float(today["open"])
        c_high = float(today["high"])
        c_low = float(today["low"])
        c_close = float(today["close"])
        c_vol = float(today["volume"])

        p_low = float(prev["low"])
        p_close = float(prev["close"])

        # ═══════════════════════════════════════════════════════════════════
        # 1. Mandatory Master Condition:
        # ((High + Low) / 2) < (((High + Low + Close) / 3) - (((High + Low + Close) / 3) * 0.003))
        # ═══════════════════════════════════════════════════════════════════
        median_price = (c_high + c_low) / 2.0
        typical_price = (c_high + c_low + c_close) / 3.0
        threshold_price = typical_price - (typical_price * 0.003)

        if median_price >= threshold_price:
            return None  # Fails master filter

        diff_pct = ((typical_price - median_price) / median_price) * 100.0 if median_price > 0 else 0.0
        confluences = [f"Master Pivot Clearance: +{diff_pct:.2f}% (Close in Upper Range)"]

        matched_sub_strategies = []

        # ═══════════════════════════════════════════════════════════════════
        # 2. Sub-Strategy 1: Monthly Breakout & Turnover >= 10 Cr
        # - SMA(Volume, 20) * Open >= 100,000,000
        # - Monthly Close >= Previous Month High
        # ═══════════════════════════════════════════════════════════════════
        sma_vol_20 = float(df["volume"].rolling(min(20, n_bars)).mean().iloc[-1])
        calculated_turnover = (sma_vol_20 * c_open)
        turnover_in_cr = calculated_turnover / 10_000_000.0

        sub1_turnover_pass = calculated_turnover >= 100_000_000.0

        # Calculate previous month high
        cur_month = today["timestamp"].month
        cur_year = today["timestamp"].year
        # Mask for previous calendar month
        prev_month = cur_month - 1 if cur_month > 1 else 12
        prev_year = cur_year if cur_month > 1 else cur_year - 1

        pm_df = df[(df["timestamp"].dt.month == prev_month) & (df["timestamp"].dt.year == prev_year)]
        if not pm_df.empty:
            prev_month_high = float(pm_df["high"].max())
            monthly_breakout = (c_close >= prev_month_high)
        else:
            # Fallback: lookback 22 to 44 bars
            lb_start = max(0, n_bars - 44)
            lb_end = max(1, n_bars - 22)
            prev_month_high = float(df["high"].iloc[lb_start:lb_end].max()) if lb_end > lb_start else float("inf")
            monthly_breakout = (c_close >= prev_month_high)

        if sub1_turnover_pass and monthly_breakout:
            matched_sub_strategies.append("Monthly Breakout (Sub 1)")
            confluences.append(f"Monthly Breakout (Close ₹{c_close:.2f} >= Prev Month High ₹{prev_month_high:.2f})")
            confluences.append(f"Institutional Turnover: ₹{turnover_in_cr:.1f} Cr >= ₹10 Cr")

        # ═══════════════════════════════════════════════════════════════════
        # 3. Sub-Strategy 2: 20-Week Multi-Month Breakout & 200 SMA
        # - Weekly Close > 1 Week Ago Max(20, Weekly Close)
        # - Daily Close > Daily SMA(Close, 200)
        # ═══════════════════════════════════════════════════════════════════
        try:
            df_weekly = df.set_index("timestamp").resample("W-FRI").agg({
                "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"
            }).dropna().reset_index()

            if len(df_weekly) >= 5:
                # 20 weeks lookback before this week
                w_lookback = min(20, len(df_weekly) - 1)
                max_20w_close = float(df_weekly["close"].iloc[-w_lookback - 1:-1].max())
                weekly_breakout = (c_close > max_20w_close)
            else:
                max_20w_close = float("inf")
                weekly_breakout = False
        except Exception as e:
            logger.debug(f"Weekly resample error for {symbol}: {e}")
            max_20w_close = float("inf")
            weekly_breakout = False

        sma_200_len = min(200, n_bars)
        sma_200 = float(df["close"].rolling(sma_200_len).mean().iloc[-1])
        above_200_sma = (c_close > sma_200)

        if weekly_breakout and above_200_sma:
            matched_sub_strategies.append("20W High + 200 SMA (Sub 2)")
            confluences.append(f"20-Week High Breakout (₹{c_close:.2f} > ₹{max_20w_close:.2f})")
            confluences.append(f"Above 200 SMA (₹{c_close:.2f} > ₹{sma_200:.2f})")

        # ═══════════════════════════════════════════════════════════════════
        # 4. Sub-Strategy 3: Intraday Momentum, Multi-SMA & Volume Surge
        # - Daily SMA(Volume, 7) > 100,000
        # - Daily Close >= 100
        # - Daily Low > 1 Day Ago Low
        # - Daily Close > Low & Close > Open (Green Candle)
        # - Close crossed above ANY SMA(11..35)
        # - RSI(14) crossed above ANY integer (11..55)
        # - Daily Volume >= ANY SMA(Volume, 5..20)
        # ═══════════════════════════════════════════════════════════════════
        sma_vol_7 = float(df["volume"].rolling(min(7, n_bars)).mean().iloc[-1])
        sub3_vol7_pass = sma_vol_7 > 100_000.0
        sub3_price_pass = c_close >= 100.0
        sub3_higher_low = c_low > p_low
        sub3_green_candle = (c_close > c_low) and (c_close > c_open)

        # Check ANY SMA Crossover across range 11 to 35
        mas_crossed = []
        for period in range(11, 36):
            if n_bars > period:
                sma_now = float(df["close"].rolling(period).mean().iloc[-1])
                sma_prev = float(df["close"].rolling(period).mean().iloc[-2])
                if (c_close > sma_now) and (p_close <= sma_prev):
                    mas_crossed.append(period)

        sub3_ma_cross_pass = len(mas_crossed) > 0

        # Calculate RSI(14)
        rsi_series = self.calculate_rsi(df["close"], length=14)
        rsi_now = float(rsi_series.iloc[-1])
        rsi_prev = float(rsi_series.iloc[-2])

        # Check ANY RSI Cross Above from 11 to 55
        rsi_levels_crossed = [lvl for lvl in range(11, 56) if (rsi_now > lvl and rsi_prev <= lvl)]
        sub3_rsi_cross_pass = len(rsi_levels_crossed) > 0

        # Check ANY Volume SMA(5..20) satisfied
        vol_surges = []
        for v_period in range(5, 21):
            if n_bars >= v_period:
                v_sma = float(df["volume"].rolling(v_period).mean().iloc[-1])
                if c_vol >= v_sma:
                    vol_surges.append(v_period)

        sub3_vol_surge_pass = len(vol_surges) > 0
        min_v_sma = float(df["volume"].rolling(min(10, n_bars)).mean().iloc[-1])
        vol_ratio = (c_vol / min_v_sma) if min_v_sma > 0 else 1.0

        if (sub3_vol7_pass and sub3_price_pass and sub3_higher_low and sub3_green_candle
                and sub3_ma_cross_pass and sub3_rsi_cross_pass and sub3_vol_surge_pass):
            matched_sub_strategies.append("MA + RSI + Vol Surge (Sub 3)")
            confluences.append(f"SMA Cross: {len(mas_crossed)} MAs ({','.join(map(str, mas_crossed[:3]))})")
            confluences.append(f"RSI(14): {rsi_now:.1f} (Crossed {rsi_levels_crossed[-1]})")
            confluences.append(f"Volume Surge: {vol_ratio:.1f}x (>= {len(vol_surges)} Vol SMAs)")
            confluences.append("Higher Low & Bullish Green Candle")

        # If none of the 3 sub-strategies passed, no signal
        if not matched_sub_strategies:
            return None

        t_val = today["timestamp"]
        if isinstance(t_val, (pd.Timestamp, datetime)):
            # If date-only daily candle without intraday time (00:00:00), use live scan time
            if t_val.time() == dt_time(0, 0, 0):
                ts_str = datetime.now().strftime("%H:%M:%S")
            else:
                ts_str = t_val.strftime("%H:%M:%S")
        else:
            ts_str = datetime.now().strftime("%H:%M:%S")

        return ChartinkSignal(
            symbol=symbol,
            timestamp=ts_str,
            price=c_close,
            matched_strategies=matched_sub_strategies,
            primary_strategy=matched_sub_strategies[0],
            median_pivot_diff_pct=diff_pct,
            turnover_cr=turnover_in_cr if turnover_in_cr > 0 else (turnover_cr or 0.0),
            rsi_14=rsi_now,
            ma_crossed=mas_crossed,
            vol_surge_ratio=vol_ratio,
            confluence_factors=confluences,
            fut_symbol=fut_symbol or f"{symbol} FUT",
            lot_size=lot_size,
            liquidity_tier=liquidity_tier,
            is_most_liquid=is_most_liquid,
        )
