"""
Risk manager: enforces position limits, allocation caps, daily loss limits, kill switch.

Addresses: RISK-01 through RISK-07
Prevents: Pitfall 8 (overtrading), Pitfall 4 (fee miscalculation)

Audit fixes: C-09, C-10, C-11, H-13, H-14, H-15
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from ..core.config import StrategyConfig
from ..core.db import Database
from ..core.wallet import WalletManager

if TYPE_CHECKING:
    from .order_manager import OrderManager, Signal

logger = structlog.get_logger()

# Key for persisting kill switch state in DB metadata table
_KILL_SWITCH_DB_KEY = "risk_kill_switch_active"


class RiskManager:
    """Centralized risk management.

    Every signal passes through risk checks before execution.
    Enforces position limits, allocation caps, and loss limits.

    Audit fixes applied:
    - C-09: Fail-closed on wallet balance check failure.
    - C-10: Drains order queue when kill switch activates.
    - C-11: Daily loss includes unrealized P&L from open positions.
    - H-13: Blocks all trades when portfolio_value is 0 or unknown.
    - H-14: Prevents multiple strategies from opening on the same market.
    - H-15: Kill switch state persisted to DB, loaded on startup.
    """

    def __init__(
        self,
        strategy_config: StrategyConfig,
        db: Database,
        wallet: WalletManager,
    ):
        self.config = strategy_config
        self.db = db
        self.wallet = wallet
        self._order_manager: OrderManager | None = None
        self._trading_halted = False
        self._daily_loss_halt = False

        # H-15: Load persisted kill switch state from DB
        self._kill_switch_active = self._load_kill_switch_state()
        if self._kill_switch_active:
            logger.warning("kill_switch_restored_from_db")

    def set_order_manager(self, order_manager: OrderManager) -> None:
        """Set order manager reference (C-10: needed to drain queue on kill switch)."""
        self._order_manager = order_manager

    def approve_signal(self, signal: Signal) -> tuple[bool, str]:
        """Check if a signal passes all risk checks.

        Returns (approved, reason).
        """
        # Kill switch check
        if self._kill_switch_active:
            return False, "Kill switch active"

        # Trading halted
        if self._trading_halted:
            return False, "Trading halted"

        # Daily loss limit
        if self._daily_loss_halt:
            return False, "Daily loss limit reached"

        # H-13: Get portfolio value — block trades if unknown/zero
        portfolio_value = self._get_portfolio_value()
        if portfolio_value <= 0:
            logger.warning(
                "portfolio_value_zero_blocks_trade",
                portfolio_value=portfolio_value,
            )
            return False, "Portfolio value is 0 or unknown — cannot assess risk"

        # C-11: Daily P&L includes unrealized losses from open positions
        daily_realized_pnl = self.db.get_today_realized_pnl()
        total_unrealized_pnl = self._get_total_unrealized_pnl()
        daily_total_pnl = daily_realized_pnl + total_unrealized_pnl

        if daily_total_pnl < 0:
            daily_loss_pct = abs(daily_total_pnl) / portfolio_value * 100
            if daily_loss_pct >= self.config.daily_loss_limit_pct:
                self._daily_loss_halt = True
                logger.warning(
                    "daily_loss_limit_reached",
                    realized_pnl=daily_realized_pnl,
                    unrealized_pnl=total_unrealized_pnl,
                    total_pnl=daily_total_pnl,
                    loss_pct=round(daily_loss_pct, 2),
                    limit_pct=self.config.daily_loss_limit_pct,
                )
                limit = self.config.daily_loss_limit_pct
                return (
                    False,
                    f"Daily loss limit: {daily_loss_pct:.1f}% >= {limit}%"
                    f" (realized={daily_realized_pnl:.2f},"
                    f" unrealized={total_unrealized_pnl:.2f})",
                )

        # RISK-02: Max open positions
        open_count = self.db.count_open_positions()
        if open_count >= self.config.max_open_positions:
            return (
                False,
                f"Max open positions reached: {open_count}/{self.config.max_open_positions}",
            )

        # H-14: Duplicate market check — prevent any duplicate positions on same market
        # Blocks both cross-strategy AND same-strategy duplicates.
        if not signal.metadata.get("is_exit", False):
            open_positions = self.db.get_open_positions()
            for p in open_positions:
                if p["market_id"] == signal.market_id:
                    return (
                        False,
                        f"Market {signal.market_id[:12]}... already has position "
                        f"from strategy '{p['strategy']}'",
                    )

        # RISK-01: Max position size
        position_pct = (signal.size / portfolio_value) * 100
        if position_pct > self.config.max_position_pct:
            return False, (
                f"Position size too large: {position_pct:.1f}% > {self.config.max_position_pct}%"
            )

        # Min position size (fee protection)
        if signal.size < self.config.min_position_size_usd:
            return (
                False,
                f"Position below min size: ${signal.size} < ${self.config.min_position_size_usd}",
            )

        # RISK-03: Per-strategy allocation
        strategy_allocation = self.config.get_strategy_allocation(signal.strategy)
        if strategy_allocation > 0:
            max_allocation_usd = portfolio_value * (strategy_allocation / 100)
            current_strategy_exposure = self._get_strategy_exposure(signal.strategy)
            if current_strategy_exposure + signal.size > max_allocation_usd:
                return False, (
                    f"Strategy allocation exceeded: "
                    f"${current_strategy_exposure + signal.size:.0f} > ${max_allocation_usd:.0f}"
                )

        # C-09: RISK-07: Cash reserve — fail-CLOSED on balance check failure
        try:
            usdc_balance = self.wallet.get_usdc_balance()
            min_reserve = portfolio_value * (self.config.min_cash_reserve_pct / 100)
            if usdc_balance - signal.size < min_reserve:
                return False, (
                    f"Cash reserve: ${usdc_balance - signal.size:.0f} < ${min_reserve:.0f} minimum"
                )
        except Exception as e:
            # C-09: Fail-closed — deny trade when balance is uncertain
            logger.warning("balance_check_failed_deny_trade", error=str(e))
            return False, f"Balance check failed (fail-closed): {e}"

        # RISK-06: Minimum edge (for strategies that provide edge calculation)
        edge_pct = signal.metadata.get("edge_pct")
        if edge_pct is not None and edge_pct < self.config.min_edge_pct:
            return False, f"Edge too low: {edge_pct:.1f}% < {self.config.min_edge_pct}%"

        logger.info(
            "signal_approved",
            strategy=signal.strategy,
            side=signal.side,
            size=signal.size,
        )
        return True, "Approved"

    def activate_kill_switch(self) -> None:
        """Activate kill switch: block all new trades and drain pending signals.

        Addresses: RISK-05, C-10, H-15
        """
        self._kill_switch_active = True

        # H-15: Persist to DB
        self._persist_kill_switch_state(True)

        # C-10: Drain pending signal queue if order manager is set
        if self._order_manager is not None:
            drained = self._order_manager._drain_signal_queue()
            logger.warning("kill_switch_activated", signals_drained=drained)
        else:
            logger.warning("kill_switch_activated", signals_drained="N/A (no order_manager ref)")

    def deactivate_kill_switch(self) -> None:
        """Deactivate kill switch: resume trading."""
        self._kill_switch_active = False
        self._daily_loss_halt = False

        # H-15: Persist to DB
        self._persist_kill_switch_state(False)

        logger.info("kill_switch_deactivated")

    def pause_trading(self) -> None:
        """Pause all trading."""
        self._trading_halted = True
        logger.info("trading_paused")

    def resume_trading(self) -> None:
        """Resume trading."""
        self._trading_halted = False
        self._daily_loss_halt = False
        logger.info("trading_resumed")

    @property
    def is_kill_switch_active(self) -> bool:
        return self._kill_switch_active

    @property
    def is_trading_halted(self) -> bool:
        return self._kill_switch_active or self._trading_halted or self._daily_loss_halt

    def get_status(self) -> dict[str, Any]:
        """Get current risk status."""
        portfolio_value = self._get_portfolio_value()
        open_positions = self.db.count_open_positions()
        daily_realized = self.db.get_today_realized_pnl()
        total_unrealized = self._get_total_unrealized_pnl()

        return {
            "kill_switch": self._kill_switch_active,
            "halted": self._kill_switch_active or self._trading_halted or self._daily_loss_halt,
            "trading_halted": self._trading_halted,
            "daily_loss_halt": self._daily_loss_halt,
            "portfolio_value": portfolio_value,
            "open_positions": open_positions,
            "max_positions": self.config.max_open_positions,
            "daily_realized_pnl": daily_realized,
            "daily_unrealized_pnl": total_unrealized,
            "daily_total_pnl": daily_realized + total_unrealized,
            "daily_loss_limit_pct": self.config.daily_loss_limit_pct,
        }

    # ── Private helpers ──────────────────────────────────────────

    def _get_portfolio_value(self) -> float:
        """Estimate portfolio value (USDC balance + open position value).

        H-13: Returns 0 on failure, which blocks trades upstream.
        """
        try:
            usdc = self.wallet.get_usdc_balance()
        except Exception:
            usdc = 0.0

        # Add unrealized value from open positions
        positions = self.db.get_open_positions()
        position_value = sum(
            p.get("current_price", p.get("entry_price", 0)) * p.get("size", 0) for p in positions
        )

        return float(usdc + position_value)

    def _get_total_unrealized_pnl(self) -> float:
        """Sum unrealized P&L across all open positions (C-11)."""
        positions = self.db.get_open_positions()
        return float(sum(p.get("unrealized_pnl", 0.0) for p in positions))

    def _get_strategy_exposure(self, strategy: str) -> float:
        """Get current capital deployed by a specific strategy."""
        positions = self.db.get_open_positions(strategy=strategy)
        return float(sum(p.get("entry_price", 0) * p.get("size", 0) for p in positions))

    def _persist_kill_switch_state(self, active: bool) -> None:
        """Persist kill switch state to DB (H-15)."""
        try:
            self.db.set_metadata(_KILL_SWITCH_DB_KEY, "1" if active else "0")
        except Exception as e:
            logger.error("kill_switch_persist_failed", error=str(e))

    def _load_kill_switch_state(self) -> bool:
        """Load kill switch state from DB (H-15)."""
        try:
            val = self.db.get_metadata(_KILL_SWITCH_DB_KEY)
            return val == "1"
        except Exception:
            return False
