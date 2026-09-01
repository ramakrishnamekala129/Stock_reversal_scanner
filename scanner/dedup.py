"""
Event Deduplication Module.
Prevents duplicate alerts for the same candle timestamp and pattern.
"""

from datetime import datetime
from typing import Any, Set, Tuple


class EventDeduplicator:
    """Tracks seen (symbol, timestamp, pattern_name) triples."""

    def __init__(self):
        self._seen_events: Set[Tuple[str, str, str]] = set()

    def is_duplicate(self, symbol: str, timestamp: Any, pattern_name: str) -> bool:
        """
        Checks whether the event key has already been registered.
        Returns True if duplicate, False if new.
        """
        ts_str = timestamp.isoformat() if isinstance(timestamp, datetime) else str(timestamp)
        key = (symbol, ts_str, pattern_name)
        if key in self._seen_events:
            return True
        return False

    def mark_seen(self, symbol: str, timestamp: Any, pattern_name: str):
        """Registers the event key as seen."""
        ts_str = timestamp.isoformat() if isinstance(timestamp, datetime) else str(timestamp)
        key = (symbol, ts_str, pattern_name)
        self._seen_events.add(key)

    def reset(self):
        """Clears seen events cache (e.g. at market open)."""
        self._seen_events.clear()

    @property
    def total_events(self) -> int:
        return len(self._seen_events)
