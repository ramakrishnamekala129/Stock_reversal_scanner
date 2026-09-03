"""
Multi-Factor Signal Scoring & Detection Engine.
Combines 5M Candlestick Patterns, Pivot/PDH/PDL Levels, and Volume Confirmation.
"""

from dataclasses import dataclass, field
from datetime import datetime
import logging
from typing import Any, Dict, List, Optional
import pandas as pd

import config
from indicators.pivots import DailyPivots, evaluate_pivot_relationship, PivotRelationship, get_pivot_zone
from indicators.volume import calculate_volume_metrics, VolumeMetrics
from patterns.candlestick import detect_candlestick_patterns, PatternResult
from scanner.context import determine_market_context

logger = logging.getLogger(__name__)


@dataclass
class SignalEvent:
    """Structure representing a validated actionable scanner signal."""
    symbol: str
    timestamp: datetime
    pattern: str
    direction: str  # BULLISH SETUP, BEARISH WARNING, etc.
    price: float
    score: int
    score_breakdown: List[str]

    # Daily Levels
    pdo: float
    pdh: float
    pdl: float
    pdc: float
    pdv: int

    pivot: float
    r1: float
    r2: float
    r3: float
    s1: float
    s2: float
    s3: float

    # Confirmation
    relative_volume: float
    volume: int
    conditions_met: List[str]
    zone: str = ""
    timeframe: str = "5m"

    # Setup Candle Extremes & Next-Candle Trigger Tracking
    candle_high: float = 0.0
    candle_low: float = 0.0
    trigger_status: str = "PENDING"  # PENDING, TRIGGERED, INVALIDATED
    trigger_time: str = ""
    trigger_price: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat() if isinstance(self.timestamp, datetime) else str(self.timestamp),
            "pattern": self.pattern,
            "direction": self.direction,
            "price": self.price,
            "score": self.score,
            "score_breakdown": self.score_breakdown,
            "zone": self.zone,
            "timeframe": self.timeframe,
            "pivot": self.pivot,
            "pp": self.pivot,
            "pdo": self.pdo,
            "pdh": self.pdh,
            "pdl": self.pdl,
            "pdc": self.pdc,
            "pdv": self.pdv,
            "r1": self.r1,
            "r2": self.r2,
            "r3": self.r3,
            "s1": self.s1,
            "s2": self.s2,
            "s3": self.s3,
            "relative_volume": self.relative_volume,
            "volume": self.volume,
            "conditions_met": self.conditions_met,
            "candle_high": self.candle_high,
            "candle_low": self.candle_low,
            "trigger_status": self.trigger_status,
            "trigger_time": self.trigger_time,
            "trigger_price": self.trigger_price,
        }


