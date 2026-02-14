"""Unit tests for the order manager and signal processing."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.client import OrderResult
from src.core.rate_limiter import RateLimiter
from src.execution.order_manager import OrderManager, Signal


class TestSignal:
    """Tests for the Signal dataclass."""

    def test_signal_defaults(self):
        signal = Signal(
            strategy="test",
            market_id="m1",
            token_id="t1",
            side="BUY",
            price=0.45,
            size=10.0,
        )
        assert signal.order_type == "GTC"
        assert signal.urgency == "normal"
        assert signal.reasoning == ""
        assert signal.metadata == {}

    def test_signal_with_metadata(self, sample_signal: Signal):
        assert sample_signal.metadata["edge_pct"] == 8.0
        assert sample_signal.strategy == "copy_trader"


class TestOrderManager:
    """Tests for OrderManager queue and execution."""

    @pytest.fixture
    def mock_client(self) -> MagicMock:
        client = MagicMock()
        client.create_and_place_order.return_value = OrderResult(success=True, order_id="ord-123")
        return client

    @pytest.fixture
    def order_manager(self, mock_client: MagicMock, db: MagicMock) -> OrderManager:
        rate_limiter = RateLimiter(max_requests=100, window_seconds=60.0)
        return OrderManager(mock_client, db, rate_limiter)

    @pytest.mark.asyncio
    async def test_submit_signal(self, order_manager: OrderManager, sample_signal: Signal):
        """Submitting a signal adds it to the queue."""
        await order_manager.submit_signal(sample_signal)
        assert order_manager.get_pending_count() == 1

    @pytest.mark.asyncio
    async def test_cancel_all(self, order_manager: OrderManager, mock_client: MagicMock):
        """Cancel all delegates to client."""
        mock_client.cancel_all_orders = AsyncMock(return_value=True)
        result = await order_manager.cancel_all()
        assert result is True
        mock_client.cancel_all_orders.assert_called_once()
