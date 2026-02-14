"""Unit tests for the risk manager module."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.core.config import StrategyConfig
from src.core.db import Database
from src.execution.order_manager import Signal
from src.execution.risk_manager import RiskManager


class TestRiskManager:
    """Tests for RiskManager approval logic."""

    @pytest.fixture
    def risk_manager(
        self, strategy_config: StrategyConfig, db: Database, mock_wallet: MagicMock
    ) -> RiskManager:
        return RiskManager(strategy_config, db, mock_wallet)

    def test_approve_valid_signal(self, risk_manager: RiskManager, sample_signal: Signal):
        """A valid signal should be approved."""
        approved, reason = risk_manager.approve_signal(sample_signal)
        assert approved is True
        assert reason == "Approved"

    def test_reject_when_kill_switch_active(self, risk_manager: RiskManager, sample_signal: Signal):
        """Kill switch should block all signals."""
        risk_manager.activate_kill_switch()
        approved, reason = risk_manager.approve_signal(sample_signal)
        assert approved is False
        assert "kill switch" in reason.lower()

    def test_reject_when_trading_halted(self, risk_manager: RiskManager, sample_signal: Signal):
        """Paused trading should block all signals."""
        risk_manager.pause_trading()
        approved, reason = risk_manager.approve_signal(sample_signal)
        assert approved is False
        assert "halt" in reason.lower()

    def test_resume_after_pause(self, risk_manager: RiskManager, sample_signal: Signal):
        """Resuming should allow signals again."""
        risk_manager.pause_trading()
        risk_manager.resume_trading()
        approved, _ = risk_manager.approve_signal(sample_signal)
        assert approved is True

    def test_reject_max_open_positions(
        self,
        risk_manager: RiskManager,
        sample_signal: Signal,
        db: Database,
    ):
        """Should reject when max open positions reached."""
        # Fill up to max (10)
        for i in range(10):
            db.open_position(f"m{i}", f"t{i}", "copy_trading", "BUY", 0.45, 10.0)

        approved, reason = risk_manager.approve_signal(sample_signal)
        assert approved is False
        assert "max open positions" in reason.lower()

    def test_reject_position_too_large(
        self,
        risk_manager: RiskManager,
        db: Database,
        mock_wallet: MagicMock,
    ):
        """Should reject position exceeding max_position_pct."""
        # Portfolio value ~500 USDC, max position 15% = $75
        signal = Signal(
            strategy="copy_trading",
            market_id="0x" + "cc" * 32,
            token_id="0x" + "dd" * 32,
            side="BUY",
            price=0.50,
            size=100.0,  # $100 > $75 limit
            metadata={"edge_pct": 8.0},
        )
        approved, reason = risk_manager.approve_signal(signal)
        assert approved is False
        assert "position size" in reason.lower()

    def test_reject_low_edge(self, risk_manager: RiskManager):
        """Should reject signal with edge below minimum."""
        signal = Signal(
            strategy="copy_trading",
            market_id="0x" + "cc" * 32,
            token_id="0x" + "dd" * 32,
            side="BUY",
            price=0.50,
            size=10.0,
            metadata={"edge_pct": 2.0},  # Below 5% minimum
        )
        approved, reason = risk_manager.approve_signal(signal)
        assert approved is False
        assert "edge" in reason.lower()

    def test_reject_too_small_position(self, risk_manager: RiskManager):
        """Should reject position below minimum size."""
        signal = Signal(
            strategy="copy_trading",
            market_id="0x" + "cc" * 32,
            token_id="0x" + "dd" * 32,
            side="BUY",
            price=0.50,
            size=2.0,  # $2 < $5 minimum
            metadata={"edge_pct": 8.0},
        )
        approved, reason = risk_manager.approve_signal(signal)
        assert approved is False
        assert "min" in reason.lower()

    def test_get_status(self, risk_manager: RiskManager):
        """Status should return all expected fields."""
        status = risk_manager.get_status()
        assert "kill_switch" in status
        assert "halted" in status
        assert "portfolio_value" in status
        assert "open_positions" in status

    def test_kill_switch_properties(self, risk_manager: RiskManager):
        """Kill switch properties should reflect state."""
        assert risk_manager.is_kill_switch_active is False
        assert risk_manager.is_trading_halted is False

        risk_manager.activate_kill_switch()
        assert risk_manager.is_kill_switch_active is True
        assert risk_manager.is_trading_halted is True

        risk_manager.deactivate_kill_switch()
        assert risk_manager.is_kill_switch_active is False
