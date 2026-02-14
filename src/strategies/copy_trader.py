"""Copy Trading Strategy — Track whale wallets and replicate their trades.

Addresses: COPY-01 through COPY-06
- COPY-01: Track target wallets from wallets.yaml via Data API polling
- COPY-02: Detect new whale positions by diffing against stored state
- COPY-03: Configurable sizing (fixed $, % portfolio, % whale)
- COPY-04: Conviction filter (skip if whale's position < threshold)
- COPY-05: Slippage protection (skip if price moved >X% from whale entry)
- COPY-06: Per-wallet performance tracking

Audit fixes applied:
- H-10: Generate SELL signals when whale reduces/exits a position
- H-11: Conviction filter uses current value (shares * live price), not cost basis
- M-18: Portfolio value uses live prices for position valuation
"""

from __future__ import annotations

import json
from typing import Any

import structlog

from ..core.client import PolymarketClient
from ..core.config import StrategyConfig, WalletConfig
from ..core.db import Database
from ..core.wallet import WalletManager
from ..core.websocket import WebSocketManager
from ..execution.order_manager import OrderManager, Signal
from ..execution.risk_manager import RiskManager
from .base import BaseStrategy

logger = structlog.get_logger()

# Thresholds for whale position change detection
_POSITION_INCREASE_THRESHOLD = 1.10  # 10% increase triggers copy BUY
_POSITION_DECREASE_THRESHOLD = 0.70  # 30% decrease triggers copy SELL
_MIN_EXIT_SIZE_USD = 10.0  # Minimum USD value to bother generating an exit signal


