"""Unit tests for the config module."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.core.config import Settings, StrategyConfig


class TestSettings:
    """Tests for Settings loading and validation."""

    def test_default_settings(self, settings: Settings):
        """Settings fixture loads correctly."""
        assert settings.trading_mode == "paper"
        assert settings.is_live is False
        assert settings.chain_id == 137

    def test_db_path_extraction(self, settings: Settings):
        """db_path property extracts path from database_url."""
        assert isinstance(settings.db_path, Path)
        assert str(settings.db_path).endswith("test.db")

    def test_is_live_mode(self, settings: Settings):
        """is_live returns True only in live mode."""
        settings.trading_mode = "live"
        assert settings.is_live is True

    def test_private_key_is_secret(self, settings: Settings):
        """Private key should be a SecretStr."""
        # Should not expose the value in repr
        assert "ab" * 32 not in repr(settings.wallet_private_key)
        # But should be accessible
        assert settings.wallet_private_key.get_secret_value().startswith("0x")


class TestStrategyConfig:
    """Tests for StrategyConfig YAML loading."""

    def test_load_strategies(self, strategy_config: StrategyConfig):
        """Config loads strategy definitions from YAML."""
        assert strategy_config.is_strategy_enabled("copy_trader") is True
        assert strategy_config.is_strategy_enabled("stink_bidder") is False

    def test_risk_parameters(self, strategy_config: StrategyConfig):
        """Risk parameters loaded correctly."""
        assert strategy_config.max_position_pct == 15
        assert strategy_config.max_open_positions == 10
        assert strategy_config.min_edge_pct == 5
        assert strategy_config.min_cash_reserve_pct == 10
        assert strategy_config.daily_loss_limit_pct == 10

    def test_strategy_allocation(self, strategy_config: StrategyConfig):
        """Strategy allocation percentages loaded correctly."""
        assert strategy_config.get_strategy_allocation("copy_trader") == 40
        assert strategy_config.get_strategy_allocation("arb_scanner") == 20
        assert strategy_config.get_strategy_allocation("nonexistent") == 0

    def test_take_profit_tiers(self, strategy_config: StrategyConfig):
        """Take profit tiers loaded correctly."""
        tiers = strategy_config.get_take_profit_tiers()
        assert len(tiers) == 2
        assert tiers[0]["gain_pct"] == 50
        assert tiers[0]["sell_pct"] == 50
        assert tiers[1]["gain_pct"] == 100

    def test_fee_parameters(self, strategy_config: StrategyConfig):
        """Fee parameters loaded correctly."""
        assert strategy_config.winner_fee_pct == 2
        assert strategy_config.max_taker_fee_pct == 1
        assert strategy_config.estimated_gas_usd == 0.01

    def test_position_management(self, strategy_config: StrategyConfig):
        """Position management params loaded correctly."""
        assert strategy_config.stop_loss_pct == 25
        assert strategy_config.trailing_stop_pct == 10

    def test_get_strategy(self, strategy_config: StrategyConfig):
        """Can retrieve individual strategy config."""
        copy = strategy_config.get_strategy("copy_trader")
        assert copy is not None
        assert copy["enabled"] is True
        assert copy["allocation_pct"] == 40

    def test_get_missing_strategy(self, strategy_config: StrategyConfig):
        """Missing strategy returns None."""
        assert strategy_config.get_strategy("nonexistent") is None
