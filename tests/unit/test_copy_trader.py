"""Unit tests for the CopyTrader strategy.

Tests COPY-01 through COPY-06:
- COPY-01: Track target wallets via Data API polling
- COPY-02: Detect new whale positions by diffing against stored state
- COPY-03: Configurable sizing (fixed $, % portfolio, % whale)
- COPY-04: Conviction filter (skip if whale position < threshold)
- COPY-05: Slippage protection (skip if price moved >X%)
- COPY-06: Per-wallet performance tracking
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.client import Market
from src.core.config import StrategyConfig, WalletConfig
from src.core.db import Database
from src.execution.order_manager import Signal
from src.strategies.copy_trader import CopyTrader


# ─── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def copy_strategy_config(tmp_path: Path) -> StrategyConfig:
    """Strategy config with copy_trader section."""
    config_path = tmp_path / "strategies.yaml"
    config_path.write_text("""
global:
  max_position_pct: 15
  max_open_positions: 10
  min_edge_pct: 5
  min_cash_reserve_pct: 10
  daily_loss_limit_pct: 10
  min_position_size_usd: 5

fees:
  winner_fee_pct: 2
  max_taker_fee_pct: 1
  estimated_gas_usd: 0.01

positions:
  stop_loss_pct: 25
  trailing_stop_pct: 10
  take_profit:
    - gain_pct: 50
      sell_pct: 50
    - gain_pct: 100
      sell_pct: 100

strategies:
  copy_trader:
    enabled: true
    allocation_pct: 40
    eval_interval_seconds: 30
    sizing_method: fixed
    fixed_size_usd: 50
    portfolio_pct_per_trade: 5
    whale_pct: 10
    min_whale_position_usd: 500
    max_slippage_pct: 5
    poll_interval_sec: 30
    order_type: GTC
