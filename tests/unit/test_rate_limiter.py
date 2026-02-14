"""Unit tests for the rate limiter module."""

from __future__ import annotations

import asyncio
import time

import pytest

from src.core.rate_limiter import RateLimiter


class TestRateLimiter:
    """Tests for RateLimiter token bucket implementation."""

    def test_init_defaults(self):
        limiter = RateLimiter()
        assert limiter.remaining == 55
        assert limiter.current_usage == 0

    def test_init_custom(self):
        limiter = RateLimiter(max_requests=10, window_seconds=30.0)
        assert limiter.remaining == 10

    @pytest.mark.asyncio
    async def test_acquire_basic(self):
        limiter = RateLimiter(max_requests=5, window_seconds=60.0)
        await limiter.acquire()
        assert limiter.current_usage == 1
        assert limiter.remaining == 4

    @pytest.mark.asyncio
    async def test_acquire_multiple(self):
        limiter = RateLimiter(max_requests=5, window_seconds=60.0)
        for _ in range(5):
            await limiter.acquire()
        assert limiter.current_usage == 5
        assert limiter.remaining == 0

    @pytest.mark.asyncio
    async def test_record_success_resets_backoff(self):
        limiter = RateLimiter(max_requests=10, window_seconds=60.0)
        limiter.record_rate_limit()
        assert limiter._consecutive_rate_limits == 1
        # H-17: requires 3 consecutive successes to reset (not 1)
        limiter.record_success()
        assert limiter._consecutive_rate_limits == 1  # Not yet reset
        limiter.record_success()
        assert limiter._consecutive_rate_limits == 1  # Still not reset
        limiter.record_success()
        assert limiter._consecutive_rate_limits == 0  # Reset after 3 consecutive successes

    def test_record_rate_limit_increments(self):
        limiter = RateLimiter(max_requests=10, window_seconds=60.0)
        limiter.record_rate_limit()
        assert limiter._consecutive_rate_limits == 1
        limiter.record_rate_limit()
        assert limiter._consecutive_rate_limits == 2

    @pytest.mark.asyncio
    async def test_sliding_window_clears(self):
        """Requests outside the window should be cleared."""
        limiter = RateLimiter(max_requests=5, window_seconds=0.1)
        for _ in range(5):
            await limiter.acquire()
        assert limiter.remaining == 0

        # Wait for window to expire
        await asyncio.sleep(0.15)
        assert limiter.remaining == 5

    @pytest.mark.asyncio
    async def test_acquire_waits_when_full(self):
        """Acquire should block when rate limit is reached."""
        limiter = RateLimiter(max_requests=2, window_seconds=0.2)
        await limiter.acquire()
        await limiter.acquire()

        start = time.monotonic()
        await limiter.acquire()  # Should wait for window to slide
        elapsed = time.monotonic() - start
        assert elapsed >= 0.1  # Should have waited some time
