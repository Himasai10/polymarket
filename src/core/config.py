"""
Type-safe configuration loaded from .env and strategies.yaml.

Addresses: CORE-01 (config from .env + yaml)
Prevents: Pitfall 10 (private key exposure via SecretStr)

Audit fixes applied:
- H-22: Validate credentials exist for live mode
- H-23: Default to empty dict when yaml.safe_load returns None
- M-01: API keys wrapped in SecretStr to prevent accidental logging
- M-02: Strategy allocation percentages validated (sum ≤ 100%)
- M-24: RPC URL format validation
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import structlog
import yaml
from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger()


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

    # M-01 FIX: Polymarket API credentials wrapped in SecretStr
    polymarket_api_key: SecretStr = SecretStr("")
    polymarket_api_secret: SecretStr = SecretStr("")
    polymarket_api_passphrase: SecretStr = SecretStr("")

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

    # M-24 FIX: Validate RPC URL format
    @field_validator("polygon_rpc_url")
    @classmethod
    def validate_rpc_url(cls, v: str) -> str:
        parsed = urlparse(v)
        if parsed.scheme not in ("http", "https", "ws", "wss"):
            raise ValueError(f"polygon_rpc_url must start with http(s):// or ws(s)://, got '{v}'")
        if not parsed.hostname:
            raise ValueError(f"polygon_rpc_url missing hostname: '{v}'")
        return v

    # H-22 FIX: Validate credentials exist for live mode
    @model_validator(mode="after")
    def validate_live_credentials(self) -> "Settings":
        if self.trading_mode != "live":
            return self

        missing: list[str] = []
        if not self.wallet_private_key.get_secret_value():
            missing.append("wallet_private_key")
        if not self.polymarket_api_key.get_secret_value():
            missing.append("polymarket_api_key")
        if not self.polymarket_api_secret.get_secret_value():
            missing.append("polymarket_api_secret")
        if not self.polymarket_api_passphrase.get_secret_value():
            missing.append("polymarket_api_passphrase")
        if not self.funder_address:
            missing.append("funder_address")

        if missing:
            raise ValueError(
                f"Live trading requires credentials: {', '.join(missing)}. "
                "Set them in .env or switch to trading_mode=paper."
            )
        return self

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
    """Strategy configuration loaded from strategies.yaml.

    Audit fixes:
    - H-23: Defaults to empty dict when yaml.safe_load returns None
    - M-02: Validates strategy allocation totals ≤ 100%
    """

    def __init__(self, config_path: Path | None = None):
        if config_path is None:
            config_path = PROJECT_ROOT / "config" / "strategies.yaml"

        if not config_path.exists():
            raise FileNotFoundError(f"Strategy config not found: {config_path}")

        with open(config_path) as f:
            raw = yaml.safe_load(f)
            # H-23 FIX: safe_load returns None for empty files
            self._data: dict[str, Any] = raw if isinstance(raw, dict) else {}

        if not self._data:
            logger.warning(
                "strategy_config_empty",
                path=str(config_path),
                msg="Strategy config file is empty or invalid, using defaults",
            )

        # M-02 FIX: Validate allocation totals
        self._validate_allocations()

    def _validate_allocations(self) -> None:
        """M-02 FIX: Validate that enabled strategy allocations sum to ≤ 100%."""
        strategies = self._data.get("strategies", {})
        if not isinstance(strategies, dict):
            return

        total_allocation = 0.0
        for name, cfg in strategies.items():
            if not isinstance(cfg, dict):
                continue
            if not cfg.get("enabled", False):
                continue
            alloc = float(cfg.get("allocation_pct", 0.0))
            if alloc < 0:
                raise ValueError(f"Strategy '{name}' has negative allocation_pct: {alloc}")
            total_allocation += alloc

        if total_allocation > 100.0:
            raise ValueError(
                f"Total strategy allocation ({total_allocation:.1f}%) exceeds 100%. "
                "Reduce allocation_pct values in strategies.yaml."
            )

        if total_allocation > 0:
            logger.info(
                "strategy_allocations_validated",
                total_pct=round(total_allocation, 1),
                remaining_pct=round(100.0 - total_allocation, 1),
            )

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
    """Tracked wallets loaded from wallets.yaml.

    H-23 FIX: Defaults to empty dict when yaml.safe_load returns None.
    """

    def __init__(self, config_path: Path | None = None):
        if config_path is None:
            config_path = PROJECT_ROOT / "config" / "wallets.yaml"

        if not config_path.exists():
            raise FileNotFoundError(f"Wallet config not found: {config_path}")

        with open(config_path) as f:
            raw = yaml.safe_load(f)
            # H-23 FIX: safe_load returns None for empty files
            self._data: dict[str, Any] = raw if isinstance(raw, dict) else {}

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
