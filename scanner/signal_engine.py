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
        }


class SignalEngine:
    """Evaluates multi-factor score on closed 5-minute candles."""

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
    ) -> List[SignalEvent]:
        """
        Evaluates a newly closed 5M candle for symbol against all rules, patterns, pivots, and volume.
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

            # --- Pattern Score ---
            pat_name = pat.pattern_name.replace(" ", "_")
            pat_weight = self.weights.get(pat_name, 2)
            score += pat_weight
            score_breakdown.append(f"{pat.pattern_name} ({pat_weight:+d})")

            # --- Pivot & Level Context Score ---
            if pivot_rel.above_pivot:
                w = self.weights.get("CLOSE_ABOVE_PIVOT", 1)
                score += w
                score_breakdown.append(f"Price > Pivot ({w:+d})")
                conditions_met.append("Price > Pivot")
            elif pivot_rel.below_pivot:
                w = self.weights.get("CLOSE_BELOW_PIVOT", -1)
                score += w
                score_breakdown.append(f"Price < Pivot ({w:+d})")
                conditions_met.append("Price < Pivot")

            if pivot_rel.above_pdh:
                w = self.weights.get("BREAK_PDH", 2)
                score += w
                score_breakdown.append(f"Price > PDH ({w:+d})")
                conditions_met.append("Price > PDH")
            elif pivot_rel.below_pdl:
                w = self.weights.get("BREAK_PDL", -2)
                score += w
                score_breakdown.append(f"Price < PDL ({w:+d})")
                conditions_met.append("Price < PDL")

            if pivot_rel.crossed_r1 or curr_close > pivots.r1:
                w = self.weights.get("BREAK_R1", 2)
                score += w
                score_breakdown.append(f"Price > R1 ({w:+d})")
                conditions_met.append("Price > R1")

            if pivot_rel.crossed_s1 or curr_close < pivots.s1:
                w = self.weights.get("BREAK_S1", -2)
                score += w
                score_breakdown.append(f"Price < S1 ({w:+d})")
                conditions_met.append("Price < S1")

            # --- Trap Zone Confluences ---
            # 1. Bear Trap Zone (S1 & PDL Support Zone): Bullish reversals here trap short sellers!
            if pivots.bear_trap_bottom <= curr_close <= pivots.bear_trap_top:
                if pat.pattern_direction == "BULLISH" or pat.pattern_name in ["HAMMER", "BULLISH ENGULFING", "INVERSE HAMMER", "BULLISH HARAMI"]:
                    w = 3
                    score += w
                    score_breakdown.append(f"Bear Trap S1-PDL Support Reversal ({w:+d})")
                    conditions_met.append("🪤 Bear Trap Reversal (S1-PDL)")

            # 2. Bull Trap Zone (R1 & PDH Resistance Zone): Bearish reversals here trap breakout buyers!
            if pivots.bull_trap_bottom <= curr_close <= pivots.bull_trap_top:
                if pat.pattern_direction == "BEARISH" or pat.pattern_name == "HANGING MAN":
                    w = -3
                    score += w
                    score_breakdown.append(f"Bull Trap R1-PDH Resistance Rejection ({w:+d})")
                    conditions_met.append("🪤 Bull Trap Rejection (R1-PDH)")

            # 3. CPR (Central Pivot Range) Confluence
            if pivots.cpr_bottom <= curr_close <= pivots.cpr_top:
                conditions_met.append("🎯 Inside CPR Zone")

            # Near support bounce check for Hammer / Reversals
            if abs(curr_low - pivots.s1) / pivots.s1 < 0.003 and curr_close > pivots.s1:
                w = self.weights.get("NEAR_S1_S2_SUPPORT", 2)
                score += w
                score_breakdown.append(f"Near S1 Support Bounce ({w:+d})")
                conditions_met.append("Bounce near S1 Support")

            # --- Volume Confirmation Score ---
            if vol_metrics and vol_metrics.is_high_volume:
                w = self.weights.get("HIGH_RELATIVE_VOLUME", 1)
                score += w
                score_breakdown.append(f"Relative Vol {rel_vol}x > {config.MIN_RELATIVE_VOLUME}x ({w:+d})")
                conditions_met.append(f"Volume Surge ({rel_vol}x)")

            # Direction classification
            if pat.pattern_direction == "BEARISH" or pat.pattern_name == "HANGING MAN":
                direction = "BEARISH WARNING"
                # For bearish warnings, check if absolute negative score reaches threshold
                is_actionable = abs(score) >= (self.min_signal_score - 1)
            else:
                direction = "BULLISH SETUP"
                is_actionable = score >= self.min_signal_score

            if is_actionable:
                pivot_zone = get_pivot_zone(curr_close, pivots)
                signal_event = SignalEvent(
                    symbol=symbol,
                    timestamp=ts,
                    pattern=pat.pattern_name,
                    direction=direction,
                    price=curr_close,
                    score=score,
                    score_breakdown=score_breakdown,
                    zone=pivot_zone,
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
                )
                signals.append(signal_event)

        return signals
