"""
Risk manager: enforces position limits, allocation caps, daily loss limits, kill switch.

Addresses: RISK-01 through RISK-07
Prevents: Pitfall 8 (overtrading), Pitfall 4 (fee miscalculation)
"""

from __future__ import annotations

from typing import Any

import structlog

from ..core.config import StrategyConfig
from ..core.db import Database
from ..core.wallet import WalletManager
from .order_manager import Signal

logger = structlog.get_logger()


class RiskManager:
    """Centralized risk management.

    Every signal passes through risk checks before execution.
    Enforces position limits, allocation caps, and loss limits.
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
        self._kill_switch_active = False
        self._trading_halted = False
        self._daily_loss_halt = False

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

        # Check daily P&L
        daily_pnl = self.db.get_today_realized_pnl()
        portfolio_value = self._get_portfolio_value()
        if portfolio_value > 0:
            daily_loss_pct = abs(daily_pnl) / portfolio_value * 100
            if daily_pnl < 0 and daily_loss_pct >= self.config.daily_loss_limit_pct:
                self._daily_loss_halt = True
                logger.warning(
                    "daily_loss_limit_reached",
                    daily_pnl=daily_pnl,
                    limit_pct=self.config.daily_loss_limit_pct,
                )
                return (
                    False,
                    f"Daily loss limit: {daily_loss_pct:.1f}% >= {self.config.daily_loss_limit_pct}%",
                )

        # RISK-02: Max open positions
        open_count = self.db.count_open_positions()
        if open_count >= self.config.max_open_positions:
            return (
                False,
                f"Max open positions reached: {open_count}/{self.config.max_open_positions}",
            )

        # RISK-01: Max position size
        if portfolio_value > 0:
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
        if strategy_allocation > 0 and portfolio_value > 0:
            max_allocation_usd = portfolio_value * (strategy_allocation / 100)
            current_strategy_exposure = self._get_strategy_exposure(signal.strategy)
            if current_strategy_exposure + signal.size > max_allocation_usd:
                return False, (
                    f"Strategy allocation exceeded: "
                    f"${current_strategy_exposure + signal.size:.0f} > ${max_allocation_usd:.0f}"
                )

        # RISK-07: Cash reserve
        try:
            usdc_balance = self.wallet.get_usdc_balance()
            min_reserve = portfolio_value * (self.config.min_cash_reserve_pct / 100)
            if usdc_balance - signal.size < min_reserve:
                return False, (
                    f"Cash reserve: ${usdc_balance - signal.size:.0f} < ${min_reserve:.0f} minimum"
                )
        except Exception as e:
            logger.warning("balance_check_failed_in_risk", error=str(e))
            # Allow trade if balance check fails (don't block on RPC issues)

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
        """Activate kill switch: block all new trades.

        Addresses: RISK-05
        """
        self._kill_switch_active = True
        logger.warning("kill_switch_activated")

    def deactivate_kill_switch(self) -> None:
        """Deactivate kill switch: resume trading."""
        self._kill_switch_active = False
        self._daily_loss_halt = False
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
        daily_pnl = self.db.get_today_realized_pnl()

        return {
            "kill_switch": self._kill_switch_active,
            "halted": self._kill_switch_active or self._trading_halted or self._daily_loss_halt,
            "trading_halted": self._trading_halted,
            "daily_loss_halt": self._daily_loss_halt,
            "portfolio_value": portfolio_value,
            "open_positions": open_positions,
            "max_positions": self.config.max_open_positions,
            "daily_pnl": daily_pnl,
            "daily_loss_limit_pct": self.config.daily_loss_limit_pct,
        }

    def _get_portfolio_value(self) -> float:
        """Estimate portfolio value (USDC balance + open position value)."""
        try:
            usdc = self.wallet.get_usdc_balance()
        except Exception:
            usdc = 0.0

        # Add unrealized value from open positions
        positions = self.db.get_open_positions()
        position_value = sum(
            p.get("current_price", p.get("entry_price", 0)) * p.get("size", 0) for p in positions
        )

        return usdc + position_value

    def _get_strategy_exposure(self, strategy: str) -> float:
        """Get current capital deployed by a specific strategy."""
        positions = self.db.get_open_positions(strategy=strategy)
        return sum(p.get("entry_price", 0) * p.get("size", 0) for p in positions)