class CopyTrader(BaseStrategy):
    """Copy trading strategy that tracks profitable whale wallets.

    Lifecycle:
    1. initialize() — Load saved whale positions from DB, validate wallet config
    2. evaluate() — Poll Data API for each tracked wallet, detect new/exited positions,
       apply conviction + slippage filters, emit copy signals (BUY and SELL)
    3. shutdown() — Save state for next restart
    """

    def __init__(
        self,
        client: PolymarketClient,
        db: Database,
        order_manager: OrderManager,
        risk_manager: RiskManager,
        strategy_config: StrategyConfig,
        wallet_config: WalletConfig,
        wallet_manager: WalletManager,
        ws_manager: WebSocketManager,
    ) -> None:
        super().__init__(
            name="copy_trader",
            client=client,
            db=db,
            order_manager=order_manager,
            risk_manager=risk_manager,
            strategy_config=strategy_config,
        )
        self._wallet_config = wallet_config
        self._wallet_manager = wallet_manager
        self._ws_manager = ws_manager

        # Strategy-specific config from strategies.yaml
        self._sizing_method: str = self._config.get("sizing_method", "fixed")
        self._fixed_size_usd: float = self._config.get("fixed_size_usd", 50.0)
        self._portfolio_pct_per_trade: float = self._config.get("portfolio_pct_per_trade", 5.0)
        self._whale_pct: float = self._config.get("whale_pct", 10.0)
        self._min_whale_position_usd: float = self._config.get("min_whale_position_usd", 500.0)
        self._max_slippage_pct: float = self._config.get("max_slippage_pct", 5.0)
        self._poll_interval: int = self._config.get("poll_interval_sec", 30)
        self._order_type: str = self._config.get("order_type", "GTC")

        # Override eval interval from base class with poll interval
        self._eval_interval = self._poll_interval

        # In-memory cache of last known whale positions per wallet
        # { wallet_address: { (market_id, token_id): { size, avg_price, ... } } }
        self._whale_cache: dict[str, dict[tuple[str, str], dict[str, Any]]] = {}

    async def initialize(self) -> None:
        """Load saved whale positions from DB into cache."""
        enabled_wallets = self._wallet_config.enabled_wallets
        if not enabled_wallets:
            logger.warning("copy_trader_no_wallets", msg="No enabled wallets in wallets.yaml")
            return

        # Load previously saved whale positions into memory
        for wallet in enabled_wallets:
            address = wallet["address"]
            saved = self._db.get_whale_positions(address)
            self._whale_cache[address] = {}
            for pos in saved:
                key = (pos["market_id"], pos["token_id"])
                self._whale_cache[address][key] = {
                    "size": pos["size"],
                    "avg_price": pos.get("avg_price"),
                    "last_seen_at": pos.get("last_seen_at"),
                }

        logger.info(
            "copy_trader_initialized",
            tracked_wallets=len(enabled_wallets),
            cached_positions=sum(len(v) for v in self._whale_cache.values()),
        )

    async def evaluate(self) -> list[Signal]:
        """Poll each tracked wallet for position changes and emit copy signals.

        Returns:
            Signals for new positions detected.
        """
        enabled_wallets = self._wallet_config.enabled_wallets
        if not enabled_wallets:
            return []

        signals: list[Signal] = []

        for wallet_cfg in enabled_wallets:
            address = wallet_cfg["address"]
            wallet_name = wallet_cfg.get("name", address[:10])
            max_allocation = wallet_cfg.get("max_allocation_usd", float("inf"))

            try:
                new_signals = await self._process_wallet(address, wallet_name, max_allocation)
                signals.extend(new_signals)
            except Exception:
                logger.exception(
                    "copy_trader_wallet_error",
                    wallet=wallet_name,
                    address=address[:10] + "...",
                )

        return signals

    async def _process_wallet(
        self,
        address: str,
        wallet_name: str,
        max_allocation: float,
    ) -> list[Signal]:
        """Process a single whale wallet: fetch positions, detect changes, emit signals.

        H-10 FIX: Detects both entries (BUY) and exits/reductions (SELL).
        """
        # COPY-01: Poll Data API for current positions
        current_positions = await self._client.get_positions(address)

        # Build lookup of current positions
        current_lookup: dict[tuple[str, str], dict[str, Any]] = {}
        if current_positions:
            for pos in current_positions:
                market_id = pos.get(
                    "conditionId", pos.get("market_id", pos.get("condition_id", ""))
                )
                token_id = pos.get("tokenId", pos.get("token_id", pos.get("asset", "")))
                if not market_id or not token_id:
                    continue

                size = float(pos.get("size", pos.get("amount", 0)))
                avg_price = float(pos.get("avgPrice", pos.get("avg_price", 0)))

                if size <= 0:
                    continue

                current_lookup[(market_id, token_id)] = {
                    "size": size,
                    "avg_price": avg_price,
                    "raw": pos,
                }

        # Get previously known positions
        prev_positions = self._whale_cache.get(address, {})

        signals: list[Signal] = []

        # ── H-10 FIX: Detect whale EXITS and significant REDUCTIONS ──
        exit_signals = await self._detect_whale_exits(
            address, wallet_name, prev_positions, current_lookup
        )
        signals.extend(exit_signals)

        # ── COPY-02: Detect new/increased positions → BUY signals ──
        buy_signals = await self._detect_whale_entries(
            address, wallet_name, max_allocation, prev_positions, current_lookup
        )
        signals.extend(buy_signals)

        # Update cache and DB with current positions
        self._whale_cache[address] = current_lookup
        self._persist_whale_positions(address, current_lookup)

        # Subscribe to WebSocket for new tokens we're tracking
        new_token_ids = [
            token_id
            for _, token_id in current_lookup.keys()
            if self._ws_manager.get_latest_price(token_id) is None
        ]
        if new_token_ids:
            self._ws_manager.subscribe(new_token_ids)

        return signals

    # ─── H-10: Whale exit/reduction detection ─────────────────────

    async def _detect_whale_exits(
        self,
        address: str,
        wallet_name: str,
        prev_positions: dict[tuple[str, str], dict[str, Any]],
        current_lookup: dict[tuple[str, str], dict[str, Any]],
    ) -> list[Signal]:
        """H-10 FIX: Detect whale position reductions/exits and generate SELL signals.

        Compares previous whale state to current:
        - Position removed entirely → full SELL
        - Position reduced by >30% → proportional SELL
        """
        signals: list[Signal] = []

        for key, prev_data in prev_positions.items():
            market_id, token_id = key

            current_data = current_lookup.get(key)
            prev_size = prev_data["size"]

            if current_data is None:
                # Whale fully exited this position
                reduction_pct = 100.0
                remaining_size = 0.0
            else:
                current_size = current_data["size"]
                if current_size >= prev_size * _POSITION_DECREASE_THRESHOLD:
                    # Not a significant decrease, skip
                    continue
                reduction_pct = ((prev_size - current_size) / prev_size) * 100
                remaining_size = current_size

            # Check if we even have a matching copy position to exit
            our_positions = self._db.get_open_positions(strategy="copy_trader")
            matching_pos = None
            for pos in our_positions:
                metadata = pos.get("metadata")
                if not metadata:
                    continue
                try:
                    meta = json.loads(metadata) if isinstance(metadata, str) else metadata
                    if meta.get("source_wallet") == address and pos.get("token_id") == token_id:
                        matching_pos = pos
                        break
                except (json.JSONDecodeError, TypeError):
                    continue

            if matching_pos is None:
                # We never copied this position, nothing to exit
                continue

            # Get current price for the exit signal
            current_price = await self._get_current_price(token_id)
            if current_price is None:
                logger.warning(
                    "copy_exit_no_price",
                    wallet=wallet_name,
                    token_id=token_id[:16],
                )
                continue

            # Calculate exit size proportional to whale reduction
            our_entry_size_usd = matching_pos.get("entry_price", 0) * matching_pos.get("size", 0)
            exit_size_usd = our_entry_size_usd * (reduction_pct / 100.0)

            if exit_size_usd < _MIN_EXIT_SIZE_USD:
                continue

            market = await self._client.get_market(market_id)
            market_question = market.question if market else market_id[:30]

            exit_type = "full exit" if remaining_size == 0 else f"{reduction_pct:.0f}% reduction"

            signal = Signal(
                strategy="copy_trader",
                market_id=market_id,
                token_id=token_id,
                side="SELL",
                price=current_price,
                size=round(exit_size_usd, 2),  # USD (C-06)
                order_type=self._order_type,
                reasoning=(
                    f"Whale exit copy {wallet_name}: "
                    f"{exit_type} "
                    f"(prev {prev_size:.1f} → {remaining_size:.1f} shares), "
                    f"market: {market_question}"
                ),
                metadata={
                    "source_wallet": address,
                    "source_wallet_name": wallet_name,
                    "exit_type": exit_type,
                    "whale_reduction_pct": round(reduction_pct, 1),
                    "matching_position_id": matching_pos.get("id", ""),
                },
            )
            signals.append(signal)

            logger.info(
                "copy_exit_signal",
                wallet=wallet_name,
                market_id=market_id[:16],
                exit_type=exit_type,
                reduction_pct=round(reduction_pct, 1),
                exit_size_usd=round(exit_size_usd, 2),
                current_price=round(current_price, 4),
            )

        return signals

    # ─── COPY-02/03/04/05: Whale entry detection ─────────────────

    async def _detect_whale_entries(
        self,
        address: str,
        wallet_name: str,
        max_allocation: float,
        prev_positions: dict[tuple[str, str], dict[str, Any]],
        current_lookup: dict[tuple[str, str], dict[str, Any]],
    ) -> list[Signal]:
        """Detect new or increased whale positions and generate BUY signals."""
        signals: list[Signal] = []

        for key, pos_data in current_lookup.items():
            market_id, token_id = key

            if key in prev_positions:
                # Position already existed — check if size increased significantly
                prev_size = prev_positions[key]["size"]
                if pos_data["size"] <= prev_size * _POSITION_INCREASE_THRESHOLD:
                    # Size hasn't increased meaningfully, skip
                    continue
                # Whale added to position
                logger.info(
                    "whale_position_increased",
                    wallet=wallet_name,
                    market_id=market_id[:16],
                    prev_size=prev_size,
                    new_size=pos_data["size"],
                )

            # H-11 FIX: Get live price FIRST, use it for conviction check below
            current_price = await self._get_current_price(token_id)
            if current_price is None:
                logger.warning(
                    "copy_skip_no_price",
                    wallet=wallet_name,
                    token_id=token_id[:16],
                )
                continue

            # H-11 FIX: Conviction uses current value (shares * live price),
            # not cost basis (shares * avg_price). Stale cost basis misleads sizing.
            whale_current_value_usd = pos_data["size"] * current_price

            # COPY-04: Conviction filter
            if whale_current_value_usd < self._min_whale_position_usd:
                logger.debug(
                    "copy_skip_conviction",
                    wallet=wallet_name,
                    market_id=market_id[:16],
                    whale_current_value_usd=round(whale_current_value_usd, 2),
                    min_required=self._min_whale_position_usd,
                )
                continue

            # COPY-05: Slippage protection — check current price vs whale entry
            whale_entry = pos_data["avg_price"]
            slippage_pct = 0.0
            if whale_entry > 0:
                slippage_pct = ((current_price - whale_entry) / whale_entry) * 100
                if slippage_pct > self._max_slippage_pct:
                    logger.info(
                        "copy_skip_slippage",
                        wallet=wallet_name,
                        market_id=market_id[:16],
                        whale_entry=whale_entry,
                        current_price=current_price,
                        slippage_pct=round(slippage_pct, 2),
                        max_allowed=self._max_slippage_pct,
                    )
                    continue

            # COPY-03: Calculate trade size (H-11: pass current value, not cost basis)
            trade_size = await self._calculate_trade_size(
                whale_size_usd=whale_current_value_usd,
                max_allocation=max_allocation,
                address=address,
            )
            if trade_size <= 0:
                continue

            # Check per-wallet allocation limit
            current_exposure = self._get_wallet_exposure(address)
            if current_exposure + trade_size > max_allocation:
                trade_size = max(0, max_allocation - current_exposure)
                if trade_size < self._strategy_config.min_position_size_usd:
                    logger.info(
                        "copy_skip_wallet_allocation",
                        wallet=wallet_name,
                        current_exposure=round(current_exposure, 2),
                        max_allocation=max_allocation,
                    )
                    continue

            # Calculate edge estimate (whale profit expectation)
            # Conservative: assume whale has 60% win rate, discount by fees
            fees_pct = (
                self._strategy_config.winner_fee_pct + self._strategy_config.max_taker_fee_pct
            )
            estimated_edge = max(0, 10.0 - fees_pct)  # Conservative edge estimate

            # Fetch market info for reasoning
            market = await self._client.get_market(market_id)
            market_question = market.question if market else market_id[:30]

            signal = Signal(
                strategy="copy_trader",
                market_id=market_id,
                token_id=token_id,
                side="BUY",
                price=current_price,
                size=trade_size,
                order_type=self._order_type,
                reasoning=(
                    f"Copy {wallet_name}: "
                    f"whale holds ${whale_current_value_usd:,.0f} (live value) "
                    f"@ entry {whale_entry:.3f}, current {current_price:.3f}, "
                    f"market: {market_question}"
                ),
                metadata={
                    "source_wallet": address,
                    "source_wallet_name": wallet_name,
                    "whale_entry_price": whale_entry,
                    "whale_current_value_usd": whale_current_value_usd,
                    "slippage_pct": round(slippage_pct, 2),
                    "edge_pct": estimated_edge,
                    "yes_token_id": market.yes_token_id if market else "",
                    "no_token_id": market.no_token_id if market else "",
                },
            )
            signals.append(signal)

            logger.info(
                "copy_signal_generated",
                wallet=wallet_name,
                market_id=market_id[:16],
                whale_value_usd=round(whale_current_value_usd, 2),
                trade_size=round(trade_size, 2),
                current_price=round(current_price, 4),
                whale_entry=round(whale_entry, 4),
            )

        return signals

    async def _calculate_trade_size(
        self,
        whale_size_usd: float,
        max_allocation: float,
        address: str,
    ) -> float:
        """Calculate trade size based on configured sizing method.

        COPY-03: Supports fixed $, % of portfolio, % of whale's size.
        M-18 FIX: Portfolio value uses live prices, not stale entry_price.
        """
        if self._sizing_method == "fixed":
            size = self._fixed_size_usd

        elif self._sizing_method == "portfolio_pct":
            # M-18 FIX: Use live prices for portfolio valuation
            portfolio_value = self._wallet_manager.get_usdc_balance()
            # Add open positions valued at current market prices
            positions = self._db.get_open_positions()
            for p in positions:
                token_id = p.get("token_id", "")
                pos_size = p.get("size", 0)
                if token_id and pos_size > 0:
                    live_price = await self._get_current_price(token_id)
                    if live_price is not None:
                        portfolio_value += live_price * pos_size
                    else:
                        # Fallback to entry price if live price unavailable
                        portfolio_value += p.get("entry_price", 0) * pos_size
            size = portfolio_value * (self._portfolio_pct_per_trade / 100)

        elif self._sizing_method == "whale_pct":
            size = whale_size_usd * (self._whale_pct / 100)

        else:
            logger.warning("copy_unknown_sizing", method=self._sizing_method)
            size = self._fixed_size_usd

        # Clamp to min position size
        min_size = self._strategy_config.min_position_size_usd
        if size < min_size:
            return 0.0

        return round(size, 2)

    async def _get_current_price(self, token_id: str) -> float | None:
        """Get current price from WS cache first, then REST fallback."""
        # Try WebSocket cache first (fastest)
        ws_price = self._ws_manager.get_latest_price(token_id)
        if ws_price is not None:
            return ws_price

        # REST fallback
        return await self._client.get_price(token_id)

    def _get_wallet_exposure(self, wallet_address: str) -> float:
        """Get total capital currently deployed copying this wallet."""
        positions = self._db.get_open_positions(strategy="copy_trader")
        exposure = 0.0
        for pos in positions:
            # Check if position metadata references this wallet
            metadata = pos.get("metadata")
            if metadata:
                try:
                    meta_dict = json.loads(metadata) if isinstance(metadata, str) else metadata
                    if meta_dict.get("source_wallet") == wallet_address:
                        exposure += pos["entry_price"] * pos["size"]
                except (json.JSONDecodeError, TypeError):
                    pass
        return exposure

    def _persist_whale_positions(
        self, address: str, positions: dict[tuple[str, str], dict[str, Any]]
    ) -> None:
        """Save current whale positions to DB for restart recovery."""
        # Delete positions no longer held
        saved = self._db.get_whale_positions(address)
        saved_keys = {(p["market_id"], p["token_id"]) for p in saved}
        current_keys = set(positions.keys())

        for key in saved_keys - current_keys:
            market_id, token_id = key
            self._db.delete_whale_position(address, market_id, token_id)

        # Upsert current positions
        for (market_id, token_id), data in positions.items():
            self._db.upsert_whale_position(
                wallet_address=address,
                market_id=market_id,
                token_id=token_id,
                size=data["size"],
                avg_price=data.get("avg_price"),
            )

    # ─── COPY-06: Per-wallet performance tracking ─────────────────

    def get_wallet_performance(self, wallet_address: str) -> dict[str, Any]:
        """Get performance metrics for a specific tracked wallet.

        Returns win rate, total P&L, trade count for positions sourced from this wallet.
        """
        positions = self._db.get_closed_positions(strategy="copy_trader")

        wins = 0
        losses = 0
        total_pnl = 0.0
        trade_count = 0

        for pos in positions:
            metadata = pos.get("metadata")
            if not metadata:
                continue
            try:
                meta_dict = json.loads(metadata) if isinstance(metadata, str) else metadata
                if meta_dict.get("source_wallet") != wallet_address:
                    continue
            except (json.JSONDecodeError, TypeError):
                continue

            trade_count += 1
            realized = pos.get("realized_pnl", 0.0)
            total_pnl += realized
            if realized > 0:
                wins += 1
            elif realized < 0:
                losses += 1

        total = wins + losses
        win_rate = (wins / total * 100) if total > 0 else 0.0

        return {
            "wallet_address": wallet_address,
            "trade_count": trade_count,
            "wins": wins,
            "losses": losses,
            "win_rate": round(win_rate, 1),
            "total_pnl": round(total_pnl, 2),
            "current_exposure": round(self._get_wallet_exposure(wallet_address), 2),
        }

    def get_all_wallet_performance(self) -> list[dict[str, Any]]:
        """Get performance for all tracked wallets."""
        results = []
        for wallet in self._wallet_config.enabled_wallets:
            perf = self.get_wallet_performance(wallet["address"])
            perf["name"] = wallet.get("name", wallet["address"][:10])
            results.append(perf)
        return results

    async def shutdown(self) -> None:
        """Persist state on shutdown."""
        # State is auto-saved by BaseStrategy.stop()
        logger.info("copy_trader_shutting_down")