""")
    return StrategyConfig(config_path)


@pytest.fixture
def mock_wallet_config() -> MagicMock:
    """Mock WalletConfig with two enabled wallets."""
    wc = MagicMock(spec=WalletConfig)
    wc.enabled_wallets = [
        {
            "address": "0xwhale1" + "a" * 34,
            "name": "BigWhale",
            "enabled": True,
            "max_allocation_usd": 1000.0,
        },
        {
            "address": "0xwhale2" + "b" * 34,
            "name": "SmartMoney",
            "enabled": True,
            "max_allocation_usd": 500.0,
        },
    ]
    return wc


@pytest.fixture
def mock_wallet_config_empty() -> MagicMock:
    """Mock WalletConfig with no enabled wallets."""
    wc = MagicMock(spec=WalletConfig)
    wc.enabled_wallets = []
    return wc


@pytest.fixture
def mock_client() -> MagicMock:
    """Mock PolymarketClient."""
    client = MagicMock()
    client.get_positions = AsyncMock(return_value=[])
    client.get_price = AsyncMock(return_value=0.50)
    client.get_market = AsyncMock(
        return_value=Market(
            condition_id="mkt1",
            question="Will X happen?",
            slug="will-x-happen",
            yes_token_id="yes_tok_1",
            no_token_id="no_tok_1",
            yes_price=0.50,
            no_price=0.50,
            volume=100000,
            liquidity=50000,
            end_date="2026-12-31",
            active=True,
        )
    )
    return client


@pytest.fixture
def mock_ws_manager() -> MagicMock:
    """Mock WebSocketManager."""
    ws = MagicMock()
    ws.get_latest_price = MagicMock(return_value=None)
    ws.subscribe = MagicMock()
    return ws


@pytest.fixture
def mock_wallet_manager() -> MagicMock:
    """Mock WalletManager."""
    wm = MagicMock()
    wm.get_usdc_balance.return_value = 1000.0
    return wm


@pytest.fixture
def mock_order_manager() -> MagicMock:
    """Mock OrderManager."""
    om = MagicMock()
    om.submit_signal = AsyncMock()
    return om


@pytest.fixture
def mock_risk_manager() -> MagicMock:
    """Mock RiskManager."""
    rm = MagicMock()
    rm.approve_signal.return_value = (True, "")
    return rm


@pytest.fixture
def copy_trader(
    mock_client: MagicMock,
    db: Database,
    mock_order_manager: MagicMock,
    mock_risk_manager: MagicMock,
    copy_strategy_config: StrategyConfig,
    mock_wallet_config: MagicMock,
    mock_wallet_manager: MagicMock,
    mock_ws_manager: MagicMock,
) -> CopyTrader:
    """Fully wired CopyTrader instance with mocked dependencies."""
    return CopyTrader(
        client=mock_client,
        db=db,
        order_manager=mock_order_manager,
        risk_manager=mock_risk_manager,
        strategy_config=copy_strategy_config,
        wallet_config=mock_wallet_config,
        wallet_manager=mock_wallet_manager,
        ws_manager=mock_ws_manager,
    )


def _make_position(
    market_id: str = "mkt1",
    token_id: str = "tok1",
    size: float = 1000.0,
    avg_price: float = 0.50,
) -> dict:
    """Helper to create a position dict like Data API returns."""
    return {
        "conditionId": market_id,
        "tokenId": token_id,
        "size": str(size),
        "avgPrice": str(avg_price),
    }


# ─── COPY-01: Initialize & wallet tracking ───────────────────────


class TestCopyTraderInitialize:
    """Tests for initialization and wallet loading."""

    @pytest.mark.asyncio
    async def test_initialize_loads_saved_positions(self, copy_trader: CopyTrader, db: Database):
        """Saved whale positions are loaded into memory cache on init."""
        address = copy_trader._wallet_config.enabled_wallets[0]["address"]
        # Pre-populate DB with a whale position
        db.upsert_whale_position(
            wallet_address=address,
            market_id="mkt1",
            token_id="tok1",
            size=500.0,
            avg_price=0.55,
        )

        await copy_trader.initialize()

        assert address in copy_trader._whale_cache
        assert ("mkt1", "tok1") in copy_trader._whale_cache[address]
        cached = copy_trader._whale_cache[address][("mkt1", "tok1")]
        assert cached["size"] == 500.0

    @pytest.mark.asyncio
    async def test_initialize_no_wallets(
        self,
        mock_client: MagicMock,
        db: Database,
        mock_order_manager: MagicMock,
        mock_risk_manager: MagicMock,
        copy_strategy_config: StrategyConfig,
        mock_wallet_config_empty: MagicMock,
        mock_wallet_manager: MagicMock,
        mock_ws_manager: MagicMock,
    ):
        """Initialize with no enabled wallets logs warning and returns."""
        trader = CopyTrader(
            client=mock_client,
            db=db,
            order_manager=mock_order_manager,
            risk_manager=mock_risk_manager,
            strategy_config=copy_strategy_config,
            wallet_config=mock_wallet_config_empty,
            wallet_manager=mock_wallet_manager,
            ws_manager=mock_ws_manager,
        )
        await trader.initialize()
        # Should not crash and cache should be empty
        assert trader._whale_cache == {}

    @pytest.mark.asyncio
    async def test_initialize_multiple_wallets(self, copy_trader: CopyTrader, db: Database):
        """Both enabled wallets get initialized in the cache."""
        wallets = copy_trader._wallet_config.enabled_wallets
        for w in wallets:
            db.upsert_whale_position(
                wallet_address=w["address"],
                market_id="mkt_shared",
                token_id="tok_shared",
                size=100.0,
            )

        await copy_trader.initialize()

        for w in wallets:
            assert w["address"] in copy_trader._whale_cache


# ─── COPY-02: Detect new whale positions ─────────────────────────


class TestCopyTraderDetection:
    """Tests for whale position detection (diffing)."""

    @pytest.mark.asyncio
    async def test_detect_new_position_emits_signal(
        self, copy_trader: CopyTrader, mock_client: MagicMock
    ):
        """A new whale position with sufficient conviction generates a signal."""
        await copy_trader.initialize()

        # Whale has a new large position
        mock_client.get_positions = AsyncMock(
            return_value=[_make_position(size=2000.0, avg_price=0.50)]
        )
        # Current price same as whale entry → 0% slippage
        mock_client.get_price = AsyncMock(return_value=0.50)

        signals = await copy_trader.evaluate()

        assert len(signals) >= 1
        sig = signals[0]
        assert sig.strategy == "copy_trader"
        assert sig.side == "BUY"
        assert sig.size == 50.0  # fixed sizing

    @pytest.mark.asyncio
    async def test_existing_position_no_signal(
        self, copy_trader: CopyTrader, mock_client: MagicMock
    ):
        """A position already in cache (unchanged) does not generate a signal."""
        # Pre-populate cache for ALL wallets with the exact same position
        for wallet in copy_trader._wallet_config.enabled_wallets:
            copy_trader._whale_cache[wallet["address"]] = {
                ("mkt1", "tok1"): {"size": 2000.0, "avg_price": 0.50}
            }

        # API returns the same position for both wallets
        mock_client.get_positions = AsyncMock(
            return_value=[_make_position(size=2000.0, avg_price=0.50)]
        )

        signals = await copy_trader.evaluate()

        # No signals — existing position didn't grow >10%
        assert len(signals) == 0

    @pytest.mark.asyncio
    async def test_increased_position_emits_signal(
        self, copy_trader: CopyTrader, mock_client: MagicMock
    ):
        """A whale significantly increasing a position triggers a copy signal."""
        address = copy_trader._wallet_config.enabled_wallets[0]["address"]
        # Cache has old size of 1000
        copy_trader._whale_cache[address] = {("mkt1", "tok1"): {"size": 1000.0, "avg_price": 0.50}}

        # Whale increased to 2000 (>10% increase)
        mock_client.get_positions = AsyncMock(
            return_value=[_make_position(size=2000.0, avg_price=0.50)]
        )
        mock_client.get_price = AsyncMock(return_value=0.50)

        signals = await copy_trader.evaluate()

        assert len(signals) >= 1

    @pytest.mark.asyncio
    async def test_small_increase_no_signal(self, copy_trader: CopyTrader, mock_client: MagicMock):
        """A whale increasing position by <10% does not trigger a signal."""
        # Pre-populate cache for ALL wallets
        for wallet in copy_trader._wallet_config.enabled_wallets:
            copy_trader._whale_cache[wallet["address"]] = {
                ("mkt1", "tok1"): {"size": 2000.0, "avg_price": 0.50}
            }

        # Only 5% increase (2000 → 2100), below 10% threshold
        mock_client.get_positions = AsyncMock(
            return_value=[_make_position(size=2100.0, avg_price=0.50)]
        )

        signals = await copy_trader.evaluate()

        assert len(signals) == 0

    @pytest.mark.asyncio
    async def test_no_positions_from_api(self, copy_trader: CopyTrader, mock_client: MagicMock):
        """Empty API response returns no signals."""
        await copy_trader.initialize()
        mock_client.get_positions = AsyncMock(return_value=[])

        signals = await copy_trader.evaluate()
        assert signals == []

    @pytest.mark.asyncio
    async def test_api_error_handled_gracefully(
        self, copy_trader: CopyTrader, mock_client: MagicMock
    ):
        """API exception for one wallet doesn't crash; other wallets still process."""
        await copy_trader.initialize()

        call_count = 0

        async def side_effect(address):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("API timeout")
            return [_make_position(size=2000.0, avg_price=0.50)]

        mock_client.get_positions = AsyncMock(side_effect=side_effect)
        mock_client.get_price = AsyncMock(return_value=0.50)

        # Should not raise; second wallet should still produce signals
        signals = await copy_trader.evaluate()
        assert call_count == 2  # Both wallets attempted


