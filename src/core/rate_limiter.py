"""
Token bucket rate limiter for Polymarket API.

Enforces 60 orders/minute with exponential backoff on rate limit errors.

Addresses: CORE-07
"""

from __future__ import annotations

import asyncio
import time
from collections import deque

import structlog

logger = structlog.get_logger()


class RateLimiter:
    """Token bucket rate limiter.

    Polymarket CLOB API allows 60 orders per minute.
    This limiter tracks request timestamps and blocks when the limit is approached.
    """

    def __init__(self, max_requests: int = 55, window_seconds: float = 60.0):
        """Initialize rate limiter.

        Args:
            max_requests: Maximum requests per window (55, not 60, for safety margin)
            window_seconds: Time window in seconds
        """
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._timestamps: deque[float] = deque()
        self._lock = asyncio.Lock()
        self._backoff_until: float = 0.0
        self._consecutive_rate_limits: int = 0

    def _prune_old(self) -> None:
        """Remove timestamps older than the window."""
        cutoff = time.monotonic() - self.window_seconds
        while self._timestamps and self._timestamps[0] < cutoff:
            self._timestamps.popleft()

    @property
    def current_usage(self) -> int:
        """Number of requests in the current window."""
        self._prune_old()
        return len(self._timestamps)

    @property
    def remaining(self) -> int:
        """Remaining requests in the current window."""
        return max(0, self.max_requests - self.current_usage)

    async def acquire(self) -> None:
        """Wait until a request slot is available, then acquire it.

        Blocks if rate limit is reached. Respects exponential backoff
        if previous requests received 429 responses.
        """
        async with self._lock:
            # Respect backoff from rate limit errors
            now = time.monotonic()
            if now < self._backoff_until:
                wait = self._backoff_until - now
                logger.warning("rate_limit_backoff", wait_seconds=round(wait, 1))
                await asyncio.sleep(wait)

            # Wait until a slot is available
            while True:
                self._prune_old()
                if len(self._timestamps) < self.max_requests:
                    break

                # Wait for the oldest request to expire
                wait = self._timestamps[0] + self.window_seconds - time.monotonic()
                if wait > 0:
                    logger.info(
                        "rate_limit_wait",
                        wait_seconds=round(wait, 1),
                        current=len(self._timestamps),
                        max=self.max_requests,
                    )
                    await asyncio.sleep(wait + 0.1)

            self._timestamps.append(time.monotonic())
            self._consecutive_rate_limits = 0

    def record_rate_limit(self) -> None:
        """Record a 429 rate limit response and apply exponential backoff."""
        self._consecutive_rate_limits += 1
        backoff = min(2**self._consecutive_rate_limits, 60)  # Max 60s backoff
        self._backoff_until = time.monotonic() + backoff
        logger.warning(
            "rate_limit_hit",
            consecutive=self._consecutive_rate_limits,
            backoff_seconds=backoff,
        )

    def record_success(self) -> None:
        """Record a successful request (resets consecutive rate limit counter)."""
        self._consecutive_rate_limits = 0
