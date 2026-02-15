"""Unit tests for StinkBidder strategy (Phase 4)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.client import Market
from src.core.config import StrategyConfig
from src.core.db import Database
from src.execution.order_manager import OrderManager, Signal
from src.execution.risk_manager import RiskManager
from src.strategies.stink_bidder import StinkBidder


@pytest.fixture
def mock_deps():
    client = MagicMock()
    client.get_markets = AsyncMock()
    # Mock CLOB client inside client
    client.clob = MagicMock()
    client.clob.get_orders = MagicMock(return_value=[])

    db = MagicMock(spec=Database)
    db.load_strategy_state.return_value = {}

    order_manager = MagicMock(spec=OrderManager)
    order_manager.submit_signal = AsyncMock()

    risk_manager = MagicMock(spec=RiskManager)
    risk_manager.approve_signal.return_value = (True, "")

    strategy_config = MagicMock(spec=StrategyConfig)
    strategy_config.get_strategy.return_value = {
        "allocation_pct": 20.0,
        "min_discount_pct": 70.0,
        "max_discount_pct": 90.0,
        "max_active_bids": 2,  # Small limit for testing
        "refresh_interval_sec": 300,
        "min_market_volume_usd": 1000.0,
    }
    strategy_config.is_strategy_enabled.return_value = True
    strategy_config.min_position_size_usd = 10.0

    notifier = MagicMock()

    return {
        "client": client,
        "db": db,
        "order_manager": order_manager,
        "risk_manager": risk_manager,
        "strategy_config": strategy_config,
        "notifier": notifier,
    }


@pytest.fixture
def bidder(mock_deps):
    return StinkBidder(
        client=mock_deps["client"],
        db=mock_deps["db"],
        order_manager=mock_deps["order_manager"],
        risk_manager=mock_deps["risk_manager"],
        strategy_config=mock_deps["strategy_config"],
        notifier=mock_deps["notifier"],
    )


class TestStinkBidder:
    @pytest.mark.asyncio
    async def test_initialization(self, bidder, mock_deps):
        # Setup: persisted orders
        bidder._state = {"active_orders": {"order1": {"market_id": "m1"}}}

        # CLOB has this order, so it should stay
        mock_deps["client"].clob.get_orders.return_value = [{"orderID": "order1"}]

        await bidder.initialize()

        assert len(bidder._active_orders) == 1
        assert "order1" in bidder._active_orders
        assert bidder._min_discount_pct == 70.0

    @pytest.mark.asyncio
    async def test_reconcile_removes_missing_orders(self, bidder, mock_deps):
        # Setup: internal tracker has 2 orders
        bidder._active_orders = {"order1": {"market_id": "m1"}, "order2": {"market_id": "m2"}}

        # CLOB only has order2 (order1 filled or cancelled)
        mock_deps["client"].clob.get_orders.return_value = [{"orderID": "order2"}]

        # Calling evaluate triggers reconcile
        mock_deps["client"].get_markets.return_value = []
        await bidder.evaluate()

        assert len(bidder._active_orders) == 1
        assert "order2" in bidder._active_orders
        assert "order1" not in bidder._active_orders

    @pytest.mark.asyncio
    async def test_evaluate_at_capacity(self, bidder, mock_deps):
        # Max bids is 2
        bidder._active_orders = {"order1": {"market_id": "m1"}, "order2": {"market_id": "m2"}}
        mock_deps["client"].clob.get_orders.return_value = [
            {"orderID": "order1"},
            {"orderID": "order2"},
        ]

        signals = await bidder.evaluate()

        assert len(signals) == 0
        mock_deps["client"].get_markets.assert_not_called()  # Shouldn't even fetch markets

    @pytest.mark.asyncio
    async def test_evaluate_places_bid(self, bidder, mock_deps):
        # Capacity available
        bidder._active_orders = {}

        # Market available
        market = Market(
            condition_id="m1",
            question="Q?",
            slug="q",
            yes_token_id="y1",
            no_token_id="n1",
            yes_price=0.50,
            no_price=0.50,
            volume=5000,
            liquidity=5000,
            end_date="2025-01-01",
            active=True,
        )
        mock_deps["client"].get_markets.return_value = [market]

        signals = await bidder.evaluate()

        assert len(signals) == 1
        sig = signals[0]
        assert sig.strategy == "stink_bidder"
        assert sig.side == "BUY"
        assert sig.order_type == "GTC"
        assert sig.metadata["stink_bid"] is True

        # Check price logic: 0.50 * (1 - 0.70...0.90) = 0.15 ... 0.05
        # It clamps at 0.10 max stink price in code
        assert 0.01 <= sig.price <= 0.10

    @pytest.mark.asyncio
    async def test_evaluate_skips_existing_market(self, bidder, mock_deps):
        # Already have a bid on m1
        bidder._active_orders = {"order1": {"market_id": "m1"}}
        mock_deps["client"].clob.get_orders.return_value = [{"orderID": "order1"}]

        market = Market(
            condition_id="m1",
            question="Q?",
            slug="q",
            yes_token_id="y1",
            no_token_id="n1",
            yes_price=0.50,
            no_price=0.50,
            volume=5000,
            liquidity=5000,
            end_date="2025-01-01",
            active=True,
        )
        mock_deps["client"].get_markets.return_value = [market]

        signals = await bidder.evaluate()

        assert len(signals) == 0

    @pytest.mark.asyncio
    async def test_emit_signal_passthrough(self, bidder, mock_deps):
        signal = Signal(
            strategy="stink_bidder", market_id="m1", token_id="t1", side="BUY", price=0.05, size=100
        )

        await bidder.emit_signal(signal)

        mock_deps["risk_manager"].approve_signal.assert_called_once()
        mock_deps["order_manager"].submit_signal.assert_called_once()

    @pytest.mark.asyncio
    async def test_price_logic_safety(self, bidder, mock_deps):
        # Market price very high, discount might still be > 0.10?
        # Say price is 0.90. 70% discount = 0.27.
        # Code has safety clamp at 0.10.
        market = Market(
            condition_id="m1",
            question="Q?",
            slug="q",
            yes_token_id="y1",
            no_token_id="n1",
            yes_price=0.90,
            no_price=0.10,
            volume=5000,
            liquidity=5000,
            end_date="2025-01-01",
            active=True,
        )
        mock_deps["client"].get_markets.return_value = [market]

        signals = await bidder.evaluate()

        assert len(signals) == 1
        assert signals[0].price <= 0.10
