"""Process-wide pacing and Retry-After coordination for Upstox APIs."""

import asyncio
import threading
import time
from typing import Tuple

import config


class AsyncUpstoxRateLimiter:
    """Coordinate Upstox requests across workers and independent event loops."""

    _state_lock = threading.Lock()
    _next_request_at = 0.0
    _cooldown_until = 0.0

    def __init__(self, rate_per_sec: float = 25.0, burst_capacity: float = 25.0):
        requested_interval = 1.0 / max(float(rate_per_sec), 0.01)
        self.min_interval = max(
            requested_interval,
            float(getattr(config, "UPSTOX_MIN_REQUEST_INTERVAL_SECONDS", 0.95)),
        )

    async def acquire(self):
        while True:
            with self._state_lock:
                now = time.monotonic()
                wait_for = max(
                    type(self)._cooldown_until - now,
                    type(self)._next_request_at - now,
                )
                if wait_for <= 0:
                    type(self)._next_request_at = now + self.min_interval
                    return
            # Periodic wakeups keep cancellation responsive during long bans.
            await asyncio.sleep(min(wait_for, 30.0))

    def acquire_sync(self):
        """Synchronous counterpart used by SDK/requests based clients."""
        while True:
            with self._state_lock:
                now = time.monotonic()
                wait_for = max(
                    type(self)._cooldown_until - now,
                    type(self)._next_request_at - now,
                )
                if wait_for <= 0:
                    type(self)._next_request_at = now + self.min_interval
                    return
            time.sleep(min(wait_for, 30.0))

    def defer(self, retry_after_seconds: float) -> Tuple[float, bool]:
        """Apply Retry-After globally; return (seconds, newly_announced)."""
        seconds = min(
            max(float(retry_after_seconds), 1.0),
            float(getattr(config, "UPSTOX_MAX_RETRY_AFTER_SECONDS", 900.0)),
        )
        with self._state_lock:
            now = time.monotonic()
            requested_until = now + seconds
            announced = requested_until > type(self)._cooldown_until + 1.0
            type(self)._cooldown_until = max(type(self)._cooldown_until, requested_until)
        return seconds, announced

    @classmethod
    def reset_for_tests(cls):
        with cls._state_lock:
            cls._next_request_at = 0.0
            cls._cooldown_until = 0.0
