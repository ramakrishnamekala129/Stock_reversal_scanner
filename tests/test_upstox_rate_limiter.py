from market.rate_limiter import AsyncUpstoxRateLimiter


def test_retry_after_is_shared_across_limiter_instances(monkeypatch):
    AsyncUpstoxRateLimiter.reset_for_tests()
    monkeypatch.setattr("market.rate_limiter.time.monotonic", lambda: 1000.0)

    first = AsyncUpstoxRateLimiter(25)
    second = AsyncUpstoxRateLimiter(12)
    seconds, announced = first.defer(364)
    _, duplicate_announcement = second.defer(364)

    assert seconds == 364
    assert announced is True
    assert duplicate_announcement is False
    assert AsyncUpstoxRateLimiter._cooldown_until == 1364.0


def test_long_window_quota_sets_safe_minimum_interval():
    limiter = AsyncUpstoxRateLimiter(50)
    assert limiter.min_interval >= 0.95
