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
fill for the arb to be risk-free.

Audit fixes: C-12, H-01, H-12, M-17
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
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
    yes_price: float  # Best ask from CLOB (H-12)
    no_price: float  # Best ask from CLOB (H-12)
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

    Audit fixes:
    - C-12: Rollback first leg if second leg fails (naked position protection).
    - H-01: Corrected fee calculation formula.
    - H-12: Uses live CLOB orderbook prices, not stale Gamma API.
    - M-17: Signal.size is always in USD (C-06 convention).
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

        # Fee assumptions from global config (as decimals: 0.02 = 2%)
        self._winner_fee_rate: float = self._strategy_config.winner_fee_pct / 100
        self._taker_fee_rate: float = self._strategy_config.max_taker_fee_pct / 100
        self._gas_usd: float = self._strategy_config.estimated_gas_usd

        # Track opportunities for logging/metrics
        self._total_opportunities: int = 0
        self._total_executed: int = 0
        self._opportunities_log: list[ArbOpportunity] = []

    async def initialize(self) -> None:
        """Load persisted counters from strategy state."""
        raw_opps = self.get_state("total_opportunities", 0)
        raw_exec = self.get_state("total_executed", 0)
        self._total_opportunities = int(raw_opps) if raw_opps is not None else 0  # type: ignore[call-overload]
        self._total_executed = int(raw_exec) if raw_exec is not None else 0  # type: ignore[call-overload]
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
            opportunity = await self._evaluate_market(market, max_arb_usd)
            if opportunity is None:
                continue

            # ARB-03: Log ALL opportunities
            self._log_opportunity(opportunity)

            if opportunity.executable:
                # ARB-02 + C-12: Execute arb with rollback protection
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
                            f"Yes: ${opportunity.yes_price:.4f}"
                            f" + No: ${opportunity.no_price:.4f}"
                            f" = ${opportunity.total_price:.4f}\n"
                            f"Gap: {opportunity.gap:.4f}"
                            f" ({opportunity.estimated_profit_pct:+.2f}%"
                            f" after fees)\n"
                            f"Size: ${opportunity.size_usd:.2f}"
                        ),
                        level="info",
                    )

        return signals

    async def _evaluate_market(self, market: Market, max_arb_usd: float) -> ArbOpportunity | None:
        """Check if a single market has an arb opportunity.

        H-12: Fetches live CLOB orderbook best-ask prices instead of stale Gamma.

        Returns an ArbOpportunity if yes+no < threshold, None otherwise.
        """
        # H-12: Fetch live prices from CLOB orderbook (best ask = price to buy)
        _, yes_ask = await self._client.get_best_bid_ask(market.yes_token_id)
        _, no_ask = await self._client.get_best_bid_ask(market.no_token_id)

        # Fall back to Gamma prices if orderbook is empty
        yes_price = yes_ask if yes_ask is not None else market.yes_price
        no_price = no_ask if no_ask is not None else market.no_price

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

        # H-01: Correct fee calculation
        # Taker fee applies to the dollar cost of each leg:
        #   Entry fee = size_usd * taker_fee_rate (split across both legs)
        # Winner fee applies to the $1.00 payout per unit at resolution:
        #   Winner fee = units * 1.0 * winner_fee_rate
        # Per unit: cost = yes_price + no_price, payout = $1.00
        # Per-unit taker fees = (yes_price + no_price) * taker_fee_rate
        # Per-unit winner fee = 1.0 * winner_fee_rate
        # Per-unit net profit = 1.0 - total_price - total_price * taker_fee_rate - winner_fee_rate
        per_unit_cost = total_price * (1 + self._taker_fee_rate)
        per_unit_payout = 1.0 * (1 - self._winner_fee_rate)
        per_unit_profit = per_unit_payout - per_unit_cost

        min_position_size = self._strategy_config.min_position_size_usd

        # M-17: size_usd is the total USD to spend on both legs
        size_usd = min(max_arb_usd, min_position_size * 4)  # Start conservative

        # Units = total USD / cost per unit
        units = size_usd / total_price if total_price > 0 else 0
        gas_cost = 2 * self._gas_usd
        estimated_profit_usd = per_unit_profit * units - gas_cost
        estimated_profit_pct = (per_unit_profit / per_unit_cost) * 100 if per_unit_cost > 0 else 0

        # Determine if executable
        executable = True
        reason_skipped = ""

        if per_unit_profit <= 0:
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
        """ARB-02: Create FOK buy signals for both sides.

        M-17: Signal.size is in USD (C-06 convention).
        C-12: Signals include arb_pair_id so order_manager can roll back if
        the second leg fails.  Rollback is handled in _execute_arb_pair().

        Both orders must fill for the arb to be risk-free.  Using FOK
        ensures we don't get partial fills that leave us exposed.
        """
        market = opp.market
        # M-17: Split USD evenly across both legs
        yes_usd = opp.size_usd / 2
        no_usd = opp.size_usd / 2

        # C-12: Generate a pair ID to link the two legs
        arb_pair_id = f"arb_{market.condition_id[:12]}_{int(time.time())}"

        common_metadata = {
            "arb_opportunity": True,
            "arb_pair_id": arb_pair_id,
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
            size=yes_usd,  # M-17: USD, not shares
            order_type=self._order_type,
            urgency="high",
            reasoning=f"Arb: Yes+No=${opp.total_price:.4f}, gap={opp.gap:.4f}",
            metadata={**common_metadata, "arb_side": "yes", "arb_leg": 1},
        )

        no_signal = Signal(
            strategy="arb_scanner",
            market_id=market.condition_id,
            token_id=market.no_token_id,
            side="BUY",
            price=opp.no_price,
            size=no_usd,  # M-17: USD, not shares
            order_type=self._order_type,
            urgency="high",
            reasoning=f"Arb: Yes+No=${opp.total_price:.4f}, gap={opp.gap:.4f}",
            metadata={
                **common_metadata,
                "arb_side": "no",
                "arb_leg": 2,
                # C-12: Include rollback info so order_manager can unwind leg 1
                "arb_rollback_token_id": market.yes_token_id,
                "arb_rollback_price": opp.yes_price,
                "arb_rollback_size_usd": yes_usd,
            },
        )

        return [yes_signal, no_signal]

    def _get_max_arb_size_usd(self) -> float:
        """Calculate maximum USD to allocate to a single arb trade."""
        try:
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

    def get_status(self) -> dict[str, Any]:
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
