"""Shared test fixtures for all unit tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.core.config import Settings, StrategyConfig
from src.core.db import Database
from src.execution.order_manager import Signal


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> str:
    """Provide a temporary database path."""
    return str(tmp_path / "test.db")


@pytest.fixture
def settings(tmp_db_path: str) -> Settings:
    """Provide test settings with a temp database."""
    return Settings(
        polymarket_api_key="test-key",
        polymarket_api_secret="test-secret",
        polymarket_api_passphrase="test-pass",
        wallet_private_key="0x" + "ab" * 32,
        funder_address="0x" + "11" * 20,
        polygon_rpc_url="https://polygon-rpc.com",
        trading_mode="paper",
        log_level="DEBUG",
        database_url=f"sqlite:///{tmp_db_path}",
    )


@pytest.fixture
def db(settings: Settings) -> Database:
    """Provide an initialized test database."""
    database = Database(settings)
    database.initialize()
    yield database
    database.close()


@pytest.fixture
def strategy_config(tmp_path: Path) -> StrategyConfig:
    """Provide test strategy config."""
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
  arb_scanner:
    enabled: true
    allocation_pct: 20
    eval_interval_seconds: 10
  stink_bidder:
    enabled: false
    allocation_pct: 20
    eval_interval_seconds: 300
""")
    return StrategyConfig(config_path)


@pytest.fixture
def sample_signal() -> Signal:
    """Provide a sample trading signal."""
    return Signal(
        strategy="copy_trader",
        market_id="0x" + "aa" * 32,
        token_id="0x" + "bb" * 32,
        side="BUY",
        price=0.45,
        size=10.0,
        order_type="GTC",
        urgency="normal",
        reasoning="Test signal",
        metadata={"edge_pct": 8.0},
    )


@pytest.fixture
def mock_wallet() -> MagicMock:
    """Provide a mock wallet manager."""
    wallet = MagicMock()
    wallet.get_usdc_balance.return_value = 500.0
    wallet.get_matic_balance.return_value = 1.0
    wallet.funder_address = "0x" + "11" * 20
    return wallet
