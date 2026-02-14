"""
Token bucket rate limiter for Polymarket API.

Enforces 60 orders/minute with exponential backoff on rate limit errors.

Addresses: CORE-07
Audit fixes: H-16, H-17
"""

from __future__ import annotations

import asyncio
import time
from collections import deque

import structlog

logger = structlog.get_logger()

# H-17: Number of consecutive successes required to reset the error counter
_CONSECUTIVE_SUCCESS_THRESHOLD = 3


class RateLimiter:
    """Token bucket rate limiter.

    Polymarket CLOB API allows 60 orders per minute.
    This limiter tracks request timestamps and blocks when the limit is approached.

    Audit fixes:
    - H-16: Backoff sleep released from lock to avoid blocking all coroutines.
    - H-17: Error counter only resets after N consecutive successes.
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
        self._consecutive_successes: int = 0  # H-17

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

        H-16: Backoff sleep and window-wait sleep happen OUTSIDE the lock
        to avoid blocking all other coroutines.
        """
        # Phase 1: Wait for backoff outside the lock (H-16)
        now = time.monotonic()
        if now < self._backoff_until:
            wait = self._backoff_until - now
            logger.warning("rate_limit_backoff", wait_seconds=round(wait, 1))
            await asyncio.sleep(wait)

        # Phase 2: Acquire slot — loop until we get one
        while True:
            sleep_time = 0.0
            async with self._lock:
                self._prune_old()
                if len(self._timestamps) < self.max_requests:
                    # Slot available — claim it and return
                    self._timestamps.append(time.monotonic())
                    return

                # No slot — calculate how long to wait for the oldest to expire
                sleep_time = self._timestamps[0] + self.window_seconds - time.monotonic()

            # H-16: Sleep OUTSIDE the lock so other coroutines can proceed
            if sleep_time > 0:
                logger.info(
                    "rate_limit_wait",
                    wait_seconds=round(sleep_time, 1),
                    current=len(self._timestamps),
                    max=self.max_requests,
                )
                await asyncio.sleep(sleep_time + 0.1)

    def record_rate_limit(self) -> None:
        """Record a 429 rate limit response and apply exponential backoff."""
        self._consecutive_rate_limits += 1
        self._consecutive_successes = 0  # H-17: reset success streak
        backoff = min(2**self._consecutive_rate_limits, 60)  # Max 60s backoff
        self._backoff_until = time.monotonic() + backoff
        logger.warning(
            "rate_limit_hit",
            consecutive=self._consecutive_rate_limits,
            backoff_seconds=backoff,
        )

    def record_success(self) -> None:
        """Record a successful request.

        H-17: Only resets the error counter after N consecutive successes,
        not immediately on the first success after a failure.
        """
        self._consecutive_successes += 1
        if self._consecutive_successes >= _CONSECUTIVE_SUCCESS_THRESHOLD:
            if self._consecutive_rate_limits > 0:
                logger.info(
                    "rate_limit_counter_reset",
                    after_successes=self._consecutive_successes,
                    previous_errors=self._consecutive_rate_limits,
                )
            self._consecutive_rate_limits = 0
            self._consecutive_successes = 0
