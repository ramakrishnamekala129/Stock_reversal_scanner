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
    first_detected_time: str = ""
    first_detected_price: float = 0.0
    date: str = ""
    option_strike: str = ""
    option_symbol: str = ""
    option_lot_size: int = 0
    option_expiry: str = ""

    def to_dict(self) -> Dict[str, Any]:
        display_ts = self.first_detected_time or self.timestamp
        return {
            "symbol": self.symbol,
            "date": self.date,
            "timestamp": display_ts,
            "first_detected_time": display_ts,
            "first_detected_price": round(self.first_detected_price or self.price, 2),
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
            "option_strike": self.option_strike,
            "option_symbol": self.option_symbol,
            "option_lot_size": self.option_lot_size,
            "option_expiry": self.option_expiry,
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
        target_date: Optional[date] = None,
    ) -> Optional[ChartinkSignal]:
        """
        Evaluates a stock against the Chartink Intraday Screener formula.
        df_daily: historical daily bars sorted by timestamp ascending.
        today_override: optional dict containing today's latest live candle (open, high, low, close, volume).
        """
        if df_daily is None or len(df_daily) < 15:
            return None

        df = df_daily.copy()
        df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_localize(None)
        df = df.sort_values("timestamp").reset_index(drop=True)

        if target_date is not None:
            active_date = target_date
        else:
            try:
                import config
                import pytz
                active_date = datetime.now(pytz.timezone(config.MARKET_TIMEZONE)).date()
            except Exception:
                active_date = date.today()

        # If no live today_override is given, verify that df_daily actually has today's bar
        if today_override is None:
            last_date = df["timestamp"].iloc[-1].date()
            if last_date != active_date:
                # df ends on yesterday or past trading day; do not evaluate past day as today!
                return None

        # Merge today's live session if provided
        if today_override:
            t_dt = pd.to_datetime(today_override.get("timestamp", datetime.now()))
            if hasattr(t_dt, "tz") and t_dt.tz is not None:
                t_dt = t_dt.tz_localize(None)
            if t_dt.date() != active_date:
                return None
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
            df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_localize(None)

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
        ts_today = pd.to_datetime(today["timestamp"])
        cur_month = ts_today.month
        cur_year = ts_today.year
        # Mask for previous calendar month
        prev_month = cur_month - 1 if cur_month > 1 else 12
        prev_year = cur_year if cur_month > 1 else cur_year - 1

        ts_series = pd.to_datetime(df["timestamp"])
        pm_df = df[(ts_series.dt.month == prev_month) & (ts_series.dt.year == prev_year)]
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
            confluences.append(f"Monthly Breakout (Close Rs.{c_close:.2f} >= Prev Month High Rs.{prev_month_high:.2f})")
            confluences.append(f"Institutional Turnover: Rs.{turnover_in_cr:.1f} Cr >= Rs.10 Cr")

        # ═══════════════════════════════════════════════════════════════════
        # 3. Sub-Strategy 2: 20-Week Multi-Month Breakout & 200 SMA
        # - Weekly Close > 1 Week Ago Max(20, Weekly Close)
        # - Daily Close > Daily SMA(Close, 200)
        # ═══════════════════════════════════════════════════════════════════
        try:
            df_for_weekly = df.copy()
            df_for_weekly["timestamp"] = pd.to_datetime(df_for_weekly["timestamp"])
            df_weekly = df_for_weekly.set_index("timestamp").resample("W-FRI").agg({
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
            confluences.append(f"20-Week High Breakout (Rs.{c_close:.2f} > Rs.{max_20w_close:.2f})")
            confluences.append(f"Above 200 SMA (Rs.{c_close:.2f} > Rs.{sma_200:.2f})")

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

        calc_date_str = str(target_date) if target_date else (
            t_val.strftime("%Y-%m-%d") if isinstance(t_val, (pd.Timestamp, datetime))
            else datetime.now().strftime("%Y-%m-%d")
        )

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
            first_detected_time=ts_str,
            first_detected_price=c_close,
            date=calc_date_str,
        )

    def find_first_detection(
        self,
        symbol: str,
        df_daily: pd.DataFrame,
        df_today_candles: pd.DataFrame,
        fut_symbol: str = "",
        lot_size: int = 0,
        turnover_cr: float = 0.0,
        liquidity_tier: str = "Normal",
        is_most_liquid: bool = False,
        target_date: Optional[date] = None,
    ) -> Optional[ChartinkSignal]:
        """
        Evaluates today's intraday candles (1m or 5m) chronologically to find the
        EXACT first candle where Chartink breakout conditions were triggered.
        Guarantees that the detected timestamp is the actual historical breakout candle time,
        completely solving repainting.
        """
        if df_today_candles is None or df_today_candles.empty:
            return None

        # 1. Quick check: does the full day's aggregate candle trigger the formula?
        c_open = float(df_today_candles.iloc[0]["open"])
        c_high = float(df_today_candles["high"].max())
        c_low = float(df_today_candles["low"].min())
        c_close = float(df_today_candles.iloc[-1]["close"])
        c_vol = int(df_today_candles["volume"].sum())
        last_ts = df_today_candles.iloc[-1]["timestamp"]

        full_day_override = {
            "timestamp": last_ts,
            "open": c_open,
            "high": c_high,
            "low": c_low,
            "close": c_close,
            "volume": c_vol,
        }
        final_sig = self.evaluate_stock(
            symbol=symbol,
            df_daily=df_daily,
            today_override=full_day_override,
            fut_symbol=fut_symbol,
            lot_size=lot_size,
            turnover_cr=turnover_cr,
            liquidity_tier=liquidity_tier,
            is_most_liquid=is_most_liquid,
            target_date=target_date,
        )
        if not final_sig:
            return None

        # 2. It triggered today! Now find the earliest candle that triggered
        first_time_str = None
        first_px = None
        for i in range(1, len(df_today_candles) + 1):
            sub = df_today_candles.iloc[:i]
            bar_ts = sub.iloc[-1]["timestamp"]
            sub_override = {
                "timestamp": bar_ts,
                "open": float(sub.iloc[0]["open"]),
                "high": float(sub["high"].max()),
                "low": float(sub["low"].min()),
                "close": float(sub.iloc[-1]["close"]),
                "volume": int(sub["volume"].sum()),
            }
            cand_sig = self.evaluate_stock(
                symbol=symbol,
                df_daily=df_daily,
                today_override=sub_override,
                target_date=target_date,
            )
            if cand_sig:
                dt_obj = pd.to_datetime(bar_ts)
                first_time_str = dt_obj.strftime("%H:%M:%S")
                first_px = float(sub.iloc[-1]["close"])
                break

        if first_time_str:
            final_sig.timestamp = first_time_str
            final_sig.first_detected_time = first_time_str
            final_sig.first_detected_price = first_px or c_close

        final_sig.date = str(target_date) if target_date else datetime.now().strftime("%Y-%m-%d")

        return final_sig
