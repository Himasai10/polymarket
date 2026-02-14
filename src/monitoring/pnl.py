"""P&L tracking and reporting.

Computes portfolio value, daily P&L, per-strategy P&L, and generates summaries
for logging and Telegram reporting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone

import structlog

from ..core.db import Database
from ..core.wallet import WalletManager

logger = structlog.get_logger()


@dataclass
class PnLSnapshot:
    """Point-in-time P&L data."""

    timestamp: datetime
    portfolio_value: float
    usdc_balance: float
    positions_value: float
    unrealized_pnl: float
    realized_pnl_today: float
    daily_return_pct: float
    open_position_count: int
    per_strategy: dict[str, StrategyPnL] = field(default_factory=dict)


@dataclass
class StrategyPnL:
    """P&L breakdown for a single strategy."""

    strategy: str
    exposure: float
    unrealized_pnl: float
    realized_pnl: float
    trade_count: int
    win_count: int
    loss_count: int

    @property
    def win_rate(self) -> float:
        """Win rate as a percentage."""
        total = self.win_count + self.loss_count
        if total == 0:
            return 0.0
        return (self.win_count / total) * 100.0


class PnLTracker:
    """Tracks and reports portfolio P&L.

    Aggregates data from the database and wallet to produce real-time
    P&L snapshots and daily summaries.
    """

    def __init__(self, db: Database, wallet: WalletManager) -> None:
        self._db = db
        self._wallet = wallet
        self._starting_balance: float | None = None

    async def initialize(self) -> None:
        """Load or set today's starting balance."""
        today = date.today().isoformat()
        daily = self._db.get_daily_pnl(today)
        if daily:
            self._starting_balance = daily["starting_balance"]
            logger.info("pnl_tracker_initialized", starting_balance=self._starting_balance)
        else:
            # First run today — record current portfolio value as starting balance
            balance = self._wallet.get_usdc_balance()
            positions = self._db.get_open_positions()
            positions_value = sum(
                (p["current_price"] or p["entry_price"]) * p["size"] for p in positions
            )
            self._starting_balance = balance + positions_value
            self._db.record_daily_pnl(today, self._starting_balance)
            logger.info(
                "pnl_tracker_new_day",
                starting_balance=self._starting_balance,
                usdc=balance,
                positions_value=positions_value,
            )

    def get_snapshot(self) -> PnLSnapshot:
        """Get current P&L snapshot."""
        usdc_balance = self._wallet.get_usdc_balance()
        positions = self._db.get_open_positions()

        positions_value = 0.0
        unrealized_pnl = 0.0
        per_strategy: dict[str, StrategyPnL] = {}

        for pos in positions:
            pos_value = (pos["current_price"] or pos["entry_price"]) * pos["size"]
            pos_unrealized = pos.get("unrealized_pnl", 0.0)
            positions_value += pos_value
            unrealized_pnl += pos_unrealized

            strategy = pos["strategy"]
            if strategy not in per_strategy:
                per_strategy[strategy] = StrategyPnL(
                    strategy=strategy,
                    exposure=0.0,
                    unrealized_pnl=0.0,
                    realized_pnl=0.0,
                    trade_count=0,
                    win_count=0,
                    loss_count=0,
                )
            per_strategy[strategy].exposure += pos_value
            per_strategy[strategy].unrealized_pnl += pos_unrealized

        # Add realized P&L from closed trades today
        realized_today = self._db.get_today_realized_pnl()

        # Compute daily return
        portfolio_value = usdc_balance + positions_value
        starting = self._starting_balance or portfolio_value
        daily_return_pct = (
            ((portfolio_value - starting) / starting * 100.0) if starting > 0 else 0.0
        )

        # Enrich per-strategy with realized P&L from trades
        self._enrich_strategy_pnl(per_strategy)

        return PnLSnapshot(
            timestamp=datetime.now(timezone.utc),
            portfolio_value=portfolio_value,
            usdc_balance=usdc_balance,
            positions_value=positions_value,
            unrealized_pnl=unrealized_pnl,
            realized_pnl_today=realized_today,
            daily_return_pct=round(daily_return_pct, 4),
            open_position_count=len(positions),
            per_strategy=per_strategy,
        )

    def _enrich_strategy_pnl(self, per_strategy: dict[str, StrategyPnL]) -> None:
        """Add realized P&L and win/loss counts from today's closed positions."""
        # Use closed positions (not trades) for accurate realized P&L
        closed_positions = self._db.get_closed_positions(limit=500)
        today = date.today().isoformat()

        for pos in closed_positions:
            # Only count positions closed today
            closed_at = pos.get("closed_at", "")
            if not closed_at.startswith(today):
                continue

            strategy = pos["strategy"]
            if strategy not in per_strategy:
                per_strategy[strategy] = StrategyPnL(
                    strategy=strategy,
                    exposure=0.0,
                    unrealized_pnl=0.0,
                    realized_pnl=0.0,
                    trade_count=0,
                    win_count=0,
                    loss_count=0,
                )

            realized_pnl = pos.get("realized_pnl", 0.0)
            per_strategy[strategy].trade_count += 1
            per_strategy[strategy].realized_pnl += realized_pnl
            if realized_pnl > 0:
                per_strategy[strategy].win_count += 1
            elif realized_pnl < 0:
                per_strategy[strategy].loss_count += 1

    def format_summary(self, snapshot: PnLSnapshot | None = None) -> str:
        """Format a human-readable P&L summary for logging or Telegram.

        Args:
            snapshot: Optional pre-computed snapshot. If None, computes fresh.

        Returns:
            Multi-line formatted summary string.
        """
        if snapshot is None:
            snapshot = self.get_snapshot()

        lines = [
            "--- P&L Summary ---",
            f"Portfolio: ${snapshot.portfolio_value:,.2f}",
            f"  USDC: ${snapshot.usdc_balance:,.2f}",
            f"  Positions: ${snapshot.positions_value:,.2f} ({snapshot.open_position_count} open)",
            f"Daily Return: {snapshot.daily_return_pct:+.2f}%",
            f"  Realized: ${snapshot.realized_pnl_today:+,.2f}",
            f"  Unrealized: ${snapshot.unrealized_pnl:+,.2f}",
        ]

        if snapshot.per_strategy:
            lines.append("Per Strategy:")
            for name, spnl in snapshot.per_strategy.items():
                lines.append(
                    f"  {name}: exposure=${spnl.exposure:,.2f} "
                    f"unrealized={spnl.unrealized_pnl:+,.2f} "
                    f"trades={spnl.trade_count} "
                    f"win_rate={spnl.win_rate:.0f}%"
                )

        lines.append("---")
        return "\n".join(lines)

    def log_snapshot(self) -> PnLSnapshot:
        """Take a snapshot and log it."""
        snapshot = self.get_snapshot()
        logger.info(
            "pnl_snapshot",
            portfolio_value=snapshot.portfolio_value,
            usdc_balance=snapshot.usdc_balance,
            positions_value=snapshot.positions_value,
            unrealized_pnl=snapshot.unrealized_pnl,
            realized_pnl_today=snapshot.realized_pnl_today,
            daily_return_pct=snapshot.daily_return_pct,
            open_positions=snapshot.open_position_count,
        )
        return snapshot

    def check_daily_loss_limit(self, limit_pct: float) -> bool:
        """Check if daily loss exceeds the configured limit.

        Args:
            limit_pct: Maximum allowable daily loss as a positive percentage (e.g. 10.0).

        Returns:
            True if within limit (safe), False if limit breached.
        """
        snapshot = self.get_snapshot()
        return snapshot.daily_return_pct >= -abs(limit_pct)