class SignalEngine:
    """Evaluates multi-factor score on closed candles (3m, 5m, 15m)."""

    def __init__(
        self,
        min_signal_score: int = config.MIN_SIGNAL_SCORE,
        score_weights: Optional[Dict[str, int]] = None,
    ):
        self.min_signal_score = min_signal_score
        self.weights = score_weights or config.SCORE_WEIGHTS

    def evaluate_candle(
        self,
        symbol: str,
        df_candles: pd.DataFrame,
        pivots: DailyPivots,
        timeframe: str = "5m",
    ) -> List[SignalEvent]:
        """
        Evaluates a newly closed candle (3m, 5m, 15m) for symbol against all rules, patterns, pivots, and volume.
        Returns a list of SignalEvent if criteria are met.
        """
        if df_candles.empty or len(df_candles) < 2:
            return []

        curr_candle = df_candles.iloc[-1]
        prev_candle = df_candles.iloc[-2]

        curr_close = float(curr_candle["close"])
        curr_open = float(curr_candle["open"])
        curr_high = float(curr_candle["high"])
        curr_low = float(curr_candle["low"])
        curr_vol = int(curr_candle["volume"])
        ts = curr_candle["timestamp"]

        # 1. Market context
        context = determine_market_context(df_candles)

        # 2. Candlestick patterns
        patterns_data = detect_candlestick_patterns(df_candles, context=context)
        detected_details: List[PatternResult] = patterns_data.get("details", [])

        if not detected_details:
            return []

        # 3. Pivot & PDH/PDL relationships
        prev_close = float(prev_candle["close"])
        pivot_rel: PivotRelationship = evaluate_pivot_relationship(
            curr_close=curr_close,
            curr_open=curr_open,
            curr_high=curr_high,
            curr_low=curr_low,
            pivots=pivots,
            prev_close=prev_close,
        )

        # 4. Volume metrics
        vol_metrics = calculate_volume_metrics(df_candles)
        rel_vol = vol_metrics.relative_volume if vol_metrics else 1.0

        signals: List[SignalEvent] = []

        for pat in detected_details:
            score = 0
            score_breakdown: List[str] = []
            conditions_met: List[str] = []
            is_bearish = (pat.pattern_direction == "BEARISH" or pat.pattern_name in ["HANGING MAN", "BEARISH ENGULFING", "BEARISH HARAMI", "SHOOTING STAR", "INVERSE HAMMER", "BEARISH MARUBOZU"])

            # --- Directional Consistency with CPR Breakout/Breakdown & Major Levels ---
            # A candle closing in CPR Breakdown CANNOT be a Bullish Setup
            if not is_bearish and pivot_rel.cpr_breakdown:
                continue

            # A candle closing in CPR Breakout CANNOT be a Bearish Warning
            if is_bearish and pivot_rel.cpr_breakout:
                continue

            # A stock breaking out strongly above PDH and R1 CANNOT be a Bearish Warning UNLESS it is an R2 Resistance Rejection
            is_near_r2_rejection = (
                abs(curr_high - pivots.r2) / pivots.r2 <= 0.0035
                or (curr_high >= pivots.r2 and curr_close <= pivots.r2)
            ) and ((curr_high - curr_close) / max(curr_high - curr_low, 0.001) >= 0.35)

            if is_bearish and curr_close > pivots.pdh and curr_close > pivots.r1 and not is_near_r2_rejection:
                continue

            # A stock breaking down below PDL and S1 CANNOT be a Bullish Setup UNLESS it is an S2 Support Bounce
            is_near_s2_bounce = (
                abs(curr_low - pivots.s2) / pivots.s2 <= 0.0035
                or (curr_low <= pivots.s2 and curr_close >= pivots.s2)
            ) and ((curr_close - curr_low) / max(curr_high - curr_low, 0.001) >= 0.35)

            if not is_bearish and curr_close < pivots.pdl and curr_close < pivots.s1 and not is_near_s2_bounce:
                continue

            # Suppress low-volume Harami inside-bar chop (Rel Vol < 0.75x)
            # Harami is an inside consolidation candle; on low volume it is pure noise
            if pat.pattern_name in ["BULLISH HARAMI", "BEARISH HARAMI"] and rel_vol < 0.75:
                continue

            # Invalidate Marubozu on dead/sub-average volume (< 1.0x) or tiny micro-ticks (< 0.15% range)
            if pat.pattern_name in ["BULLISH MARUBOZU", "BEARISH MARUBOZU"]:
                candle_range_pct = (curr_high - curr_low) / max(curr_close, 1.0) * 100.0
                if candle_range_pct < 0.15 or rel_vol < 1.0:
                    continue

            # Invalidate Shooting Star or Hanging Man occurring at support (S1 / PDL)
            # Bearish exhaustion wicks at major support are not valid resistance rejections
            if pat.pattern_name in ["SHOOTING STAR", "HANGING MAN"] and curr_close <= pivots.bear_trap_top:
                continue

            # Invalidate Hammer occurring at resistance (R1 / PDH)
            # Bullish bounce wicks at major resistance are not valid support bounces
            if pat.pattern_name == "HAMMER" and curr_close >= pivots.bull_trap_bottom:
                continue

            # --- Pattern Score ---
            pat_name = pat.pattern_name.replace(" ", "_")
            pat_weight = abs(self.weights.get(pat_name, 2))
            score += pat_weight
            score_breakdown.append(f"{pat.pattern_name} (+{pat_weight})")

            # --- Pivot & Level Context Score ---
            if is_bearish:
                # Bearish Context Points
                if pivot_rel.below_pivot or curr_close < pivots.pp:
                    w = 1
                    score += w
                    score_breakdown.append(f"Price < Pivot (+{w})")
                    conditions_met.append("Price < Pivot")

                if pivot_rel.below_pdl or curr_close < pivots.pdl:
                    w = 2
                    score += w
                    score_breakdown.append(f"Price < PDL (+{w})")
                    conditions_met.append("Price < PDL")

                if pivot_rel.crossed_s1 or curr_close < pivots.s1:
                    w = 2
                    score += w
                    score_breakdown.append(f"Price < S1 (+{w})")
                    conditions_met.append("Price < S1")

                # Near R1 Resistance Rejection
                if abs(curr_high - pivots.r1) / pivots.r1 < 0.004 and curr_close < pivots.r1:
                    w = 2
                    score += w
                    score_breakdown.append(f"Near R1 Resistance Rejection (+{w})")
                    conditions_met.append("Rejection near R1 Resistance")

                # Near R2 Resistance Rejection (Ceiling Resistance Reversal / Overbought HOD)
                if is_near_r2_rejection:
                    w = 3
                    score += w
                    score_breakdown.append(f"Near R2 Resistance Rejection (+{w})")
                    conditions_met.append("🛡️ Rejection near R2 Resistance")
            else:
                # Bullish Context Points
                if pivot_rel.above_pivot or curr_close > pivots.pp:
                    w = 1
                    score += w
                    score_breakdown.append(f"Price > Pivot (+{w})")
                    conditions_met.append("Price > Pivot")

                if pivot_rel.above_pdh or curr_close > pivots.pdh:
                    w = 2
                    score += w
                    score_breakdown.append(f"Price > PDH (+{w})")
                    conditions_met.append("Price > PDH")

                if pivot_rel.crossed_r1 or curr_close > pivots.r1:
                    w = 2
                    score += w
                    score_breakdown.append(f"Price > R1 (+{w})")
                    conditions_met.append("Price > R1")

                # Near S1 Support Bounce
                if abs(curr_low - pivots.s1) / pivots.s1 < 0.004 and curr_close > pivots.s1:
                    # Require candle to have a meaningful bounce off the low
                    if (curr_close - curr_low) / max(curr_high - curr_low, 0.001) >= 0.25:
                        w = 2
                        score += w
                        score_breakdown.append(f"Near S1 Support Bounce (+{w})")
                        conditions_met.append("Bounce near S1 Support")

                # Near S2 Support Bounce (Floor Support Reversal / Oversold LOD)
                if is_near_s2_bounce:
                    w = 3
                    score += w
                    score_breakdown.append(f"Near S2 Support Bounce (+{w})")
                    conditions_met.append("🛡️ Bounce near S2 Support")

            # --- Trap Zone Confluences (Must be <= 0.20% of price) ---
            # 1. Bear Trap Zone (S1 & PDL Support Zone - Narrow <= 0.20%)
            if pivots.is_narrow_bear_trap:
                # A. Bear Trap Breakout: Candle tested or originated from trap and closed ABOVE trap top (PDL)
                if not is_bearish and curr_close > pivots.bear_trap_top and (curr_low <= pivots.bear_trap_top or curr_open <= pivots.bear_trap_top):
                    w = 4
                    score += w
                    score_breakdown.append(f"Bear Trap Breakout above PDL ({pivots.bear_trap_width_pct:.2f}% <= 0.2%) (+{w})")
                    conditions_met.append(f"⚡ Narrow Bear Trap ({pivots.bear_trap_width_pct:.2f}%)")
                    conditions_met.append("🚀 Bear Trap Breakout (Above S1/PDL)")
                else:
                    # B. Bear Trap Reversal: Candle touches S1-PDL range and holds support
                    candle_touches_bear_trap = (curr_low <= pivots.bear_trap_top and curr_high >= pivots.bear_trap_bottom)
                    if candle_touches_bear_trap and not is_bearish:
                        # Valid Bear Trap Reversal: Candle must hold support above trap bottom and not be a CPR breakdown
                        if curr_close >= pivots.bear_trap_bottom and not pivot_rel.cpr_breakdown:
                            w = 4
                            score += w
                            score_breakdown.append(f"Bear Trap S1-PDL Touch & Bounce ({pivots.bear_trap_width_pct:.2f}% <= 0.2%) (+{w})")
                            conditions_met.append(f"⚡ Narrow Bear Trap ({pivots.bear_trap_width_pct:.2f}%)")
                            conditions_met.append("🪤 Bear Trap Reversal (S1-PDL)")

            # 2. Bull Trap Zone (R1 & PDH Resistance Zone - Narrow <= 0.20%)
            if pivots.is_narrow_bull_trap:
                # A. Bull Trap Breakdown: Candle tested or originated from trap and closed BELOW trap bottom (R1)
                if is_bearish and curr_close < pivots.bull_trap_bottom and (curr_high >= pivots.bull_trap_bottom or curr_open >= pivots.bull_trap_bottom):
                    w = 4
                    score += w
                    score_breakdown.append(f"Bull Trap Breakdown below R1 ({pivots.bull_trap_width_pct:.2f}% <= 0.2%) (+{w})")
                    conditions_met.append(f"⚡ Narrow Bull Trap ({pivots.bull_trap_width_pct:.2f}%)")
                    conditions_met.append("💥 Bull Trap Breakdown (Below R1/PDH)")
                else:
                    # B. Bull Trap Rejection: Candle touches R1-PDH range and holds resistance
                    candle_touches_bull_trap = (curr_high >= pivots.bull_trap_bottom and curr_low <= pivots.bull_trap_top)
                    if candle_touches_bull_trap and is_bearish:
                        # Valid Bull Trap Rejection: Candle must hold resistance below trap top and not be a CPR breakout
                        if curr_close <= pivots.bull_trap_top and not pivot_rel.cpr_breakout:
                            w = 4
                            score += w
                            score_breakdown.append(f"Bull Trap R1-PDH Touch & Rejection ({pivots.bull_trap_width_pct:.2f}% <= 0.2%) (+{w})")
                            conditions_met.append(f"⚡ Narrow Bull Trap ({pivots.bull_trap_width_pct:.2f}%)")
                            conditions_met.append("🪤 Bull Trap Rejection (R1-PDH)")

            # 3. CPR Breakout / Breakdown (Decisive close with >=60% of candle)
            if pivot_rel.cpr_breakout and not is_bearish:
                w = 3
                score += w
                score_breakdown.append(f"CPR Breakout (>=60% candle above CPR) (+{w})")
                if pivots.is_narrow_cpr:
                    conditions_met.append(f"🚀 Narrow CPR Breakout ({pivots.cpr_width_pct:.2f}%)")
                else:
                    conditions_met.append("🚀 CPR Breakout (Bullish Close)")

            if pivot_rel.cpr_breakdown and is_bearish:
                w = 3
                score += w
                score_breakdown.append(f"CPR Breakdown (>=60% candle below CPR) (+{w})")
                if pivots.is_narrow_cpr:
                    conditions_met.append(f"💥 Narrow CPR Breakdown ({pivots.cpr_width_pct:.2f}%)")
                else:
                    conditions_met.append("💥 CPR Breakdown (Bearish Close)")

            # 4. Candlestick Pattern at CPR Confluence (Reversal / Bounce / Rejection)
            candle_touches_cpr = (curr_low <= pivots.cpr_top and curr_high >= pivots.cpr_bottom)
            if candle_touches_cpr and not pivot_rel.cpr_breakout and not pivot_rel.cpr_breakdown:
                w = 3
                score += w
                if not is_bearish:
                    if pivots.is_narrow_cpr:
                        score_breakdown.append(f"{pat.pattern_name} at Narrow CPR Support (+{w})")
                        conditions_met.append(f"🎯 {pat.pattern_name} at Narrow CPR Support (<0.2%)")
                    else:
                        score_breakdown.append(f"{pat.pattern_name} at CPR Support Bounce (+{w})")
                        conditions_met.append(f"🎯 {pat.pattern_name} at CPR Support")
                else:
                    if pivots.is_narrow_cpr:
                        score_breakdown.append(f"{pat.pattern_name} at Narrow CPR Resistance (+{w})")
                        conditions_met.append(f"🎯 {pat.pattern_name} at Narrow CPR Resistance (<0.2%)")
                    else:
                        score_breakdown.append(f"{pat.pattern_name} at CPR Resistance Rejection (+{w})")
                        conditions_met.append(f"🎯 {pat.pattern_name} at CPR Resistance")

            # 5. Narrow CPR Trending Day Candidate (< 0.20% width)
            if pivots.is_narrow_cpr:
                w = 2
                score += w
                score_breakdown.append(f"Narrow CPR ({pivots.cpr_width_pct}% <= 0.2%) (+{w})")
                conditions_met.append(f"⚡ Narrow CPR ({pivots.cpr_width_pct}%)")

            # --- Volume Confirmation Score ---
            if vol_metrics and vol_metrics.is_high_volume:
                w = 1
                score += w
                score_breakdown.append(f"Relative Vol {rel_vol}x > {config.MIN_RELATIVE_VOLUME}x (+{w})")
                conditions_met.append(f"Volume Surge ({rel_vol}x)")

            # Direction classification & Actionability
            direction = "BEARISH WARNING" if is_bearish else "BULLISH SETUP"
            is_actionable = score >= self.min_signal_score

            if is_actionable:
                pivot_zone = get_pivot_zone(curr_close, pivots, low=curr_low, high=curr_high, open_p=curr_open)
                signal_event = SignalEvent(
                    symbol=symbol,
                    timestamp=ts,
                    pattern=pat.pattern_name,
                    direction=direction,
                    price=curr_close,
                    score=score,
                    score_breakdown=score_breakdown,
                    zone=pivot_zone,
                    timeframe=timeframe,
                    pdo=pivots.pdo,
                    pdh=pivots.pdh,
                    pdl=pivots.pdl,
                    pdc=pivots.pdc,
                    pdv=pivots.pdv,
                    pivot=pivots.pp,
                    r1=pivots.r1,
                    r2=pivots.r2,
                    r3=pivots.r3,
                    s1=pivots.s1,
                    s2=pivots.s2,
                    s3=pivots.s3,
                    relative_volume=rel_vol,
                    volume=curr_vol,
                    conditions_met=conditions_met,
                    candle_high=curr_high,
                    candle_low=curr_low,
                    trigger_status="PENDING",
                    trigger_time="",
                    trigger_price=curr_high if not is_bearish else curr_low,
                )
                signals.append(signal_event)

        if not signals:
            return []

        # --- 6. Intelligent Signal Deduplication & Conflict Resolution ---
        bullish_signals = [s for s in signals if "BULLISH" in s.direction]
        bearish_signals = [s for s in signals if "BEARISH" in s.direction]

        if bullish_signals and bearish_signals:
            # Both Bullish & Bearish fired on the same candle!
            best_bull = max(bullish_signals, key=lambda s: s.score)
            best_bear = max(bearish_signals, key=lambda s: s.score)

            # If one clearly dominates (score diff >= 2), pick the dominant setup
            if best_bull.score >= best_bear.score + 2:
                return [best_bull]
            elif best_bear.score >= best_bull.score + 2:
                return [best_bear]
            else:
                # If scores are close (e.g. 5 vs 5 or 4 vs 5):
                # In low volume (< 1.0x), this is pure midday indecision/chop: suppress conflicting alerts!
                if rel_vol < 1.0:
                    logger.debug(f"Suppressed conflicting low-volume signal for {symbol} (Bull: {best_bull.pattern} vs Bear: {best_bear.pattern} with {rel_vol:.2f}x vol)")
                    return []
                # In high volume (>= 1.0x), break tie using candle color
                return [best_bull] if curr_close >= curr_open else [best_bear]
        elif len(signals) > 1:
            # If multiple patterns in the same direction, pick the highest scoring pattern
            best_sig = max(signals, key=lambda s: s.score)
            return [best_sig]

        return signals
