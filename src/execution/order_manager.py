"""
Order manager: creates, submits, and tracks orders through the CLOB API.

Addresses: CORE-05 (orders), CORE-07 (rate limiting integration)
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog

from ..core.client import OrderResult, PolymarketClient
from ..core.db import Database
from ..core.rate_limiter import RateLimiter

if TYPE_CHECKING:
    from ..notifications.telegram import TelegramNotifier

logger = structlog.get_logger()


@dataclass
class Signal:
    """A trading signal emitted by a strategy.

    Strategies never place orders directly — they emit signals
    that the execution layer processes through risk checks.
    """

    strategy: str
    market_id: str
    token_id: str
    side: str  # "BUY" or "SELL"
    price: float
    size: float  # USDC amount
    order_type: str = "GTC"  # "GTC", "FOK", "IOC"
    urgency: str = "normal"  # "high" (immediate) or "normal"
    reasoning: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class OrderManager:
    """Manages the order lifecycle: signal → risk check → submit → track.

    All strategy signals are processed through this single point,
    preventing conflicts and enforcing rate limits.
    """

    def __init__(
        self,
        client: PolymarketClient,
        db: Database,
        rate_limiter: RateLimiter,
        notifier: TelegramNotifier | None = None,
        paper_mode: bool = False,
    ):
        self.client = client
        self.db = db
        self.rate_limiter = rate_limiter
        self._notifier = notifier
        self._paper_mode = paper_mode
        self._signal_queue: asyncio.Queue[Signal] = asyncio.Queue()
        self._running = False

    async def submit_signal(self, signal: Signal) -> None:
        """Add a signal to the processing queue."""
        await self._signal_queue.put(signal)
        logger.info(
            "signal_queued",
            strategy=signal.strategy,
            side=signal.side,
            price=signal.price,
            size=signal.size,
            queue_size=self._signal_queue.qsize(),
        )

    async def process_signals(self) -> None:
        """Main loop: process signals from the queue."""
        self._running = True
        logger.info("order_manager_started")

        while self._running:
            try:
                # Wait for next signal with timeout (to check _running flag)
                try:
                    signal = await asyncio.wait_for(self._signal_queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue

                await self._execute_signal(signal)

            except Exception as e:
                logger.error("signal_processing_error", error=str(e))
                await asyncio.sleep(1)

    async def stop(self) -> None:
        """Stop processing signals."""
        self._running = False
        logger.info("order_manager_stopped")

    async def _execute_signal(self, signal: Signal) -> OrderResult | None:
        """Execute a single trading signal.

        In paper mode, simulates the order without calling the CLOB API.
        """
        # Acquire rate limit slot
        await self.rate_limiter.acquire()

        try:
            if self._paper_mode:
                # Paper trading: simulate a successful order
                import uuid

                result = OrderResult(
                    success=True,
                    order_id=f"paper-{uuid.uuid4().hex[:12]}",
                    raw={"mode": "paper"},
                )
                logger.info(
                    "paper_order_simulated",
                    strategy=signal.strategy,
                    side=signal.side,
                    price=signal.price,
                    size=signal.size,
                )
            else:
                # Live trading: place the order via CLOB API
                result = self.client.create_and_place_order(
                    token_id=signal.token_id,
                    side=signal.side,
                    price=signal.price,
                    size=signal.size,
                    order_type=signal.order_type,
                )

            # Record in database
            if result.success:
                self.rate_limiter.record_success()
                self.db.record_trade(
                    order_id=result.order_id,
                    strategy=signal.strategy,
                    market_id=signal.market_id,
                    token_id=signal.token_id,
                    side=signal.side,
                    price=signal.price,
                    size=signal.size,
                    order_type=signal.order_type,
                    reasoning=signal.reasoning,
                    metadata=signal.metadata,
                )

                # Open a position in DB for entry trades (not exits)
                is_exit = signal.metadata.get("is_exit", False)
                if not is_exit and signal.side == "BUY":
                    stop_loss = signal.metadata.get("stop_loss_price")
                    self.db.open_position(
                        market_id=signal.market_id,
                        token_id=signal.token_id,
                        strategy=signal.strategy,
                        side=signal.side,
                        entry_price=signal.price,
                        size=signal.size,
                        stop_loss_price=stop_loss,
                        metadata=signal.metadata,
                    )
                    logger.info(
                        "position_opened",
                        strategy=signal.strategy,
                        market_id=signal.market_id,
                        side=signal.side,
                        price=signal.price,
                        size=signal.size,
                    )
                    # Send Telegram alert (TG-01)
                    if self._notifier:
                        await self._notifier.alert_position_opened(
                            strategy=signal.strategy,
                            market_id=signal.market_id,
                            side=signal.side,
                            price=signal.price,
                            size=signal.size,
                            reasoning=signal.reasoning,
                            market_question=signal.metadata.get("market_question", ""),
                        )
            else:
                if "rate" in result.error.lower() or "429" in result.error:
                    self.rate_limiter.record_rate_limit()

                logger.warning(
                    "order_failed",
                    strategy=signal.strategy,
                    error=result.error,
                )

            return result

        except Exception as e:
            logger.error(
                "order_execution_error",
                strategy=signal.strategy,
                error=str(e),
            )
            return None

    async def cancel_all(self) -> bool:
        """Cancel all open orders (kill switch support)."""
        logger.warning("kill_switch_cancel_all_orders")
        return self.client.cancel_all_orders()

    def get_pending_count(self) -> int:
        """Number of signals waiting to be processed."""
        return self._signal_queue.qsize()