# ─── COPY-03: Configurable sizing ────────────────────────────────


class TestCopyTraderSizing:
    """Tests for trade size calculation."""

    @pytest.mark.asyncio
    async def test_fixed_sizing(self, copy_trader: CopyTrader):
        """Fixed sizing returns configured fixed_size_usd."""
        copy_trader._sizing_method = "fixed"
        copy_trader._fixed_size_usd = 75.0

        size = await copy_trader._calculate_trade_size(
            whale_size_usd=5000.0,
            max_allocation=10000.0,
            address="0xtest",
        )
        assert size == 75.0

    @pytest.mark.asyncio
    async def test_portfolio_pct_sizing(
        self, copy_trader: CopyTrader, mock_wallet_manager: MagicMock, db: Database
    ):
        """Portfolio % sizing uses USDC balance + open position value."""
        copy_trader._sizing_method = "portfolio_pct"
        copy_trader._portfolio_pct_per_trade = 10.0

        # Wallet has $1000 USDC, no open positions
        mock_wallet_manager.get_usdc_balance.return_value = 1000.0

        size = await copy_trader._calculate_trade_size(
            whale_size_usd=5000.0,
            max_allocation=10000.0,
            address="0xtest",
        )
        assert size == 100.0  # 10% of 1000

    @pytest.mark.asyncio
    async def test_whale_pct_sizing(self, copy_trader: CopyTrader):
        """Whale % sizing takes a percentage of the whale's position."""
        copy_trader._sizing_method = "whale_pct"
        copy_trader._whale_pct = 5.0

        size = await copy_trader._calculate_trade_size(
            whale_size_usd=10000.0,
            max_allocation=10000.0,
            address="0xtest",
        )
        assert size == 500.0  # 5% of 10000

    @pytest.mark.asyncio
    async def test_unknown_sizing_falls_back_to_fixed(self, copy_trader: CopyTrader):
        """Unknown sizing method falls back to fixed."""
        copy_trader._sizing_method = "unknown_method"
        copy_trader._fixed_size_usd = 42.0

        size = await copy_trader._calculate_trade_size(
            whale_size_usd=5000.0,
            max_allocation=10000.0,
            address="0xtest",
        )
        assert size == 42.0

    @pytest.mark.asyncio
    async def test_size_below_min_returns_zero(self, copy_trader: CopyTrader):
        """If calculated size is below min_position_size_usd, return 0."""
        copy_trader._sizing_method = "fixed"
        copy_trader._fixed_size_usd = 2.0  # Below min of 5

        size = await copy_trader._calculate_trade_size(
            whale_size_usd=5000.0,
            max_allocation=10000.0,
            address="0xtest",
        )
        assert size == 0.0


