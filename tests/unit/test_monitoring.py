"""Unit tests for the monitoring modules (logger, pnl, health)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.monitoring.health import ComponentStatus, HealthChecker, SystemHealth
from src.monitoring.logger import get_logger, log_trade, setup_logging
from src.monitoring.pnl import PnLSnapshot, PnLTracker


class TestLogger:
    """Tests for structured logging setup."""

    def test_setup_logging(self):
        """Logging setup should not raise."""
        setup_logging(log_level="DEBUG", json_output=False)
        logger = get_logger("test")
        assert logger is not None

    def test_log_trade(self, caplog):
        """log_trade should produce structured output."""
        setup_logging(log_level="DEBUG", json_output=False)
        logger = get_logger("test_trade")
        # Should not raise
        log_trade(
            logger,
            event="order_placed",
            strategy="copy_trading",
            market_id="m1",
            token_id="t1",
            side="BUY",
            price=0.45,
            size=10.0,
            reasoning="test",
        )


class TestPnLTracker:
    """Tests for P&L tracking."""

    @pytest.fixture
    def pnl_tracker(self, db, mock_wallet) -> PnLTracker:
        return PnLTracker(db, mock_wallet)

    @pytest.mark.asyncio
    async def test_initialize_sets_starting_balance(self, pnl_tracker):
        """First initialization should set starting balance."""
        await pnl_tracker.initialize()
        assert pnl_tracker._starting_balance is not None
        assert pnl_tracker._starting_balance >= 0

    def test_get_snapshot(self, pnl_tracker):
        """Snapshot should return valid data."""
        pnl_tracker._starting_balance = 500.0
        snapshot = pnl_tracker.get_snapshot()
        assert isinstance(snapshot, PnLSnapshot)
        assert snapshot.portfolio_value >= 0
        assert snapshot.usdc_balance == 500.0  # From mock

    def test_format_summary(self, pnl_tracker):
        """Summary should be a non-empty string."""
        pnl_tracker._starting_balance = 500.0
        summary = pnl_tracker.format_summary()
        assert "P&L Summary" in summary
        assert "Portfolio" in summary

    def test_check_daily_loss_limit_safe(self, pnl_tracker):
        """Should return True when within daily limit."""
        pnl_tracker._starting_balance = 500.0
        assert pnl_tracker.check_daily_loss_limit(10.0) is True

    def test_log_snapshot(self, pnl_tracker):
        """log_snapshot should return a PnLSnapshot."""
        pnl_tracker._starting_balance = 500.0
        snapshot = pnl_tracker.log_snapshot()
        assert isinstance(snapshot, PnLSnapshot)


class TestHealthChecker:
    """Tests for health check module."""

    @pytest.fixture
    def mock_ws(self) -> MagicMock:
        ws = MagicMock()
        ws.is_connected = True
        ws.is_stale = False
        ws.seconds_since_last_message = 5.0
        return ws

    @pytest.fixture
    def mock_client(self) -> MagicMock:
        client = MagicMock()
        client.get_markets = AsyncMock(return_value=[{"id": "m1"}])
        return client

    @pytest.fixture
    def health_checker(self, mock_client, db, mock_wallet, mock_ws) -> HealthChecker:
        return HealthChecker(mock_client, db, mock_wallet, mock_ws)

    def test_check_websocket_healthy(self, health_checker):
        """Connected, non-stale WS should be healthy."""
        result = health_checker.check_websocket()
        assert result.status == ComponentStatus.HEALTHY

    def test_check_websocket_stale(self, health_checker, mock_ws):
        """Connected but stale WS should be degraded."""
        mock_ws.is_stale = True
        result = health_checker.check_websocket()
        assert result.status == ComponentStatus.DEGRADED

    def test_check_websocket_disconnected(self, health_checker, mock_ws):
        """Disconnected WS should be down."""
        mock_ws.is_connected = False
        result = health_checker.check_websocket()
        assert result.status == ComponentStatus.DOWN

    def test_check_database(self, health_checker):
        """DB check should be healthy with valid DB."""
        result = health_checker.check_database()
        assert result.status == ComponentStatus.HEALTHY

    def test_check_wallet_healthy(self, health_checker):
        """Wallet with balance > $1 should be healthy."""
        result = health_checker.check_wallet()
        assert result.status == ComponentStatus.HEALTHY

    def test_check_wallet_low_balance(self, health_checker, mock_wallet):
        """Wallet with balance < $1 should be degraded."""
        mock_wallet.get_usdc_balance.return_value = 0.50
        result = health_checker.check_wallet()
        assert result.status == ComponentStatus.DEGRADED

    @pytest.mark.asyncio
    async def test_get_system_health(self, health_checker):
        """Full system health check should run all checks."""
        health = await health_checker.get_system_health()
        assert isinstance(health, SystemHealth)
        assert len(health.components) == 4
        assert health.uptime_seconds >= 0

    @pytest.mark.asyncio
    async def test_system_health_to_dict(self, health_checker):
        """Health report should be serializable."""
        health = await health_checker.get_system_health()
        d = health.to_dict()
        assert "timestamp" in d
        assert "overall" in d
        assert "components" in d
