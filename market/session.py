"""
NSE Market Session Manager.
Tracks market session status (09:15 to 15:30 IST), open/close transitions,
and session statistics.
"""

from dataclasses import dataclass, field
from datetime import datetime, time
import logging
from typing import Dict
import pytz

import config

logger = logging.getLogger(__name__)


@dataclass
class SessionStats:
    """Session operational and scanning metrics."""
    symbols_scanned: int = 0
    candles_processed: int = 0
    patterns_detected: int = 0
    bullish_signals: int = 0
    bearish_signals: int = 0
    hanging_man_signals: int = 0
    websocket_errors: int = 0
    missing_candles: int = 0
    pattern_breakdown: Dict[str, int] = field(default_factory=dict)

    def print_summary(self):
        """Prints standard session summary box to console."""
        print("\n" + "=" * 60)
        print("SESSION SUMMARY")
        print("=" * 60)
        print(f"F&O Symbols Scanned : {self.symbols_scanned}")
        print(f"5M Candles Processed: {self.candles_processed}")
        print(f"Patterns Detected   : {self.patterns_detected}")
        print(f"Bullish Signals     : {self.bullish_signals}")
        print(f"Hanging Man Signals : {self.hanging_man_signals}")
        print(f"WebSocket Errors    : {self.websocket_errors}")
        print(f"Missing Candles     : {self.missing_candles}")
        if self.pattern_breakdown:
            print("-" * 60)
            print("Pattern Breakdown:")
            for pat, cnt in sorted(self.pattern_breakdown.items()):
                print(f"  - {pat:<20}: {cnt}")
        print("=" * 60 + "\n")


class MarketSessionManager:
    """Manages NSE trading session state and timing checks."""

    def __init__(self):
        self.tz = pytz.timezone(config.MARKET_TIMEZONE)
        self.open_time = time.fromisoformat(config.MARKET_OPEN)
        self.close_time = time.fromisoformat(config.MARKET_CLOSE)
        self.stats = SessionStats()

    def get_current_ist_time(self) -> datetime:
        """Returns current timestamp in Asia/Kolkata timezone."""
        return datetime.now(self.tz)

    def is_market_open(self, dt: datetime = None) -> bool:
        """Checks whether the given or current time is within active market hours."""
        if dt is None:
            dt = self.get_current_ist_time()
        else:
            if dt.tzinfo is None:
                dt = self.tz.localize(dt)
            else:
                dt = dt.astimezone(self.tz)

        # NSE trades Monday (0) through Friday (4)
        if dt.weekday() >= 5:
            return False

        current_t = dt.time()
        return self.open_time <= current_t <= self.close_time

    def is_market_closed(self, dt: datetime = None) -> bool:
        """Checks if current time is past market close (15:30)."""
        if dt is None:
            dt = self.get_current_ist_time()
        else:
            if dt.tzinfo is None:
                dt = self.tz.localize(dt)
            else:
                dt = dt.astimezone(self.tz)

        return dt.time() > self.close_time or dt.weekday() >= 5

    def reset_daily_state(self):
        """Resets scanner counters and statistics for a new trading day."""
        self.stats = SessionStats()
        logger.info("Market session state reset for new trading session.")