# ─── COPY-04: Conviction filter ──────────────────────────────────


class TestCopyTraderConviction:
    """Tests for the conviction filter (minimum whale position size)."""

    @pytest.mark.asyncio
    async def test_skip_low_conviction(self, copy_trader: CopyTrader, mock_client: MagicMock):
        """Whale positions below min_whale_position_usd are skipped."""
        await copy_trader.initialize()
        copy_trader._min_whale_position_usd = 500.0

        # Whale has a small position: 100 shares * $0.50 = $50 < $500 threshold
        mock_client.get_positions = AsyncMock(
            return_value=[_make_position(size=100.0, avg_price=0.50)]
        )

        signals = await copy_trader.evaluate()
        assert len(signals) == 0

    @pytest.mark.asyncio
    async def test_pass_high_conviction(self, copy_trader: CopyTrader, mock_client: MagicMock):
        """Whale positions above threshold pass conviction filter."""
        await copy_trader.initialize()
        copy_trader._min_whale_position_usd = 500.0

        # Whale has 2000 shares @ $0.50 = $1000 > $500
        mock_client.get_positions = AsyncMock(
            return_value=[_make_position(size=2000.0, avg_price=0.50)]
        )
        mock_client.get_price = AsyncMock(return_value=0.50)

        signals = await copy_trader.evaluate()
        assert len(signals) >= 1


# ─── COPY-05: Slippage protection ────────────────────────────────


class TestCopyTraderSlippage:
    """Tests for slippage protection."""

    @pytest.mark.asyncio
    async def test_skip_high_slippage(self, copy_trader: CopyTrader, mock_client: MagicMock):
        """Skip copy if current price moved too far from whale entry."""
        await copy_trader.initialize()
        copy_trader._max_slippage_pct = 5.0

        # Whale entered at 0.50, current price is 0.60 → 20% slippage
        mock_client.get_positions = AsyncMock(
            return_value=[_make_position(size=2000.0, avg_price=0.50)]
        )
        mock_client.get_price = AsyncMock(return_value=0.60)

        signals = await copy_trader.evaluate()
        assert len(signals) == 0

    @pytest.mark.asyncio
    async def test_pass_low_slippage(self, copy_trader: CopyTrader, mock_client: MagicMock):
        """Allow copy when slippage is within threshold."""
        await copy_trader.initialize()
        copy_trader._max_slippage_pct = 5.0

        # Whale entered at 0.50, current price 0.51 → 2% slippage (OK)
        mock_client.get_positions = AsyncMock(
            return_value=[_make_position(size=2000.0, avg_price=0.50)]
        )
        mock_client.get_price = AsyncMock(return_value=0.51)

        signals = await copy_trader.evaluate()
        assert len(signals) >= 1

    @pytest.mark.asyncio
    async def test_skip_when_no_price(
        self, copy_trader: CopyTrader, mock_client: MagicMock, mock_ws_manager: MagicMock
    ):
        """Skip copy when current price cannot be fetched."""
        await copy_trader.initialize()

        mock_client.get_positions = AsyncMock(
            return_value=[_make_position(size=2000.0, avg_price=0.50)]
        )
        # Both WS and REST return None
        mock_ws_manager.get_latest_price.return_value = None
        mock_client.get_price = AsyncMock(return_value=None)

        signals = await copy_trader.evaluate()
        assert len(signals) == 0

    @pytest.mark.asyncio
    async def test_price_from_websocket_cache(
        self, copy_trader: CopyTrader, mock_client: MagicMock, mock_ws_manager: MagicMock
    ):
        """WebSocket cached price is used before falling back to REST."""
        await copy_trader.initialize()

        mock_client.get_positions = AsyncMock(
            return_value=[_make_position(size=2000.0, avg_price=0.50)]
        )
        # WS returns cached price
        mock_ws_manager.get_latest_price.return_value = 0.51
        mock_client.get_price = AsyncMock(return_value=0.60)  # REST would fail slippage

        signals = await copy_trader.evaluate()
        # Should use WS price (0.51 → 2% slippage, passes) not REST (0.60 → 20%)
        assert len(signals) >= 1
        # REST should not have been called since WS provided the price
        mock_client.get_price.assert_not_called()


