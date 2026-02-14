"""
Position manager: tracks open positions, evaluates TP/SL/trailing stop rules.

Addresses: POS-01 through POS-05
Audit fixes: C-07, C-08, H-06, H-07, M-15
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import structlog

from ..core.config import StrategyConfig
from ..core.db import Database
from .order_manager import OrderManager, Signal

if TYPE_CHECKING:
    from ..notifications.telegram import TelegramNotifier

logger = structlog.get_logger()

# Fee rates for P&L calculations (H-06)
# Polymarket charges ~3.15% taker fee and ~2% winner fee on resolved markets.
# These are approximations; actual fees may vary slightly.
TAKER_FEE_RATE = 0.0315  # 3.15% taker fee per trade
WINNER_FEE_RATE = 0.02  # 2% fee on winning resolution payouts


def _parse_metadata(position: dict[str, Any]) -> dict[str, Any]:
    """Safely parse position metadata from JSON string or dict."""
    meta = position.get("metadata")
    if meta is None:
        return {}
    if isinstance(meta, dict):
        return meta
    if isinstance(meta, str):
        try:
            parsed = json.loads(meta)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def _calc_gross_pnl(side: str, entry_price: float, exit_price: float, size_shares: float) -> float:
    """Calculate gross P&L (before fees).

    M-15: size is in shares, prices are per-share.
    P&L = (exit - entry) * shares for BUY; (entry - exit) * shares for SELL.
    """
    if side == "BUY":
        return (exit_price - entry_price) * size_shares
    else:
        return (entry_price - exit_price) * size_shares


def _calc_pnl_pct(side: str, entry_price: float, current_price: float) -> float:
    """Calculate P&L percentage from entry.

    Returns percentage (e.g., 5.0 for +5%).
    """
    if entry_price <= 0:
        return 0.0
    if side == "BUY":
        return ((current_price - entry_price) / entry_price) * 100
    else:
        return ((entry_price - current_price) / entry_price) * 100


def _estimate_fees(entry_price: float, exit_price: float, size_shares: float) -> float:
    """Estimate total trading fees for entry + exit (H-06).

    Entry fee: size_shares * entry_price * TAKER_FEE_RATE
    Exit fee:  size_shares * exit_price * TAKER_FEE_RATE
    Note: Winner fee on resolution is separate and handled in check_market_resolution.
    """
    entry_fee = size_shares * entry_price * TAKER_FEE_RATE
    exit_fee = size_shares * exit_price * TAKER_FEE_RATE
    return entry_fee + exit_fee


class PositionManager:
    """Manages open positions: real-time P&L, take-profit, stop-loss, trailing stops.

    Subscribes to price updates from WebSocket and evaluates exit rules
    for each open position.

    Audit fixes applied:
    - C-07: Uses 'closing' intermediate state; only marks 'closed' on fill.
    - C-08: _closing_positions set prevents duplicate exit orders.
    - H-06: Fees included in realized P&L.
    - H-07: Trailing stop ratchets correctly for SELL positions.
    - M-15: P&L formula uses shares * per-share-price consistently.
    """

    def __init__(
        self,
        strategy_config: StrategyConfig,
        db: Database,
        order_manager: OrderManager,
        notifier: TelegramNotifier | None = None,
    ):
        self.config = strategy_config
        self.db = db
        self.order_manager = order_manager
        self._notifier = notifier
        # C-08: Guard set — position IDs currently being closed.
        # Prevents duplicate exit orders from rapid price updates.
        self._closing_positions: set[int] = set()

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

            # C-08: Skip positions already being closed
            if pos_id in self._closing_positions:
                continue

            # Skip positions already in "closing" state (C-07)
            if position.get("status") == "closing":
                continue

            # Update current price in DB
            self.db.update_position_price(pos_id, price)

            # Calculate P&L percentage (M-15: uses per-share prices)
            entry_price = position["entry_price"]
            size = position["size"]
            side = position["side"]

            pnl_pct = _calc_pnl_pct(side, entry_price, price)

            # Check stop-loss (POS-03)
            if pnl_pct <= -self.config.stop_loss_pct:
                await self._close_position(position, price, "stop_loss", pnl_pct)
                continue

            # Check trailing stop (POS-04)
            # H-07: Correct direction for both BUY and SELL positions
            trailing_price = position.get("trailing_stop_price")
            if trailing_price:
                if side == "BUY" and price <= trailing_price:
                    await self._close_position(position, price, "trailing_stop", pnl_pct)
                    continue
                elif side == "SELL" and price >= trailing_price:
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
                            # H-07: Correct trailing direction for both sides
                            if side == "BUY":
                                new_trailing = price * (1 - trailing / 100)
                            else:
                                new_trailing = price * (1 + trailing / 100)

                            self.db.update_position_trailing_stop(pos_id, new_trailing)

                            logger.info(
                                "trailing_stop_set",
                                position_id=pos_id,
                                trailing_price=new_trailing,
                                side=side,
                            )

                    break  # Only trigger one tier per price update

            # H-07: Update trailing stop price — ratchet in the correct direction
            if trailing_price and pnl_pct > 0:
                if side == "BUY":
                    # BUY trailing: ratchet UP (only increase the floor)
                    new_trailing = price * (1 - self.config.trailing_stop_pct / 100)
                    if new_trailing > trailing_price:
                        self.db.update_position_trailing_stop(pos_id, new_trailing)
                elif side == "SELL":
                    # SELL trailing: ratchet DOWN (only decrease the ceiling)
                    new_trailing = price * (1 + self.config.trailing_stop_pct / 100)
                    if new_trailing < trailing_price:
                        self.db.update_position_trailing_stop(pos_id, new_trailing)

    async def _close_position(
        self,
        position: dict[str, Any],
        exit_price: float,
        reason: str,
        pnl_pct: float,
    ) -> None:
        """Close a position fully.

        C-07: Sets position to 'closing' state first, not 'closed'.
              'closed' is only set on fill confirmation in order_manager.
        C-08: Adds pos_id to _closing_positions to block duplicate exits.
        H-06: Includes estimated fees in realized P&L.
        M-15: P&L calculated as (exit - entry) * shares.
        """
        pos_id = position["id"]
        entry_price = position["entry_price"]
        size = position["size"]  # shares
        side = position["side"]

        # C-08: Guard against duplicate exit orders
        if pos_id in self._closing_positions:
            logger.warning("duplicate_close_blocked", position_id=pos_id)
            return
        self._closing_positions.add(pos_id)

        # M-15 + H-06: Calculate net P&L including fees
        gross_pnl = _calc_gross_pnl(side, entry_price, exit_price, size)
        fees = _estimate_fees(entry_price, exit_price, size)
        realized_pnl = gross_pnl - fees

        # Submit sell signal
        sell_side = "SELL" if side == "BUY" else "BUY"
        signal = Signal(
            strategy=position["strategy"],
            market_id=position["market_id"],
            token_id=position["token_id"],
            side=sell_side,
            price=exit_price,
            size=size * exit_price,  # Convert shares to USD for Signal (C-06 convention)
            order_type="FOK",  # Immediate execution for exits
            urgency="high",
            reasoning=f"Position close: {reason} (P&L: {pnl_pct:+.1f}%)",
            metadata={
                "is_exit": True,
                "position_id": pos_id,
                "realized_pnl": round(realized_pnl, 4),
            },
        )
        await self.order_manager.submit_signal(signal)

        # C-07: Mark as 'closing' (not 'closed') — will be finalized on fill
        self.db.set_position_closing(pos_id, reason)

        logger.info(
            "position_closing",
            position_id=pos_id,
            reason=reason,
            pnl_pct=round(pnl_pct, 2),
            gross_pnl=round(gross_pnl, 4),
            fees=round(fees, 4),
            realized_pnl=round(realized_pnl, 4),
            strategy=position["strategy"],
        )

        # Send Telegram alert (TG-02)
        if self._notifier:
            opened_at = position.get("opened_at")
            duration = ""
            if opened_at:
                try:
                    opened_dt = datetime.fromisoformat(opened_at.replace("Z", "+00:00"))
                    delta = datetime.now(timezone.utc) - opened_dt
                    hours = delta.total_seconds() / 3600
                    duration = f"{hours:.1f}h"
                except Exception:
                    pass
            await self._notifier.alert_position_closed(
                strategy=position["strategy"],
                market_id=position["market_id"],
                reason=reason,
                pnl=realized_pnl,
                pnl_pct=pnl_pct,
                hold_duration_str=duration,
                market_question=_parse_metadata(position).get("market_question", ""),
            )

    async def confirm_close(self, position_id: int, realized_pnl: float, reason: str) -> None:
        """Finalize position closure after fill confirmation (C-07).

        Called by OrderManager when the exit order is confirmed filled.
        Transitions: 'closing' -> 'closed'.
        """
        self.db.close_position(position_id, realized_pnl, reason)
        self._closing_positions.discard(position_id)

        logger.info(
            "position_closed_confirmed",
            position_id=position_id,
            realized_pnl=round(realized_pnl, 4),
        )

    def release_closing_guard(self, position_id: int) -> None:
        """Release the closing guard if an exit order fails (C-08).

        Allows the position manager to retry closing on the next price update.
        """
        self._closing_positions.discard(position_id)
        logger.warning(
            "closing_guard_released",
            position_id=position_id,
            reason="exit_order_failed",
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
            size=sell_size * price,  # Convert shares to USD for Signal (C-06)
            order_type="FOK",
            urgency="high",
            reasoning=f"Partial take-profit tier {tier_index}",
            metadata={"is_exit": True, "position_id": pos_id},
        )
        await self.order_manager.submit_signal(signal)

        # Update position size and TP tier
        remaining = position["size"] - sell_size
        self.db.update_position_partial_close(pos_id, remaining, tier_index)

        logger.info(
            "partial_close",
            position_id=pos_id,
            tier=tier_index,
            sold=sell_size,
            remaining=remaining,
        )

        # Send Telegram alert for partial close
        if self._notifier:
            await self._notifier.alert_position_closed(
                strategy=position["strategy"],
                market_id=position["market_id"],
                reason=f"Partial take-profit tier {tier_index}",
                pnl=0,  # Realized P&L tracked separately
                pnl_pct=0,
                hold_duration_str="",
                market_question=_parse_metadata(position).get("market_question", ""),
            )

    def check_market_resolution(self, market_id: str, outcome: str) -> None:
        """Handle market resolution.

        Addresses: POS-05
        H-06: Includes winner fee in resolution P&L.

        Args:
            market_id: The condition ID of the resolved market.
            outcome: The winning token_id, or "yes"/"no" for simple binary markets.
        """
        positions = self.db.get_open_positions()

        for position in positions:
            if position["market_id"] != market_id:
                continue

            pos_id = position["id"]
            entry_price = position["entry_price"]
            size = position["size"]
            side = position["side"]
            token_id = position["token_id"]

            # Determine if this position's token won
            outcome_lower = outcome.lower()
            token_won = (
                token_id == outcome  # Direct token_id match
                or (
                    outcome_lower == "yes"
                    and token_id == _parse_metadata(position).get("yes_token_id", "")
                )
            )

            # Fallback: if outcome doesn't match token_id directly,
            # check if the outcome matches the token's side
            if not token_won and outcome_lower in ("yes", "no"):
                pass

            if side == "BUY":
                resolution_price = 1.0 if token_won else 0.0
                gross_pnl = (resolution_price - entry_price) * size
            else:
                resolution_price = 0.0 if token_won else 1.0
                gross_pnl = (entry_price - resolution_price) * size

            # H-06: Deduct entry fee + winner fee (if position won)
            entry_fee = size * entry_price * TAKER_FEE_RATE
            winner_fee = 0.0
            if gross_pnl > 0:
                # Winner fee applies to the payout (resolution_price * size)
                winner_fee = resolution_price * size * WINNER_FEE_RATE
            realized_pnl = gross_pnl - entry_fee - winner_fee

            # Remove from closing guard if it was being closed
            self._closing_positions.discard(pos_id)

            self.db.close_position(pos_id, realized_pnl, "market_resolved")

            logger.info(
                "position_resolved",
                position_id=pos_id,
                market_id=market_id,
                token_won=token_won,
                gross_pnl=round(gross_pnl, 4),
                entry_fee=round(entry_fee, 4),
                winner_fee=round(winner_fee, 4),
                realized_pnl=round(realized_pnl, 4),
            )

    def get_portfolio_summary(self) -> dict[str, Any]:
        """Get portfolio summary: open positions, exposure, P&L."""
        positions = self.db.get_open_positions()
        total_exposure = 0.0
        total_unrealized = 0.0

        for p in positions:
            # M-15: exposure = entry_price * shares (USD value)
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
