"""Parity arbitrage scanner strategy.

Addresses: ARB-01 (scan), ARB-02 (execute FOK), ARB-03 (log all opportunities)

Parity arbitrage on Polymarket: every binary market has a Yes token and a No
token.  In theory Yes + No = $1.00.  Due to the 2% winner fee and taker fees,
profitable arbitrage exists when:

    yes_price + no_price < 1.0 - total_fees

For example with 2% winner fee + ~3.15% taker fee ≈ 5.15% round-trip cost,
arbitrage is profitable when yes + no < $0.9485.  The config exposes a
`min_gap_threshold` (default 0.95) that the operator can tune.

Execution: when an opportunity is found, we submit two FOK (Fill-or-Kill) buy
orders — one for Yes, one for No — within the same evaluation cycle.  Both must
fill for the arb to be risk-free.  If only one fills, the position manager will
handle exit via normal TP/SL rules.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import structlog

from ..core.client import Market, PolymarketClient
from ..core.config import StrategyConfig
from ..core.db import Database
from ..execution.order_manager import OrderManager, Signal
from ..execution.risk_manager import RiskManager
from .base import BaseStrategy

if TYPE_CHECKING:
    from ..notifications.telegram import TelegramNotifier

logger = structlog.get_logger()


@dataclass
class ArbOpportunity:
    """A detected arbitrage opportunity."""

    market: Market
    yes_price: float
    no_price: float
    total_price: float
    gap: float  # 1.0 - total_price (positive = arb exists)
    estimated_profit_pct: float  # gap minus fees
    estimated_profit_usd: float
    size_usd: float
    executable: bool  # True if profit > 0 after fees and size > min
    timestamp: float = field(default_factory=time.time)
    reason_skipped: str = ""


class ArbScanner(BaseStrategy):
    """Scans all active binary markets for parity arbitrage.

    ARB-01: Continuous scanning of all markets for Yes+No < threshold
    ARB-02: Simultaneous FOK orders on both sides
    ARB-03: Logs all detected opportunities (including too-small-to-execute)
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
            name="arb_scanner",
            client=client,
            db=db,
            order_manager=order_manager,
            risk_manager=risk_manager,
            strategy_config=strategy_config,
        )
        self._notifier = notifier

        # Config from strategies.yaml -> strategies.arb_scanner
        self._min_gap_threshold: float = self._config.get("min_gap_threshold", 0.95)
        self._order_type: str = self._config.get("order_type", "FOK")
        self._allocation_pct: float = self._config.get("allocation_pct", 10.0)

        # Override eval interval with scan_interval_sec from config
        self._eval_interval = self._config.get("scan_interval_sec", 60)

        # Fee assumptions from global config
        self._winner_fee_pct: float = self._strategy_config.winner_fee_pct
        self._taker_fee_pct: float = self._strategy_config.max_taker_fee_pct
        self._gas_usd: float = self._strategy_config.estimated_gas_usd

        # Track opportunities for logging/metrics
        self._total_opportunities: int = 0
        self._total_executed: int = 0
        self._opportunities_log: list[ArbOpportunity] = []

    async def initialize(self) -> None:
        """Load persisted counters from strategy state."""
        self._total_opportunities = int(self.get_state("total_opportunities", 0))
        self._total_executed = int(self.get_state("total_executed", 0))
        logger.info(
            "arb_scanner_initialized",
            min_gap_threshold=self._min_gap_threshold,
            eval_interval=self._eval_interval,
            order_type=self._order_type,
            allocation_pct=self._allocation_pct,
            historical_opportunities=self._total_opportunities,
            historical_executed=self._total_executed,
        )

    async def evaluate(self) -> list[Signal]:
        """ARB-01: Scan all active markets for parity arbitrage.

        Returns FOK buy signals for both sides of any arb opportunity.
        """
        signals: list[Signal] = []

        # Fetch active binary markets
        markets = await self.get_active_markets()
        if not markets:
            logger.debug("arb_no_markets")
            return signals

        # Calculate max position size from allocation
        max_arb_usd = self._get_max_arb_size_usd()

        for market in markets:
            opportunity = self._evaluate_market(market, max_arb_usd)
            if opportunity is None:
                continue

            # ARB-03: Log ALL opportunities
            self._log_opportunity(opportunity)

            if opportunity.executable:
                # ARB-02: Generate simultaneous FOK signals for both sides
                arb_signals = self._create_arb_signals(opportunity)
                signals.extend(arb_signals)
                self._total_executed += 1
                self.set_state("total_executed", self._total_executed)

                # Telegram alert for arb detection
                if self._notifier:
                    await self._notifier.alert_system(
                        title="Arbitrage Detected",
                        message=(
                            f"Market: {market.question[:60]}\n"
                            f"Yes: ${opportunity.yes_price:.4f} + No: ${opportunity.no_price:.4f} = ${opportunity.total_price:.4f}\n"
                            f"Gap: {opportunity.gap:.4f} ({opportunity.estimated_profit_pct:+.2f}% after fees)\n"
                            f"Size: ${opportunity.size_usd:.2f}"
                        ),
                        level="info",
                    )

        return signals

    def _evaluate_market(self, market: Market, max_arb_usd: float) -> ArbOpportunity | None:
        """Check if a single market has an arb opportunity.

        Returns an ArbOpportunity if yes+no < threshold, None otherwise.
        """
        yes_price = market.yes_price
        no_price = market.no_price

        # Skip markets with no valid pricing
        if yes_price <= 0 or no_price <= 0:
            return None
        if yes_price >= 1.0 or no_price >= 1.0:
            return None

        total_price = yes_price + no_price

        # ARB-01: Check if total < threshold
        if total_price >= self._min_gap_threshold:
            return None

        # There's a gap — calculate profitability
        gap = 1.0 - total_price

        # Fee calculation:
        # - Taker fee on entry (both sides): 2 * taker_fee_pct * cost
        # - Winner fee on the winning side: winner_fee_pct * $1.00 payout
        # - Gas for 2 transactions
        total_fee_pct = (2 * self._taker_fee_pct / 100) + (self._winner_fee_pct / 100)
        gas_cost = 2 * self._gas_usd

        estimated_profit_pct = (gap - total_fee_pct) * 100
        min_position_size = self._strategy_config.min_position_size_usd

        # Size: buy equal dollar amounts of both sides
        # The position size is the total cost (yes_price + no_price) per unit
        # Max units we can buy:
        size_usd = min(max_arb_usd, min_position_size * 4)  # Start conservative

        estimated_profit_usd = (gap - total_fee_pct) * (size_usd / total_price) - gas_cost

        # Determine if executable
        executable = True
        reason_skipped = ""

        if estimated_profit_pct <= 0:
            executable = False
            reason_skipped = f"negative_profit_after_fees ({estimated_profit_pct:.2f}%)"
        elif estimated_profit_usd < 0.50:
            executable = False
            reason_skipped = f"profit_too_small (${estimated_profit_usd:.2f})"
        elif size_usd < min_position_size:
            executable = False
            reason_skipped = f"below_min_position (${size_usd:.2f} < ${min_position_size:.2f})"

        self._total_opportunities += 1
        self.set_state("total_opportunities", self._total_opportunities)

        return ArbOpportunity(
            market=market,
            yes_price=yes_price,
            no_price=no_price,
            total_price=total_price,
            gap=gap,
            estimated_profit_pct=estimated_profit_pct,
            estimated_profit_usd=estimated_profit_usd,
            size_usd=size_usd,
            executable=executable,
            reason_skipped=reason_skipped,
        )

    def _create_arb_signals(self, opp: ArbOpportunity) -> list[Signal]:
        """ARB-02: Create simultaneous FOK buy signals for both sides.

        Both orders must fill for the arb to be risk-free.  Using FOK
        ensures we don't get partial fills that leave us exposed.
        """
        market = opp.market
        # Split the dollar size equally across both sides
        # Number of units (shares) for each side
        yes_units = opp.size_usd / 2 / opp.yes_price
        no_units = opp.size_usd / 2 / opp.no_price

        common_metadata = {
            "arb_opportunity": True,
            "total_price": opp.total_price,
            "gap": opp.gap,
            "estimated_profit_pct": opp.estimated_profit_pct,
            "estimated_profit_usd": opp.estimated_profit_usd,
            "market_question": market.question,
        }

        yes_signal = Signal(
            strategy="arb_scanner",
            market_id=market.condition_id,
            token_id=market.yes_token_id,
            side="BUY",
            price=opp.yes_price,
            size=round(yes_units, 2),
            order_type=self._order_type,
            urgency="high",
            reasoning=f"Arb: Yes+No=${opp.total_price:.4f}, gap={opp.gap:.4f}",
            metadata={**common_metadata, "arb_side": "yes"},
        )

        no_signal = Signal(
            strategy="arb_scanner",
            market_id=market.condition_id,
            token_id=market.no_token_id,
            side="BUY",
            price=opp.no_price,
            size=round(no_units, 2),
            order_type=self._order_type,
            urgency="high",
            reasoning=f"Arb: Yes+No=${opp.total_price:.4f}, gap={opp.gap:.4f}",
            metadata={**common_metadata, "arb_side": "no"},
        )

        return [yes_signal, no_signal]

    def _get_max_arb_size_usd(self) -> float:
        """Calculate maximum USD to allocate to a single arb trade."""
        # Get total portfolio value (approximation: use USDC balance)
        try:
            from ..core.wallet import WalletManager

            # Use the strategy config allocation as a cap
            # The actual portfolio value would come from the wallet manager,
            # but we don't have a direct reference here.  Use min_position_size
            # as a floor and a conservative fixed max.
            min_size = self._strategy_config.min_position_size_usd
            # Conservative: max $200 per arb trade (operator can tune via config)
            return max(min_size * 2, 200.0)
        except Exception:
            return 100.0

    def _log_opportunity(self, opp: ArbOpportunity) -> None:
        """ARB-03: Log all detected opportunities for analysis."""
        # Keep last 100 in memory for status reporting
        self._opportunities_log.append(opp)
        if len(self._opportunities_log) > 100:
            self._opportunities_log = self._opportunities_log[-100:]

        logger.info(
            "arb_opportunity_detected",
            market=opp.market.question[:60],
            condition_id=opp.market.condition_id[:16],
            yes_price=opp.yes_price,
            no_price=opp.no_price,
            total_price=round(opp.total_price, 4),
            gap=round(opp.gap, 4),
            profit_pct=round(opp.estimated_profit_pct, 2),
            profit_usd=round(opp.estimated_profit_usd, 2),
            executable=opp.executable,
            reason_skipped=opp.reason_skipped,
        )

    async def shutdown(self) -> None:
        """Persist state on shutdown."""
        logger.info(
            "arb_scanner_shutdown",
            total_opportunities=self._total_opportunities,
            total_executed=self._total_executed,
        )

    def get_status(self) -> dict:
        """Extended status for health reporting."""
        base = super().get_status()
        base.update(
            {
                "total_opportunities": self._total_opportunities,
                "total_executed": self._total_executed,
                "min_gap_threshold": self._min_gap_threshold,
                "recent_opportunities": len(self._opportunities_log),
            }
        )
        return base