# ─── COPY-06: Per-wallet performance tracking ────────────────────


class TestCopyTraderPerformance:
    """Tests for per-wallet performance tracking."""

    def test_wallet_performance_no_trades(self, copy_trader: CopyTrader):
        """Performance report for a wallet with no trades."""
        address = copy_trader._wallet_config.enabled_wallets[0]["address"]
        perf = copy_trader.get_wallet_performance(address)

        assert perf["wallet_address"] == address
        assert perf["trade_count"] == 0
        assert perf["wins"] == 0
        assert perf["losses"] == 0
        assert perf["win_rate"] == 0.0
        assert perf["total_pnl"] == 0.0

    def test_wallet_performance_with_closed_positions(self, copy_trader: CopyTrader, db: Database):
        """Performance tracks wins/losses from closed positions."""
        address = copy_trader._wallet_config.enabled_wallets[0]["address"]
        metadata = json.dumps({"source_wallet": address})

        # Create two closed positions: one win, one loss
        pos_id1 = db.open_position(
            market_id="mkt1",
            token_id="tok1",
            strategy="copy_trader",
            side="BUY",
            entry_price=0.50,
            size=100.0,
            metadata={"source_wallet": address},
        )
        db.close_position(pos_id1, realized_pnl=25.0, close_reason="take_profit")

        pos_id2 = db.open_position(
            market_id="mkt2",
            token_id="tok2",
            strategy="copy_trader",
            side="BUY",
            entry_price=0.60,
            size=100.0,
            metadata={"source_wallet": address},
        )
        db.close_position(pos_id2, realized_pnl=-15.0, close_reason="stop_loss")

        perf = copy_trader.get_wallet_performance(address)

        assert perf["trade_count"] == 2
        assert perf["wins"] == 1
        assert perf["losses"] == 1
        assert perf["win_rate"] == 50.0
        assert perf["total_pnl"] == 10.0

    def test_wallet_performance_ignores_other_wallets(self, copy_trader: CopyTrader, db: Database):
        """Performance only counts trades from the specified wallet."""
        address1 = copy_trader._wallet_config.enabled_wallets[0]["address"]
        address2 = copy_trader._wallet_config.enabled_wallets[1]["address"]

        # Position from wallet 1
        pos_id1 = db.open_position(
            market_id="mkt1",
            token_id="tok1",
            strategy="copy_trader",
            side="BUY",
            entry_price=0.50,
            size=100.0,
            metadata={"source_wallet": address1},
        )
        db.close_position(pos_id1, realized_pnl=50.0, close_reason="take_profit")

        # Position from wallet 2
        pos_id2 = db.open_position(
            market_id="mkt2",
            token_id="tok2",
            strategy="copy_trader",
            side="BUY",
            entry_price=0.50,
            size=100.0,
            metadata={"source_wallet": address2},
        )
        db.close_position(pos_id2, realized_pnl=-20.0, close_reason="stop_loss")

        perf1 = copy_trader.get_wallet_performance(address1)
        perf2 = copy_trader.get_wallet_performance(address2)

        assert perf1["trade_count"] == 1
        assert perf1["total_pnl"] == 50.0
        assert perf2["trade_count"] == 1
        assert perf2["total_pnl"] == -20.0

    def test_all_wallet_performance(self, copy_trader: CopyTrader):
        """get_all_wallet_performance returns entries for every enabled wallet."""
        results = copy_trader.get_all_wallet_performance()
        assert len(results) == 2
        assert results[0]["name"] == "BigWhale"
        assert results[1]["name"] == "SmartMoney"


