"""
Type-safe configuration loaded from .env and strategies.yaml.

Addresses: CORE-01 (config from .env + yaml)
Prevents: Pitfall 10 (private key exposure via SecretStr)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _find_project_root() -> Path:
    """Walk up from this file to find the project root (contains pyproject.toml)."""
    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / "pyproject.toml").exists():
            return current
        current = current.parent
    return Path.cwd()


PROJECT_ROOT = _find_project_root()


class Settings(BaseSettings):
    """Bot configuration loaded from environment variables.

    Secrets use SecretStr to prevent accidental logging.
    """

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Polymarket API credentials
    polymarket_api_key: str = ""
    polymarket_api_secret: str = ""
    polymarket_api_passphrase: str = ""

    # Wallet
    wallet_private_key: SecretStr = SecretStr("")
    funder_address: str = ""

    # Network
    polygon_rpc_url: str = "https://polygon-rpc.com"
    polymarket_host: str = "https://clob.polymarket.com"
    gamma_api_url: str = "https://gamma-api.polymarket.com"
    data_api_url: str = "https://data-api.polymarket.com"
    ws_url: str = "wss://ws-subscriptions-clob.polymarket.com"
    chain_id: int = 137  # Polygon mainnet

    # Telegram
    telegram_bot_token: SecretStr = SecretStr("")
    telegram_chat_id: str = ""

    # Trading mode
    trading_mode: str = "paper"  # "paper" or "live"

    # Logging
    log_level: str = "INFO"

    # Database
    database_url: str = "sqlite:///data/polybot.db"

    # Health check HTTP server port (DEPLOY-03)
    health_port: int = 8080

    @field_validator("trading_mode")
    @classmethod
    def validate_trading_mode(cls, v: str) -> str:
        if v not in ("paper", "live"):
            raise ValueError(f"trading_mode must be 'paper' or 'live', got '{v}'")
        return v

    @property
    def is_live(self) -> bool:
        return self.trading_mode == "live"

    @property
    def db_path(self) -> Path:
        """Extract SQLite file path from database URL."""
        url = self.database_url
        if url.startswith("sqlite:///"):
            rel_path = url[len("sqlite:///") :]
            return PROJECT_ROOT / rel_path
        return PROJECT_ROOT / "data" / "polybot.db"


class StrategyConfig:
    """Strategy configuration loaded from strategies.yaml."""

    def __init__(self, config_path: Path | None = None):
        if config_path is None:
            config_path = PROJECT_ROOT / "config" / "strategies.yaml"

        if not config_path.exists():
            raise FileNotFoundError(f"Strategy config not found: {config_path}")

        with open(config_path) as f:
            self._data: dict[str, Any] = yaml.safe_load(f)

    @property
    def global_config(self) -> dict[str, Any]:
        return self._data.get("global", {})

    @property
    def strategies(self) -> dict[str, Any]:
        return self._data.get("strategies", {})

    @property
    def positions(self) -> dict[str, Any]:
        return self._data.get("positions", {})

    @property
    def fees(self) -> dict[str, Any]:
        return self._data.get("fees", {})

    # Global settings with defaults
    @property
    def max_position_pct(self) -> float:
        return float(self.global_config.get("max_position_pct", 15.0))

    @property
    def max_open_positions(self) -> int:
        return int(self.global_config.get("max_open_positions", 10))

    @property
    def min_edge_pct(self) -> float:
        return float(self.global_config.get("min_edge_pct", 5.0))

    @property
    def min_cash_reserve_pct(self) -> float:
        return float(self.global_config.get("min_cash_reserve_pct", 10.0))

    @property
    def daily_loss_limit_pct(self) -> float:
        return float(self.global_config.get("daily_loss_limit_pct", 10.0))

    @property
    def min_position_size_usd(self) -> float:
        return float(self.global_config.get("min_position_size_usd", 25.0))

    # Fee helpers
    @property
    def winner_fee_pct(self) -> float:
        return float(self.fees.get("winner_fee_pct", 2.0))

    @property
    def max_taker_fee_pct(self) -> float:
        return float(self.fees.get("max_taker_fee_pct", 3.15))

    @property
    def estimated_gas_usd(self) -> float:
        return float(self.fees.get("estimated_gas_usd", 0.03))

    def get_strategy(self, name: str) -> dict[str, Any] | None:
        """Get configuration for a specific strategy."""
        return self.strategies.get(name)

    def is_strategy_enabled(self, name: str) -> bool:
        """Check if a strategy is enabled."""
        strategy = self.get_strategy(name)
        if strategy is None:
            return False
        return strategy.get("enabled", False)

    def get_strategy_allocation(self, name: str) -> float:
        """Get allocation percentage for a strategy."""
        strategy = self.get_strategy(name)
        if strategy is None:
            return 0.0
        return float(strategy.get("allocation_pct", 0.0))

    def get_take_profit_tiers(self) -> list[dict[str, float]]:
        """Get take-profit tier configuration."""
        return self.positions.get("take_profit", [])

    @property
    def stop_loss_pct(self) -> float:
        return float(self.positions.get("stop_loss_pct", 25.0))

    @property
    def trailing_stop_pct(self) -> float:
        return float(self.positions.get("trailing_stop_pct", 10.0))


class WalletConfig:
    """Tracked wallets loaded from wallets.yaml."""

    def __init__(self, config_path: Path | None = None):
        if config_path is None:
            config_path = PROJECT_ROOT / "config" / "wallets.yaml"

        if not config_path.exists():
            raise FileNotFoundError(f"Wallet config not found: {config_path}")

        with open(config_path) as f:
            self._data: dict[str, Any] = yaml.safe_load(f)

    @property
    def wallets(self) -> list[dict[str, Any]]:
        return self._data.get("wallets", [])

    @property
    def enabled_wallets(self) -> list[dict[str, Any]]:
        return [w for w in self.wallets if w.get("enabled", False) and w.get("address")]

    def get_wallet(self, address: str) -> dict[str, Any] | None:
        """Get wallet config by address (case-insensitive)."""
        addr_lower = address.lower()
        for w in self.wallets:
            if w.get("address", "").lower() == addr_lower:
                return w
        return None


def load_settings() -> Settings:
    """Load settings from environment / .env file."""
    return Settings()


def load_strategy_config(path: Path | None = None) -> StrategyConfig:
    """Load strategy configuration from YAML."""
    return StrategyConfig(path)


def load_wallet_config(path: Path | None = None) -> WalletConfig:
    """Load tracked wallet configuration from YAML."""
    return WalletConfig(path)
