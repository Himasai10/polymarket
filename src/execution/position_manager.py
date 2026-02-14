"""
Position manager: tracks open positions, evaluates TP/SL/trailing stop rules.

Addresses: POS-01 through POS-05
"""

from __future__ import annotations

from typing import Any

import structlog

from ..core.config import StrategyConfig
from ..core.db import Database
from .order_manager import OrderManager, Signal

logger = structlog.get_logger()


class PositionManager:
    """Manages open positions: real-time P&L, take-profit, stop-loss, trailing stops.

    Subscribes to price updates from WebSocket and evaluates exit rules
    for each open position.
    """

    def __init__(
        self,
        strategy_config: StrategyConfig,
        db: Database,
        order_manager: OrderManager,
    ):
        self.config = strategy_config
        self.db = db
        self.order_manager = order_manager

    async def on_price_update(self, token_id: str, price: float, timestamp: float) -> None:
        """Handle a price update from WebSocket.

        Evaluates TP/SL/trailing for all positions matching this token.
        This is registered as a WebSocket callback.
        """
        positions = self.db.get_open_positions()

        for position in positions:
            if position["token_id"] != token_id:
                continue

            pos_id = position["id"]

            # Update current price in DB
            self.db.update_position_price(pos_id, price)

            # Calculate P&L
            entry_price = position["entry_price"]
            size = position["size"]
            side = position["side"]

            if side == "BUY":
                pnl_pct = ((price - entry_price) / entry_price) * 100
            else:
                pnl_pct = ((entry_price - price) / entry_price) * 100

            # Check stop-loss (POS-03)
            if pnl_pct <= -self.config.stop_loss_pct:
                await self._close_position(position, price, "stop_loss", pnl_pct)
                continue

            # Check trailing stop (POS-04)
            trailing_price = position.get("trailing_stop_price")
            if trailing_price and side == "BUY" and price <= trailing_price:
                await self._close_position(position, price, "trailing_stop", pnl_pct)
                continue
            elif trailing_price and side == "SELL" and price >= trailing_price:
                await self._close_position(position, price, "trailing_stop", pnl_pct)
                continue

            # Check take-profit tiers (POS-02)
            tp_triggered = position.get("take_profit_triggered", 0)
            tiers = self.config.get_take_profit_tiers()

            for i, tier in enumerate(tiers):
                if i < tp_triggered:
                    continue  # Already triggered this tier

                gain_threshold = tier["gain_pct"]
                sell_pct = tier["sell_pct"]

                if pnl_pct >= gain_threshold:
                    sell_size = size * (sell_pct / 100)

                    if sell_pct >= 100:
                        # Full exit
                        await self._close_position(position, price, "take_profit", pnl_pct)
                    else:
                        # Partial exit
                        await self._partial_close(position, price, sell_size, i + 1)

                        # Activate trailing stop after first TP tier
                        if not trailing_price:
                            trailing = self.config.trailing_stop_pct
                            if side == "BUY":
                                new_trailing = price * (1 - trailing / 100)
                            else:
                                new_trailing = price * (1 + trailing / 100)

                            self.db.conn.execute(
                                "UPDATE positions SET trailing_stop_price = ? WHERE id = ?",
                                (new_trailing, pos_id),
                            )
                            self.db.conn.commit()

                            logger.info(
                                "trailing_stop_set",
                                position_id=pos_id,
                                trailing_price=new_trailing,
                            )

                    break  # Only trigger one tier per price update

            # Update trailing stop price if position is in profit and trailing is active
            if trailing_price and pnl_pct > 0:
                if side == "BUY":
                    new_trailing = price * (1 - self.config.trailing_stop_pct / 100)
                    if new_trailing > trailing_price:
                        self.db.conn.execute(
                            "UPDATE positions SET trailing_stop_price = ? WHERE id = ?",
                            (new_trailing, pos_id),
                        )
                        self.db.conn.commit()
                elif side == "SELL":
                    new_trailing = price * (1 + self.config.trailing_stop_pct / 100)
                    if new_trailing < trailing_price:
                        self.db.conn.execute(
                            "UPDATE positions SET trailing_stop_price = ? WHERE id = ?",
                            (new_trailing, pos_id),
                        )
                        self.db.conn.commit()

    async def _close_position(
        self,
        position: dict[str, Any],
        exit_price: float,
        reason: str,
        pnl_pct: float,
    ) -> None:
        """Close a position fully."""
        pos_id = position["id"]
        entry_price = position["entry_price"]
        size = position["size"]
        side = position["side"]

        if side == "BUY":
            realized_pnl = (exit_price - entry_price) * size
        else:
            realized_pnl = (entry_price - exit_price) * size

        # Submit sell signal
        sell_side = "SELL" if side == "BUY" else "BUY"
        signal = Signal(
            strategy=position["strategy"],
            market_id=position["market_id"],
            token_id=position["token_id"],
            side=sell_side,
            price=exit_price,
            size=size,
            order_type="FOK",  # Immediate execution for exits
            urgency="high",
            reasoning=f"Position close: {reason} (P&L: {pnl_pct:+.1f}%)",
        )
        await self.order_manager.submit_signal(signal)

        # Update position in DB
        self.db.close_position(pos_id, realized_pnl, reason)

        logger.info(
            "position_closed",
            position_id=pos_id,
            reason=reason,
            pnl_pct=round(pnl_pct, 2),
            realized_pnl=round(realized_pnl, 2),
            strategy=position["strategy"],
        )

    async def _partial_close(
        self,
        position: dict[str, Any],
        price: float,
        sell_size: float,
        tier_index: int,
    ) -> None:
        """Partially close a position (take partial profit)."""
        pos_id = position["id"]
        side = position["side"]

        sell_side = "SELL" if side == "BUY" else "BUY"
        signal = Signal(
            strategy=position["strategy"],
            market_id=position["market_id"],
            token_id=position["token_id"],
            side=sell_side,
            price=price,
            size=sell_size,
            order_type="FOK",
            urgency="high",
            reasoning=f"Partial take-profit tier {tier_index}",
        )
        await self.order_manager.submit_signal(signal)

        # Update position size and TP tier
        remaining = position["size"] - sell_size
        self.db.conn.execute(
            "UPDATE positions SET size = ?, take_profit_triggered = ? WHERE id = ?",
            (remaining, tier_index, pos_id),
        )
        self.db.conn.commit()

        logger.info(
            "partial_close",
            position_id=pos_id,
            tier=tier_index,
            sold=sell_size,
            remaining=remaining,
        )

    def check_market_resolution(self, market_id: str, outcome: str) -> None:
        """Handle market resolution.

        Addresses: POS-05
        """
        positions = self.db.get_open_positions()

        for position in positions:
            if position["market_id"] != market_id:
                continue

            pos_id = position["id"]
            entry_price = position["entry_price"]
            size = position["size"]

            # If we hold the winning token, profit = (1.0 - entry_price) * size
            # If we hold the losing token, loss = entry_price * size
            token_id = position["token_id"]
            # Determine if this position won
            # outcome would be the winning token_id or "yes"/"no"

            # Simplified: assume resolution price is 1.0 for winner, 0.0 for loser
            if position["side"] == "BUY":
                resolution_price = 1.0  # Placeholder - needs actual resolution data
                realized_pnl = (resolution_price - entry_price) * size
            else:
                resolution_price = 0.0
                realized_pnl = (entry_price - resolution_price) * size

            self.db.close_position(pos_id, realized_pnl, "market_resolved")

            logger.info(
                "position_resolved",
                position_id=pos_id,
                market_id=market_id,
                realized_pnl=round(realized_pnl, 2),
            )

    def get_portfolio_summary(self) -> dict[str, Any]:
        """Get portfolio summary: open positions, exposure, P&L."""
        positions = self.db.get_open_positions()
        total_exposure = 0.0
        total_unrealized = 0.0

        for p in positions:
            exposure = p.get("entry_price", 0) * p.get("size", 0)
            total_exposure += exposure
            total_unrealized += p.get("unrealized_pnl", 0)

        return {
            "open_positions": len(positions),
            "total_exposure": round(total_exposure, 2),
            "total_unrealized_pnl": round(total_unrealized, 2),
            "daily_realized_pnl": round(self.db.get_today_realized_pnl(), 2),
            "positions": positions,
        }