# ─── Signal metadata & content ────────────────────────────────────


class TestCopyTraderSignalContent:
    """Tests for signal metadata and content correctness."""

    @pytest.mark.asyncio
    async def test_signal_contains_wallet_metadata(
        self, copy_trader: CopyTrader, mock_client: MagicMock
    ):
        """Generated signals include source wallet info in metadata."""
        await copy_trader.initialize()

        mock_client.get_positions = AsyncMock(
            return_value=[_make_position(size=2000.0, avg_price=0.50)]
        )
        mock_client.get_price = AsyncMock(return_value=0.51)

        signals = await copy_trader.evaluate()
        assert len(signals) >= 1

        sig = signals[0]
        assert "source_wallet" in sig.metadata
        assert "source_wallet_name" in sig.metadata
        assert "whale_entry_price" in sig.metadata
        assert "whale_current_value_usd" in sig.metadata
        assert sig.metadata["source_wallet_name"] == "BigWhale"

    @pytest.mark.asyncio
    async def test_signal_reasoning_contains_context(
        self, copy_trader: CopyTrader, mock_client: MagicMock
    ):
        """Signal reasoning includes whale name and market info."""
        await copy_trader.initialize()

        mock_client.get_positions = AsyncMock(
            return_value=[_make_position(size=2000.0, avg_price=0.50)]
        )
        mock_client.get_price = AsyncMock(return_value=0.50)

        signals = await copy_trader.evaluate()
        assert len(signals) >= 1

        sig = signals[0]
        assert "BigWhale" in sig.reasoning
        assert sig.order_type == "GTC"


# ─── Cache & persistence ─────────────────────────────────────────


class TestCopyTraderPersistence:
    """Tests for whale position cache and DB persistence."""

    @pytest.mark.asyncio
    async def test_cache_updated_after_evaluate(
        self, copy_trader: CopyTrader, mock_client: MagicMock
    ):
        """After evaluate(), the whale cache reflects latest positions."""
        await copy_trader.initialize()
        address = copy_trader._wallet_config.enabled_wallets[0]["address"]

        mock_client.get_positions = AsyncMock(
            return_value=[_make_position("mkt_new", "tok_new", 3000.0, 0.45)]
        )
        mock_client.get_price = AsyncMock(return_value=0.45)

        await copy_trader.evaluate()

        assert ("mkt_new", "tok_new") in copy_trader._whale_cache[address]

    @pytest.mark.asyncio
    async def test_whale_positions_persisted_to_db(
        self, copy_trader: CopyTrader, mock_client: MagicMock, db: Database
    ):
        """Whale positions are saved to the DB after evaluate()."""
        await copy_trader.initialize()
        address = copy_trader._wallet_config.enabled_wallets[0]["address"]

        mock_client.get_positions = AsyncMock(
            return_value=[_make_position("mkt_p", "tok_p", 1500.0, 0.55)]
        )
        mock_client.get_price = AsyncMock(return_value=0.55)

        await copy_trader.evaluate()

        saved = db.get_whale_positions(address)
        assert len(saved) >= 1
        market_ids = [s["market_id"] for s in saved]
        assert "mkt_p" in market_ids

    @pytest.mark.asyncio
    async def test_removed_whale_position_deleted_from_db(
        self, copy_trader: CopyTrader, mock_client: MagicMock, db: Database
    ):
        """When a whale exits a position, it's removed from the DB."""
        address = copy_trader._wallet_config.enabled_wallets[0]["address"]

        # Pre-populate DB with an old position
        db.upsert_whale_position(
            wallet_address=address,
            market_id="mkt_old",
            token_id="tok_old",
            size=500.0,
            avg_price=0.40,
        )
        await copy_trader.initialize()

        # API now returns a different position (old one is gone)
        mock_client.get_positions = AsyncMock(
            return_value=[_make_position("mkt_new", "tok_new", 2000.0, 0.50)]
        )
        mock_client.get_price = AsyncMock(return_value=0.50)

        await copy_trader.evaluate()

        saved = db.get_whale_positions(address)
        market_ids = [s["market_id"] for s in saved]
        assert "mkt_old" not in market_ids
        assert "mkt_new" in market_ids

    @pytest.mark.asyncio
    async def test_ws_subscribe_called_for_new_tokens(
        self, copy_trader: CopyTrader, mock_client: MagicMock, mock_ws_manager: MagicMock
    ):
        """WebSocket subscribe is called for newly discovered token IDs."""
        await copy_trader.initialize()

        mock_client.get_positions = AsyncMock(
            return_value=[_make_position("mkt1", "tok_new_ws", 2000.0, 0.50)]
        )
        mock_client.get_price = AsyncMock(return_value=0.50)
        mock_ws_manager.get_latest_price.return_value = None  # Not yet cached

        await copy_trader.evaluate()

        mock_ws_manager.subscribe.assert_called()
        call_args = mock_ws_manager.subscribe.call_args[0][0]
        assert "tok_new_ws" in call_args


