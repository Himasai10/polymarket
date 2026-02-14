"""Polymarket Trading Bot — Main entry point.

Initializes all components, wires them together, starts the async event loop,
and handles graceful shutdown on SIGTERM/SIGINT.

Usage:
    polybot              # Run with defaults (paper mode)
    polybot --live       # Run in live trading mode
    polybot --log-level DEBUG
    polybot --status     # Print current status and exit
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys

import structlog

from .core.client import PolymarketClient
from .core.config import Settings, load_settings, load_strategy_config, load_wallet_config
from .core.db import Database
from .core.rate_limiter import RateLimiter
from .core.wallet import WalletManager
from .core.websocket import WebSocketManager
from .execution.order_manager import OrderManager
from .execution.position_manager import PositionManager
from .execution.risk_manager import RiskManager
from .monitoring.health import HealthChecker
from .monitoring.logger import setup_logging
from .monitoring.pnl import PnLTracker
from .strategies.base import BaseStrategy
from .strategies.copy_trader import CopyTrader

logger = structlog.get_logger()


class TradingBot:
    """Main trading bot orchestrator.

    Manages lifecycle of all components:
    - Core infrastructure (client, wallet, db, websocket, rate limiter)
    - Execution layer (order manager, risk manager, position manager)
    - Monitoring (pnl tracker, health checker)
    - Strategies (loaded and started based on config)
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._strategies: list[BaseStrategy] = []
        self._shutdown_event = asyncio.Event()

        # Core
        self._strategy_config = load_strategy_config()
        self._wallet_config = load_wallet_config()
        self._client = PolymarketClient(settings, self._strategy_config)
        self._wallet = WalletManager(settings)
        self._db = Database(settings)
        self._ws = WebSocketManager(settings)
        self._rate_limiter = RateLimiter()

        # Execution
        self._order_manager = OrderManager(self._client, self._db, self._rate_limiter)
        self._risk_manager = RiskManager(self._strategy_config, self._db, self._wallet)
        self._position_manager = PositionManager(
            self._strategy_config, self._db, self._order_manager
        )

        # Monitoring
        self._pnl_tracker = PnLTracker(self._db, self._wallet)
        self._health_checker = HealthChecker(self._client, self._db, self._wallet, self._ws)

        # Register strategies
        self._register_strategies()

    async def initialize(self) -> None:
        """Initialize all components in order."""
        logger.info(
            "bot_initializing",
            trading_mode=self._settings.trading_mode,
            log_level=self._settings.log_level,
        )

        # 1. Database first (everything depends on it)
        self._db.initialize()
        logger.info("database_initialized")

        # 2. API client
        await self._client.initialize()
        logger.info("api_client_initialized")

        # 3. Wallet
        self._wallet.initialize()
        balance = self._wallet.get_usdc_balance()
        logger.info("wallet_initialized", usdc_balance=balance)

        if balance < 1.0:
            logger.warning("low_balance", usdc_balance=balance)

        # 4. P&L tracker (needs db + wallet)
        await self._pnl_tracker.initialize()

        # 5. Register position manager as WebSocket price callback
        self._ws.register_callback(self._position_manager.on_price_update)

        # 6. Subscribe to tokens for open positions
        positions = self._db.get_open_positions()
        if positions:
            token_ids = [p["token_id"] for p in positions]
            self._ws.subscribe(token_ids)
            logger.info("ws_subscribed_existing_positions", count=len(token_ids))

        logger.info("bot_initialized")

    def register_strategy(self, strategy: BaseStrategy) -> None:
        """Register a strategy with the bot."""
        self._strategies.append(strategy)
        logger.info("strategy_registered", strategy=strategy.name)

    def _register_strategies(self) -> None:
        """Auto-register strategies based on config."""
        # Copy Trader (Phase 2)
        if self._strategy_config.is_strategy_enabled("copy_trader"):
            copy_trader = CopyTrader(
                client=self._client,
                db=self._db,
                order_manager=self._order_manager,
                risk_manager=self._risk_manager,
                strategy_config=self._strategy_config,
                wallet_config=self._wallet_config,
                wallet_manager=self._wallet,
                ws_manager=self._ws,
            )
            self.register_strategy(copy_trader)
        else:
            logger.info("strategy_disabled_in_config", strategy="copy_trader")

    async def start(self) -> None:
        """Start all components and run until shutdown."""
        await self.initialize()

        # Collect all long-running tasks
        tasks: list[asyncio.Task] = []

        # Start WebSocket
        ws_task = asyncio.create_task(self._ws.start())
        tasks.append(ws_task)

        # Start order processing loop
        order_task = asyncio.create_task(self._order_manager.process_signals())
        tasks.append(order_task)

        # Start all registered strategies
        for strategy in self._strategies:
            await strategy.start()

        # Start periodic P&L logging
        pnl_task = asyncio.create_task(self._pnl_loop())
        tasks.append(pnl_task)

        # Start health check loop
        health_task = asyncio.create_task(self._health_loop())
        tasks.append(health_task)

        logger.info(
            "bot_started",
            strategies=[s.name for s in self._strategies],
            mode=self._settings.trading_mode,
        )

        # Wait for shutdown signal
        await self._shutdown_event.wait()

        # Shutdown sequence
        await self.shutdown()

        # Cancel remaining tasks
        for task in tasks:
            if not task.done():
                task.cancel()

        await asyncio.gather(*tasks, return_exceptions=True)

    async def shutdown(self) -> None:
        """Graceful shutdown: cancel orders, save state, close connections."""
        logger.info("bot_shutting_down")

        # 1. Stop all strategies (saves state)
        for strategy in self._strategies:
            try:
                await strategy.stop()
            except Exception:
                logger.exception("strategy_stop_error", strategy=strategy.name)

        # 2. Cancel all open orders (safety first)
        if self._settings.is_live:
            try:
                await self._order_manager.cancel_all()
                logger.info("open_orders_cancelled")
            except Exception:
                logger.exception("cancel_orders_error")

        # 3. Stop order manager
        await self._order_manager.stop()

        # 4. Stop WebSocket
        await self._ws.stop()

        # 5. Final P&L snapshot
        try:
            self._pnl_tracker.log_snapshot()
        except Exception:
            logger.exception("final_pnl_snapshot_error")

        # 6. Close API client
        await self._client.close()

        # 7. Close database last
        self._db.close()

        logger.info("bot_shutdown_complete")

    def request_shutdown(self) -> None:
        """Request a graceful shutdown (called from signal handlers)."""
        logger.info("shutdown_requested")
        self._shutdown_event.set()

    async def get_status(self) -> dict:
        """Get full bot status for CLI or Telegram."""
        health = await self._health_checker.get_system_health()
        pnl = self._pnl_tracker.get_snapshot()

        return {
            "mode": self._settings.trading_mode,
            "health": health.to_dict(),
            "portfolio": {
                "value": pnl.portfolio_value,
                "usdc": pnl.usdc_balance,
                "positions_value": pnl.positions_value,
                "daily_return_pct": pnl.daily_return_pct,
                "realized_today": pnl.realized_pnl_today,
                "unrealized": pnl.unrealized_pnl,
                "open_positions": pnl.open_position_count,
            },
            "strategies": [s.get_status() for s in self._strategies],
            "risk": self._risk_manager.get_status(),
            "order_queue": self._order_manager.get_pending_count(),
        }

    # --- Internal loops ---

    async def _pnl_loop(self) -> None:
        """Periodically log P&L snapshots."""
        while not self._shutdown_event.is_set():
            try:
                self._pnl_tracker.log_snapshot()
            except Exception:
                logger.exception("pnl_snapshot_error")

            try:
                await asyncio.wait_for(
                    self._shutdown_event.wait(),
                    timeout=300.0,  # Every 5 minutes
                )
                break  # Shutdown requested
            except asyncio.TimeoutError:
                continue

    async def _health_loop(self) -> None:
        """Periodically run health checks."""
        while not self._shutdown_event.is_set():
            try:
                health = await self._health_checker.get_system_health()
                if not health.is_healthy:
                    logger.warning(
                        "system_unhealthy",
                        overall=health.overall.value,
                        components={c.name: c.status.value for c in health.components},
                    )
            except Exception:
                logger.exception("health_check_error")

            try:
                await asyncio.wait_for(
                    self._shutdown_event.wait(),
                    timeout=60.0,  # Every minute
                )
                break
            except asyncio.TimeoutError:
                continue


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Polymarket Trading Bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Run in live trading mode (default: paper)",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Override log level from settings",
    )
    parser.add_argument(
        "--json-logs",
        action="store_true",
        default=True,
        help="Output JSON formatted logs (default: True)",
    )
    parser.add_argument(
        "--no-json-logs",
        action="store_false",
        dest="json_logs",
        help="Output human-readable console logs",
    )
    parser.add_argument(
        "--log-file",
        default=None,
        help="Path to log file (in addition to stdout)",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Print current bot status and exit",
    )
    parser.add_argument(
        "--kill",
        action="store_true",
        help="Activate kill switch: cancel all open orders and exit",
    )
    return parser.parse_args()


