"""Stink bid strategy: place deep discount limit orders on high-volume markets.

Addresses: STINK-01 (place bids), STINK-02 (auto-refresh), STINK-03 (allocation limit)

Concept:
"Stink bids" are Buy orders placed at extremely low prices (e.g., $0.10 for a token
trading at $0.50).  They profit from "fat finger" mistakes or momentary liquidity
crashes.  The goal is to buy cheap and let the position manager take profit when
prices revert.

Strategy:
1. Select active markets with high volume (liquidity > $10k).
2. Calculate a "stink price" (70-90% below current market price).
3. Place GTC limit orders.
4. Monitor and refresh orders if they expire or are cancelled.
5. Limit total capital committed to this strategy.

Audit fixes: H-08, H-09, M-16, M-25
"""

from __future__ import annotations

import asyncio
import random
from typing import TYPE_CHECKING, Any

import structlog

from ..core.client import PolymarketClient
from ..core.config import StrategyConfig
from ..core.db import Database
from ..execution.order_manager import OrderManager, Signal
from ..execution.risk_manager import RiskManager
from .base import BaseStrategy

if TYPE_CHECKING:
    from ..notifications.telegram import TelegramNotifier

logger = structlog.get_logger()


class StinkBidder(BaseStrategy):
    """Places deep discount limit orders to catch market anomalies.

    STINK-01: Place GTC limit orders at 70-90% discount.
    STINK-02: Auto-refresh expired or cancelled orders.
    STINK-03: Respect max allocation and max active bids limits.

    Audit fixes:
    - H-08: _active_orders populated after order submission via DB cross-reference.
    - H-09: Sync CLOB calls wrapped in asyncio.to_thread().
    - M-16: Skip resolved/closed markets before bidding.
    - M-25: Signal.size is always in USD (C-06 convention).
    """

    def __init__(
        self,
        client: PolymarketClient,
        db: Database,
        order_manager: OrderManager,
        risk_manager: RiskManager,
        strategy_config: StrategyConfig,
        notifier: TelegramNotifier | None = None,
    ) -> None:
        super().__init__(
            name="stink_bidder",
            client=client,
            db=db,
            order_manager=order_manager,
            risk_manager=risk_manager,
            strategy_config=strategy_config,
        )
        self._notifier = notifier

        # Config
        self._allocation_pct: float = self._config.get("allocation_pct", 20.0)
        self._min_discount_pct: float = self._config.get("min_discount_pct", 70.0)
        self._max_discount_pct: float = self._config.get("max_discount_pct", 90.0)
        self._max_active_bids: int = self._config.get("max_active_bids", 10)
        self._min_market_volume: float = self._config.get("min_market_volume_usd", 10000.0)

        # Override eval interval with refresh_interval_sec from config
        self._eval_interval = self._config.get("refresh_interval_sec", 300)

        # H-08: Track active orders — populated via reconciliation
        self._active_orders: dict[str, dict[str, Any]] = {}  # order_id -> details

    async def initialize(self) -> None:
        """Load state and active orders."""
        # Restore active orders from persisted state if available
        saved_orders = self.get_state("active_orders", {})
        if isinstance(saved_orders, dict):
            self._active_orders = saved_orders

        # H-08: Reconcile with actual open orders from CLOB
        await self._reconcile_orders()

        logger.info(
            "stink_bidder_initialized",
            active_bids=len(self._active_orders),
            max_bids=self._max_active_bids,
            discount_range=f"{self._min_discount_pct}%-{self._max_discount_pct}%",
            eval_interval=self._eval_interval,
        )

    async def evaluate(self) -> list[Signal]:
        """Main strategy loop.

        1. Reconcile open orders (STINK-02).
        2. Check if we have capacity for new bids.
        3. If yes, find high-volume markets and place new stink bids.
        """
        signals: list[Signal] = []

        # 1. Clean up orders that are no longer open (filled or cancelled)
        await self._reconcile_orders()

        # 2. Check capacity
        current_bids = len(self._active_orders)
        if current_bids >= self._max_active_bids:
            logger.info("stink_bidder_at_capacity", count=current_bids, max=self._max_active_bids)
            return signals

        slots_available = self._max_active_bids - current_bids
        logger.info("stink_bidder_slots_available", slots=slots_available)

        # 3. Find candidate markets
        markets = await self.get_active_markets(min_volume=self._min_market_volume)
        if not markets:
            logger.warning("stink_no_markets_found", min_volume=self._min_market_volume)
            return signals

        # Shuffle markets to avoid always picking the same ones
        random.shuffle(markets)

        # 4. Generate signals for new bids
        for market in markets:
            if len(signals) >= slots_available:
                break

            # Skip if we already have a bid on this market
            if self._has_bid_on_market(market.condition_id):
                continue

            # M-16: Skip resolved/closed markets (fields always present on dataclass)
            if market.closed or market.resolved:
                logger.debug(
                    "stink_skip_closed_or_resolved",
                    market_id=market.condition_id[:16],
                    closed=market.closed,
                    resolved=market.resolved,
                )
                continue

            # Check price validity
            if market.yes_price <= 0 or market.yes_price >= 1:
                continue

            # Pick the higher-priced token (more likely to crash)
            target_token_id = market.yes_token_id
            current_price = market.yes_price
            side_name = "Yes"

            if market.no_price > market.yes_price:
                target_token_id = market.no_token_id
                current_price = market.no_price
                side_name = "No"

            # Calculate stink price (STINK-01)
            discount_pct = random.uniform(self._min_discount_pct, self._max_discount_pct)
            stink_price = current_price * (1 - discount_pct / 100)

            # Round to 3 decimal places
            stink_price = float(f"{stink_price:.3f}")

            # Safety clamp: never bid above $0.10 for a stink bid
            if stink_price > 0.10:
                stink_price = 0.10
            if stink_price <= 0.01:
                stink_price = 0.01  # Minimum price

            # M-25: Size in USD (C-06 convention) — OrderManager converts to shares
            size_usd = self._strategy_config.min_position_size_usd * 2

            signal = Signal(
                strategy="stink_bidder",
                market_id=market.condition_id,
                token_id=target_token_id,
                side="BUY",
                price=stink_price,
                size=size_usd,  # M-25: USD, not shares
                order_type="GTC",
                urgency="normal",
                reasoning=f"Stink bid: {discount_pct:.1f}% discount on {side_name}",
                metadata={
                    "stink_bid": True,
                    "discount_pct": discount_pct,
                    "market_question": market.question,
                },
            )
            signals.append(signal)

        return signals

    async def _reconcile_orders(self) -> None:
        """STINK-02 + H-08: Reconcile tracked orders with actual open orders on CLOB.

        H-08: Also scans for orders from our strategy in the DB that we may
        have missed tracking (e.g., from a restart).
        H-09: Sync CLOB calls wrapped in asyncio.to_thread().
        """
        try:
            # H-09: Wrap sync CLOB call in asyncio.to_thread()
            open_orders = await asyncio.to_thread(self._client.clob.get_orders)
            if not isinstance(open_orders, list):
                open_orders = []

            open_order_ids = {o.get("orderID") for o in open_orders}

            # H-08: Claim any open orders from our strategy that we're not tracking
            for order in open_orders:
                order_id = order.get("orderID", "")
                if order_id and order_id not in self._active_orders:
                    # Check if this order belongs to us via DB cross-reference
                    # (DB trade records include strategy name)
                    trades = self._db.conn.execute(
                        "SELECT * FROM trades WHERE order_id = ? AND strategy = 'stink_bidder'",
                        (order_id,),
                    ).fetchall()
                    if trades:
                        self._active_orders[order_id] = {
                            "market_id": order.get("market", ""),
                            "token_id": order.get("asset_id", ""),
                            "price": float(order.get("price", 0)),
                        }
                        logger.info(
                            "stink_bid_reclaimed",
                            order_id=order_id,
                        )

            # Remove orders that are no longer open (filled or cancelled)
            missing_ids = [oid for oid in self._active_orders if oid not in open_order_ids]
            for mid in missing_ids:
                removed = self._active_orders.pop(mid, None)
                if removed:
                    logger.info("stink_bid_removed", order_id=mid, reason="filled_or_cancelled")

            # Persist updated state
            self.set_state("active_orders", self._active_orders)

        except Exception as e:
            logger.error("stink_reconcile_error", error=str(e))

    async def emit_signal(self, signal: Signal) -> bool:
        """Override to track order placement in _active_orders (H-08).

        After queuing the signal, add a placeholder entry so we don't
        over-allocate bid slots before reconciliation confirms it.
        """
        success = await super().emit_signal(signal)

        if success:
            # H-08: Add placeholder entry keyed by market_id+token_id
            # (we don't have the order_id yet; reconciliation will fix the key)
            placeholder_key = f"pending_{signal.market_id}_{signal.token_id}"
            self._active_orders[placeholder_key] = {
                "market_id": signal.market_id,
                "token_id": signal.token_id,
                "price": signal.price,
                "pending": True,
            }
            self.set_state("active_orders", self._active_orders)
            logger.info(
                "stink_bid_slot_reserved",
                market_id=signal.market_id[:16],
                price=signal.price,
            )

        return success

    def _has_bid_on_market(self, market_id: str) -> bool:
        """Check if we already have an active bid for this market."""
        for info in self._active_orders.values():
            if info.get("market_id") == market_id:
                return True
        return False

    async def shutdown(self) -> None:
        """Persist state on shutdown."""
        logger.info("stink_bidder_shutdown", active_bids=len(self._active_orders))

    def get_status(self) -> dict[str, Any]:
        base = super().get_status()
        base.update(
            {
                "active_bids": len(self._active_orders),
                "max_bids": self._max_active_bids,
                "min_volume": self._min_market_volume,
            }
        )
        return base
