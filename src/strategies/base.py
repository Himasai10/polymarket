"""Abstract base class for all trading strategies.

All strategies inherit from BaseStrategy and implement the same lifecycle:
1. initialize() — load state, set up subscriptions
2. evaluate() — scan for opportunities, emit Signals
3. on_price_update() — react to real-time price changes (optional)
4. shutdown() — persist state, clean up

Strategies NEVER place orders directly. They emit Signal objects that flow
through the execution layer (RiskManager -> OrderManager -> CLOB).
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod

import structlog

from ..core.client import Market, PolymarketClient
from ..core.config import StrategyConfig
from ..core.db import Database
from ..execution.order_manager import OrderManager, Signal
from ..execution.risk_manager import RiskManager

logger = structlog.get_logger()


class BaseStrategy(ABC):
    """Abstract base class for trading strategies.

    Each strategy has:
    - A unique name (used for config lookup, logging, DB state)
    - Access to shared infrastructure (client, db, order manager, risk manager)
    - Its own evaluation loop running at a configurable interval
    - Persistent state saved/loaded from the database across restarts
    """

    def __init__(
        self,
        name: str,
        client: PolymarketClient,
        db: Database,
        order_manager: OrderManager,
        risk_manager: RiskManager,
        strategy_config: StrategyConfig,
    ) -> None:
        self.name = name
        self._client = client
        self._db = db
        self._order_manager = order_manager
        self._risk_manager = risk_manager
        self._strategy_config = strategy_config

        self._running = False
        self._eval_task: asyncio.Task | None = None
        self._state: dict = {}

        # Get strategy-specific config section
        self._config = self._strategy_config.get_strategy(name) or {}
        self._enabled = self._strategy_config.is_strategy_enabled(name)
        self._eval_interval = self._config.get("eval_interval_seconds", 60)

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    # --- Lifecycle methods ---

    async def start(self) -> None:
        """Start the strategy's evaluation loop."""
        if not self._enabled:
            logger.info("strategy_disabled", strategy=self.name)
            return

        # Load persisted state
        saved = self._db.load_strategy_state(self.name)
        if saved:
            self._state = saved
            logger.info("strategy_state_loaded", strategy=self.name, state_keys=list(saved.keys()))

        await self.initialize()

        self._running = True
        self._eval_task = asyncio.create_task(self._evaluation_loop())
        logger.info(
            "strategy_started",
            strategy=self.name,
            eval_interval=self._eval_interval,
        )

    async def stop(self) -> None:
        """Stop the strategy and persist state."""
        self._running = False
        if self._eval_task and not self._eval_task.done():
            self._eval_task.cancel()
            try:
                await self._eval_task
            except asyncio.CancelledError:
                pass

        # Persist state for restart
        if self._state:
            self._db.save_strategy_state(self.name, self._state)
            logger.info("strategy_state_saved", strategy=self.name)

        await self.shutdown()
        logger.info("strategy_stopped", strategy=self.name)

    def pause(self) -> None:
        """Pause the strategy (stops evaluation loop but keeps state)."""
        self._running = False
        logger.info("strategy_paused", strategy=self.name)

    def resume(self) -> None:
        """Resume a paused strategy."""
        if not self._enabled:
            logger.warning("strategy_resume_disabled", strategy=self.name)
            return
        self._running = True
        if self._eval_task is None or self._eval_task.done():
            self._eval_task = asyncio.create_task(self._evaluation_loop())
        logger.info("strategy_resumed", strategy=self.name)

    # --- Abstract methods (implement in subclasses) ---

    @abstractmethod
    async def initialize(self) -> None:
        """Initialize strategy-specific resources.

        Called once during start(). Use this to:
        - Set up market subscriptions
        - Load historical data
        - Initialize internal state
        """

    @abstractmethod
    async def evaluate(self) -> list[Signal]:
        """Run one evaluation cycle and return signals.

        This is the core strategy logic. Called at the configured interval.

        Returns:
            List of Signal objects to submit to the execution layer.
            Return an empty list if no opportunities found.
        """

    async def on_price_update(self, token_id: str, price: float, timestamp: float) -> None:
        """React to real-time price updates from WebSocket.

        Override this in strategies that need to react immediately to price changes
        (e.g., arbitrage). Default implementation does nothing.

        Args:
            token_id: The token whose price changed.
            price: New best bid/ask price.
            timestamp: Unix timestamp of the update.
        """

    async def shutdown(self) -> None:
        """Clean up strategy-specific resources.

        Called once during stop(). Override to close connections, cancel
        subscriptions, etc. Default implementation does nothing.
        """

    # --- Signal submission ---

    async def emit_signal(self, signal: Signal) -> bool:
        """Submit a signal through the risk manager and order manager.

        This is the ONLY way a strategy should attempt to place a trade.
        Never call the order manager or client directly.

        Args:
            signal: The trading signal to submit.

        Returns:
            True if the signal was approved and queued, False if rejected.
        """
        # Risk check
        approved, reason = self._risk_manager.approve_signal(signal)
        if not approved:
            logger.warning(
                "signal_rejected",
                strategy=self.name,
                market_id=signal.market_id,
                side=signal.side,
                price=signal.price,
                size=signal.size,
                reason=reason,
            )
            return False

        # Queue for execution
        await self._order_manager.submit_signal(signal)
        logger.info(
            "signal_emitted",
            strategy=self.name,
            market_id=signal.market_id,
            token_id=signal.token_id,
            side=signal.side,
            price=signal.price,
            size=signal.size,
            reasoning=signal.reasoning,
        )
        return True

    # --- Helper methods ---

    async def get_active_markets(
        self,
        min_volume: float = 0,
        min_liquidity: float = 0,
        category: str | None = None,
    ) -> list[Market]:
        """Fetch active markets from the Gamma API with filters."""
        return await self._client.get_markets(
            active=True,
            min_volume=min_volume,
            min_liquidity=min_liquidity,
            category=category,
        )

    def get_open_positions(self) -> list[dict]:
        """Get this strategy's currently open positions."""
        return self._db.get_open_positions(strategy=self.name)

    def get_state(self, key: str, default: object = None) -> object:
        """Get a value from persistent strategy state."""
        return self._state.get(key, default)

    def set_state(self, key: str, value: object) -> None:
        """Set a value in persistent strategy state.

        State is automatically persisted on stop/restart.
        """
        self._state[key] = value

    def get_status(self) -> dict:
        """Get strategy status for health/status reporting."""
        return {
            "name": self.name,
            "enabled": self._enabled,
            "running": self._running,
            "eval_interval": self._eval_interval,
            "open_positions": len(self.get_open_positions()),
            "allocation_pct": self._strategy_config.get_strategy_allocation(self.name),
        }

    # --- Internal ---

    async def _evaluation_loop(self) -> None:
        """Main loop that calls evaluate() at the configured interval."""
        logger.info("eval_loop_started", strategy=self.name, interval=self._eval_interval)

        while self._running:
            try:
                signals = await self.evaluate()

                for signal in signals:
                    await self.emit_signal(signal)

                if signals:
                    logger.info(
                        "eval_cycle_complete",
                        strategy=self.name,
                        signals_emitted=len(signals),
                    )

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("eval_cycle_error", strategy=self.name)

            # Wait for next cycle
            try:
                await asyncio.sleep(self._eval_interval)
            except asyncio.CancelledError:
                break

        logger.info("eval_loop_stopped", strategy=self.name)