def main() -> None:
    """CLI entry point for the trading bot."""
    args = parse_args()

    # Load settings
    try:
        settings = load_settings()
    except Exception as e:
        print(f"Error loading settings: {e}", file=sys.stderr)
        print("Run 'python -m scripts.setup_account' to configure the bot.", file=sys.stderr)
        sys.exit(1)

    # CLI overrides
    if args.live:
        settings.trading_mode = "live"
    if args.log_level:
        settings.log_level = args.log_level

    # Setup logging
    setup_logging(
        log_level=settings.log_level,
        json_output=args.json_logs,
        log_file=args.log_file or "data/polybot.log",
    )

    # Startup banner
    logger.info(
        "polymarket_bot",
        version="0.1.0",
        mode=settings.trading_mode,
        log_level=settings.log_level,
    )

    if settings.is_live:
        logger.warning("LIVE_TRADING_MODE — real money at risk")

    # Create and run bot
    bot = TradingBot(settings)

    # Register signal handlers for graceful shutdown
    loop = asyncio.new_event_loop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, bot.request_shutdown)

    try:
        if args.status:
            # Just print status and exit
            status = loop.run_until_complete(_print_status(bot))
        elif args.kill:
            # Activate kill switch: cancel all orders and halt trading
            loop.run_until_complete(_execute_kill_switch(bot))
        else:
            loop.run_until_complete(bot.start())
    except KeyboardInterrupt:
        logger.info("keyboard_interrupt")
    finally:
        loop.close()


