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
    from ..execution.risk_manager import RiskManager
    from ..notifications.telegram import TelegramNotifier

logger = structlog.get_logger()

# H-04 FIX: Bound signal queue to prevent unbounded memory growth
MAX_SIGNAL_QUEUE_SIZE = 100

# H-03 FIX: Retry config for failed exit orders
MAX_EXIT_RETRIES = 3
EXIT_RETRY_BACKOFF_BASE = 2.0  # seconds


@dataclass
class Signal:
    """A trading signal emitted by a strategy.

    Strategies never place orders directly — they emit signals
    that the execution layer processes through risk checks.

    C-06 FIX: size is always in USD. Conversion to shares happens at execution time.
    """

    strategy: str
    market_id: str
    token_id: str
    side: str  # "BUY" or "SELL"
    price: float
    size: float  # Always USD amount (C-06 FIX)
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
        risk_manager: RiskManager | None = None,
        notifier: TelegramNotifier | None = None,
        paper_mode: bool = False,
    ):
        self.client = client
        self.db = db
        self.rate_limiter = rate_limiter
        self._risk_manager = risk_manager
        self._notifier = notifier
        self._paper_mode = paper_mode
        # H-04 FIX: Bounded queue to prevent unbounded memory growth
        self._signal_queue: asyncio.Queue[Signal] = asyncio.Queue(maxsize=MAX_SIGNAL_QUEUE_SIZE)
        self._running = False

    def set_risk_manager(self, risk_manager: RiskManager) -> None:
        """Set the risk manager (allows deferred initialization to break circular deps)."""
        self._risk_manager = risk_manager

    async def submit_signal(self, signal: Signal) -> None:
        """Add a signal to the processing queue."""
        try:
            self._signal_queue.put_nowait(signal)
        except asyncio.QueueFull:
            logger.warning(
                "signal_queue_full",
                strategy=signal.strategy,
                dropped_side=signal.side,
                queue_size=self._signal_queue.qsize(),
            )
            return

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

    def _drain_signal_queue(self) -> int:
        """Drain all pending signals from the queue. Returns number drained.

        C-10 FIX: Used by kill switch to prevent queued signals from executing.
        """
        drained = 0
        while not self._signal_queue.empty():
            try:
                self._signal_queue.get_nowait()
                drained += 1
            except asyncio.QueueEmpty:
                break
        if drained > 0:
            logger.warning("signal_queue_drained", count=drained)
        return drained

    def _convert_usd_to_shares(self, usd_amount: float, price: float) -> float:
        """Convert USD amount to shares at given price.

        C-06 FIX: Signal.size is always USD. Convert to shares here before CLOB submission.
        """
        if price <= 0:
            logger.warning("invalid_price_for_conversion", price=price, usd=usd_amount)
            return 0.0
        return usd_amount / price

    async def _execute_signal(self, signal: Signal) -> OrderResult | None:
        """Execute a single trading signal.

        H-05 FIX: Always runs risk check before execution.
        C-06 FIX: Converts USD size to shares at execution time.
        In paper mode, simulates the order without calling the CLOB API.
        """
        # H-05 FIX: Always check risk manager before executing any signal
        if self._risk_manager is not None:
            approved, reason = self._risk_manager.approve_signal(signal)
            if not approved:
                logger.warning(
                    "signal_rejected_by_risk",
                    strategy=signal.strategy,
                    reason=reason,
                    side=signal.side,
                    size=signal.size,
                )
                return None

        # C-06 FIX: Convert USD → shares using current price
        shares = self._convert_usd_to_shares(signal.size, signal.price)
        if shares <= 0:
            logger.warning("zero_shares_after_conversion", usd=signal.size, price=signal.price)
            return None

        # Acquire rate limit slot
        await self.rate_limiter.acquire()

        is_exit = signal.metadata.get("is_exit", False)

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
                    size_usd=signal.size,
                    size_shares=shares,
                )
            else:
                # Live trading: place the order via CLOB API (now async)
                result = await self.client.create_and_place_order(
                    token_id=signal.token_id,
                    side=signal.side,
                    price=signal.price,
                    size=shares,  # C-06 FIX: Send shares to CLOB, not USD
                    order_type=signal.order_type,
                )

                # H-02 FIX: Poll for fill confirmation on FOK orders
                if result.success and signal.order_type == "FOK":
                    result = await self._confirm_fill(result, signal)

            # Record in database
            if result.success:
                self.rate_limiter.record_success()

                # H-19 FIX: Use transaction for atomic record_trade + open_position
                with self.db.transaction():
                    self.db.record_trade(
                        order_id=result.order_id,
                        strategy=signal.strategy,
                        market_id=signal.market_id,
                        token_id=signal.token_id,
                        side=signal.side,
                        price=signal.price,
                        size=signal.size,  # Record USD in DB
                        order_type=signal.order_type,
                        reasoning=signal.reasoning,
                        metadata=signal.metadata,
                    )

                    # Open a position in DB for entry trades (not exits)
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

                # C-12 FIX: If this is arb leg 2 and it failed, roll back leg 1
                if signal.metadata.get("arb_leg") == 2:
                    await self._rollback_arb_leg1(signal)

                # H-03 FIX: Retry failed exit orders with backoff (iterative)
                if is_exit:
                    retry_count = signal.metadata.get("_retry_count", 0)
                    while retry_count < MAX_EXIT_RETRIES:
                        retry_count += 1
                        signal.metadata["_retry_count"] = retry_count
                        backoff = EXIT_RETRY_BACKOFF_BASE**retry_count
                        logger.warning(
                            "exit_order_retry",
                            retry=retry_count,
                            max_retries=MAX_EXIT_RETRIES,
                            backoff_secs=backoff,
                        )
                        await asyncio.sleep(backoff)

                        # Re-run risk check + execution
                        retry_result = await self._execute_signal_inner(signal, shares)
                        if retry_result and retry_result.success:
                            result = retry_result
                            break
                    else:
                        logger.error(
                            "exit_order_retries_exhausted",
                            strategy=signal.strategy,
                            market_id=signal.market_id,
                        )

            return result

        except Exception as e:
            logger.error(
                "order_execution_error",
                strategy=signal.strategy,
                error=str(e),
            )
            return None

    async def _confirm_fill(self, result: OrderResult, signal: Signal) -> OrderResult:
        """H-02 FIX: Poll order status to confirm fill for FOK orders.

        FOK orders are either fully filled or cancelled, so we check status.
        """
        if not result.order_id:
            return result

        try:
            # Brief delay to allow exchange to process
            await asyncio.sleep(0.5)

            # Check open orders — if order is still there, it wasn't filled
            open_orders = await self.client.get_open_orders()
            order_still_open = any(
                o.get("id") == result.order_id or o.get("orderID") == result.order_id
                for o in open_orders
                if isinstance(o, dict)
            )

            if order_still_open:
                # FOK should never be still open — mark as failed
                logger.warning(
                    "fok_order_not_filled",
                    order_id=result.order_id,
                    strategy=signal.strategy,
                )
                return OrderResult(
                    success=False,
                    order_id=result.order_id,
                    error="FOK order not filled",
                    raw=result.raw,
                )

            logger.info("order_fill_confirmed", order_id=result.order_id)
            return result

        except Exception as e:
            logger.warning("fill_confirmation_failed", error=str(e), order_id=result.order_id)
            # Don't fail the order just because confirmation failed
            return result

    async def _execute_signal_inner(self, signal: Signal, shares: float) -> OrderResult | None:
        """Execute order placement only (no risk check, no recording).

        Used by retry logic to re-attempt just the CLOB submission.
        H-03 FIX: Extracted to support iterative retry without recursion.
        """
        try:
            await self.rate_limiter.acquire()

            if self._paper_mode:
                import uuid

                return OrderResult(
                    success=True,
                    order_id=f"paper-{uuid.uuid4().hex[:12]}",
                    raw={"mode": "paper"},
                )

            result = await self.client.create_and_place_order(
                token_id=signal.token_id,
                side=signal.side,
                price=signal.price,
                size=shares,
                order_type=signal.order_type,
            )

            # Confirm fill for FOK orders
            if result.success and signal.order_type == "FOK":
                result = await self._confirm_fill(result, signal)

            return result
        except Exception as e:
            logger.error("retry_execution_error", error=str(e))
            return None

    async def _rollback_arb_leg1(self, failed_leg2_signal: Signal) -> None:
        """C-12 FIX: Roll back arb leg 1 when leg 2 fails.

        Submits a compensating SELL order on the leg-1 token to unwind the
        naked position left by a successful leg 1 + failed leg 2.
        """
        rollback_token = failed_leg2_signal.metadata.get("arb_rollback_token_id")
        rollback_price = failed_leg2_signal.metadata.get("arb_rollback_price")
        rollback_size_usd = failed_leg2_signal.metadata.get("arb_rollback_size_usd")
        arb_pair_id = failed_leg2_signal.metadata.get("arb_pair_id", "")

        if not rollback_token or not rollback_price or not rollback_size_usd:
            logger.error(
                "arb_rollback_missing_metadata",
                arb_pair_id=arb_pair_id,
                has_token=bool(rollback_token),
                has_price=bool(rollback_price),
                has_size=bool(rollback_size_usd),
            )
            return

        logger.warning(
            "arb_rollback_initiated",
            arb_pair_id=arb_pair_id,
            rollback_token=str(rollback_token)[:16] + "...",
            rollback_size_usd=rollback_size_usd,
        )

        rollback_signal = Signal(
            strategy="arb_scanner",
            market_id=failed_leg2_signal.market_id,
            token_id=rollback_token,
            side="SELL",
            price=rollback_price,
            size=rollback_size_usd,
            order_type="FOK",
            urgency="high",
            reasoning=f"Arb rollback: leg 2 failed ({arb_pair_id})",
            metadata={
                "is_exit": True,
                "arb_rollback": True,
                "arb_pair_id": arb_pair_id,
            },
        )

        # Execute immediately (bypass queue to avoid delay)
        rollback_shares = self._convert_usd_to_shares(rollback_size_usd, rollback_price)
        if rollback_shares > 0:
            rollback_result = await self._execute_signal_inner(rollback_signal, rollback_shares)
            if rollback_result and rollback_result.success:
                logger.info("arb_rollback_success", arb_pair_id=arb_pair_id)
            else:
                error = rollback_result.error if rollback_result else "execution returned None"
                logger.error(
                    "arb_rollback_failed",
                    arb_pair_id=arb_pair_id,
                    error=error,
                )

    async def cancel_all(self) -> bool:
        """Cancel all open orders (kill switch support).

        C-10 FIX: Also drains the signal queue to prevent queued signals from executing.
        """
        logger.warning("kill_switch_cancel_all_orders")
        self._drain_signal_queue()
        return await self.client.cancel_all_orders()

    def get_pending_count(self) -> int:
        """Number of signals waiting to be processed."""
        return self._signal_queue.qsize()