# ─── Wallet allocation limit ─────────────────────────────────────


class TestCopyTraderAllocation:
    """Tests for per-wallet allocation limits."""

    @pytest.mark.asyncio
    async def test_allocation_limit_caps_trade_size(
        self, copy_trader: CopyTrader, mock_client: MagicMock, db: Database
    ):
        """Trade is capped when wallet allocation limit would be exceeded."""
        await copy_trader.initialize()
        address = copy_trader._wallet_config.enabled_wallets[0]["address"]

        # Create existing position that uses up most of the $1000 allocation
        db.open_position(
            market_id="mkt_existing",
            token_id="tok_existing",
            strategy="copy_trader",
            side="BUY",
            entry_price=0.50,
            size=1900.0,  # 1900 * 0.50 = $950 exposure
            metadata={"source_wallet": address},
        )

        # New whale position
        mock_client.get_positions = AsyncMock(
            return_value=[_make_position("mkt_new", "tok_new", 2000.0, 0.50)]
        )
        mock_client.get_price = AsyncMock(return_value=0.50)

        signals = await copy_trader.evaluate()

        # With $950 exposure and $1000 max, only $50 left
        # Fixed sizing would be $50, and remaining allocation is $50
        # Either the signal size is capped or skipped entirely
        for sig in signals:
            if sig.metadata.get("source_wallet") == address:
                # Size should not exceed remaining allocation
                assert sig.size <= 50.0

    @pytest.mark.asyncio
    async def test_allocation_fully_used_skips(
        self, copy_trader: CopyTrader, mock_client: MagicMock, db: Database
    ):
        """When allocation is fully used, skip the signal."""
        await copy_trader.initialize()
        address = copy_trader._wallet_config.enabled_wallets[0]["address"]

        # Create position that maxes out the $1000 allocation
        db.open_position(
            market_id="mkt_full",
            token_id="tok_full",
            strategy="copy_trader",
            side="BUY",
            entry_price=0.50,
            size=2000.0,  # 2000 * 0.50 = $1000 = max allocation
            metadata={"source_wallet": address},
        )

        mock_client.get_positions = AsyncMock(
            return_value=[_make_position("mkt_new2", "tok_new2", 5000.0, 0.50)]
        )
        mock_client.get_price = AsyncMock(return_value=0.50)

        signals = await copy_trader.evaluate()

        # Signals from wallet 1 should be skipped (allocation exhausted)
        wallet1_signals = [s for s in signals if s.metadata.get("source_wallet") == address]
        assert len(wallet1_signals) == 0


# ─── Config loading ───────────────────────────────────────────────


class TestCopyTraderConfig:
    """Tests for configuration loading from strategies.yaml."""

    def test_config_loaded_from_yaml(self, copy_trader: CopyTrader):
        """Strategy-specific config values are loaded correctly."""
        assert copy_trader._sizing_method == "fixed"
        assert copy_trader._fixed_size_usd == 50.0
        assert copy_trader._min_whale_position_usd == 500.0
        assert copy_trader._max_slippage_pct == 5.0
        assert copy_trader._poll_interval == 30
        assert copy_trader._order_type == "GTC"

    def test_eval_interval_set_from_poll_interval(self, copy_trader: CopyTrader):
        """eval_interval is overridden to poll_interval_sec."""
        assert copy_trader._eval_interval == 30

    def test_strategy_name(self, copy_trader: CopyTrader):
        """Strategy name is 'copy_trader'."""
        assert copy_trader.name == "copy_trader"