async def _print_status(bot: TradingBot) -> None:
    """Initialize enough to print status, then exit."""
    await bot.initialize()
    status = await bot.get_status()

    import json

    print(json.dumps(status, indent=2, default=str))

    await bot._client.close()
    bot._db.close()


async def _execute_kill_switch(bot: TradingBot) -> None:
    """Activate kill switch: cancel all open orders, halt trading, and exit.

    This is the emergency stop — cancels everything and sets the kill switch
    flag so the bot won't resume trading on next start without manual reset.
    """
    logger.warning("KILL_SWITCH_ACTIVATED — cancelling all orders")

    # Initialize just enough to cancel orders
    bot._db.initialize()
    await bot._client.initialize()

    # Activate the kill switch in risk manager (persists state)
    bot._risk_manager.activate_kill_switch()
    logger.info("kill_switch_set", msg="Risk manager kill switch activated")

    # Cancel all open orders
    try:
        result = await bot._order_manager.cancel_all()
        if result:
            logger.info("all_orders_cancelled")
        else:
            logger.warning("cancel_orders_may_have_failed")
    except Exception:
        logger.exception("cancel_orders_error")

    # Print summary
    open_positions = bot._db.get_open_positions()
    print(f"\nKill switch activated.")
    print(f"  Open positions remaining: {len(open_positions)}")
    print(f"  Kill switch is now ON — bot will not trade until manually reset.")
    print(f"  To resume: clear the kill switch in config or restart with fresh state.\n")

    # Cleanup
    await bot._client.close()
    bot._db.close()


if __name__ == "__main__":
    main()
