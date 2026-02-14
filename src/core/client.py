"""
Polymarket API client wrapper.

Wraps py-clob-client for CLOB operations, httpx for Gamma/Data APIs.

Addresses: CORE-02 (auth), CORE-04 (market discovery), CORE-05 (orders), CORE-06 (cancel)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import structlog

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType

from .config import Settings, StrategyConfig

logger = structlog.get_logger()


@dataclass
class Market:
    """Normalized market data from Gamma API."""

    condition_id: str
    question: str
    slug: str
    yes_token_id: str
    no_token_id: str
    yes_price: float
    no_price: float
    volume: float
    liquidity: float
    end_date: str
    active: bool
    category: str = ""
    description: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_gamma(cls, data: dict[str, Any]) -> Market:
        """Parse a market from Gamma API response."""
        tokens = data.get("tokens", [])
        yes_token = tokens[0] if len(tokens) > 0 else {}
        no_token = tokens[1] if len(tokens) > 1 else {}

        return cls(
            condition_id=data.get("conditionId", data.get("condition_id", "")),
            question=data.get("question", ""),
            slug=data.get("slug", ""),
            yes_token_id=yes_token.get("token_id", ""),
            no_token_id=no_token.get("token_id", ""),
            yes_price=float(yes_token.get("price", 0)),
            no_price=float(no_token.get("price", 0)),
            volume=float(data.get("volume", 0)),
            liquidity=float(data.get("liquidity", 0)),
            end_date=data.get("endDate", data.get("end_date", "")),
            active=data.get("active", False),
            category=data.get("category", ""),
            description=data.get("description", ""),
            raw=data,
        )


@dataclass
class OrderResult:
    """Result of an order placement."""

    success: bool
    order_id: str = ""
    error: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


class PolymarketClient:
    """Unified client for all Polymarket API interactions.

    Wraps:
    - CLOB API (via py-clob-client) for order operations
    - Gamma API for market discovery
    - Data API for portfolio and whale tracking
    """

    def __init__(self, settings: Settings, strategy_config: StrategyConfig):
        self.settings = settings
        self.strategy_config = strategy_config
        self._clob_client: ClobClient | None = None
        self._http_client: httpx.AsyncClient | None = None

    async def initialize(self) -> None:
        """Initialize API connections."""
        # Initialize CLOB client
        pk = self.settings.wallet_private_key.get_secret_value()
        if pk:
            self._clob_client = ClobClient(
                host=self.settings.polymarket_host,
                key=self.settings.polymarket_api_key,
                secret=self.settings.polymarket_api_secret,
                passphrase=self.settings.polymarket_api_passphrase,
                signature_type=1,  # MetaMask/Web3 wallet
                chain_id=self.settings.chain_id,
                funder=self.settings.funder_address or None,
            )
            logger.info("clob_client_initialized", host=self.settings.polymarket_host)
        else:
            logger.warning("clob_client_skipped", reason="no private key configured")

        # Initialize async HTTP client for Gamma/Data APIs
        self._http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            headers={"Accept": "application/json"},
        )
        logger.info("http_client_initialized")

    async def close(self) -> None:
        """Close all connections."""
        if self._http_client:
            await self._http_client.aclose()
            logger.info("http_client_closed")

    @property
    def clob(self) -> ClobClient:
        """Get the CLOB client. Raises if not initialized."""
        if self._clob_client is None:
            raise RuntimeError("CLOB client not initialized. Call initialize() first.")
        return self._clob_client

    @property
    def http(self) -> httpx.AsyncClient:
        """Get the HTTP client. Raises if not initialized."""
        if self._http_client is None:
            raise RuntimeError("HTTP client not initialized. Call initialize() first.")
        return self._http_client

    # ─── Gamma API: Market Discovery ──────────────────────────────

    async def get_markets(
        self,
        limit: int = 50,
        active: bool = True,
        sort_by: str = "volume",
        category: str | None = None,
        min_volume: float = 0,
        min_liquidity: float = 0,
    ) -> list[Market]:
        """Fetch markets from Gamma API with filtering.

        Addresses: CORE-04
        """
        params: dict[str, Any] = {
            "limit": limit,
            "active": str(active).lower(),
            "sort_by": sort_by,
        }
        if category:
            params["tag"] = category

        url = f"{self.settings.gamma_api_url}/markets"
        resp = await self.http.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()

        markets = []
        for item in data:
            try:
                market = Market.from_gamma(item)
                # Apply filters
                if market.volume < min_volume:
                    continue
                if market.liquidity < min_liquidity:
                    continue
                if not market.yes_token_id or not market.no_token_id:
                    continue
                markets.append(market)
            except (KeyError, ValueError, IndexError) as e:
                logger.warning("market_parse_error", error=str(e), raw=item.get("question", "?"))

        logger.info("markets_fetched", count=len(markets), total_raw=len(data))
        return markets

    async def get_market(self, condition_id: str) -> Market | None:
        """Fetch a single market by condition ID."""
        url = f"{self.settings.gamma_api_url}/markets/{condition_id}"
        resp = await self.http.get(url)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return Market.from_gamma(resp.json())

    # ─── CLOB API: Order Operations ──────────────────────────────

    def create_and_place_order(
        self,
        token_id: str,
        side: str,
        price: float,
        size: float,
        order_type: str = "GTC",
        expiration: int | None = None,
    ) -> OrderResult:
        """Create and submit an order via CLOB API.

        Addresses: CORE-05
        Args:
            token_id: The Yes or No token ID
            side: "BUY" or "SELL"
            price: Price in decimal (0.45 = 45 cents)
            size: Number of shares
            order_type: "GTC", "FOK", or "IOC"
            expiration: Optional expiration in seconds (GTC only)
        """
        try:
            order_args: dict[str, Any] = {
                "token_id": token_id,
                "price": price,
                "size": size,
                "side": side,
            }

            if order_type == "FOK":
                order_args["time_in_force"] = "FOK"
            elif order_type == "IOC":
                order_args["time_in_force"] = "IOC"
            elif expiration:
                order_args["expiration"] = expiration

            resp = self.clob.create_order(order_args)

            # Parse response
            if isinstance(resp, dict):
                order_id = resp.get("orderID", resp.get("id", ""))
                success = bool(order_id) or resp.get("success", False)
                error = resp.get("errorMsg", resp.get("error", ""))
            else:
                order_id = str(resp) if resp else ""
                success = bool(resp)
                error = ""

            result = OrderResult(
                success=success,
                order_id=order_id,
                error=error,
                raw=resp if isinstance(resp, dict) else {"response": str(resp)},
            )

            logger.info(
                "order_placed",
                success=result.success,
                order_id=result.order_id,
                side=side,
                price=price,
                size=size,
                token_id=token_id[:16] + "...",
                order_type=order_type,
            )
            return result

        except Exception as e:
            logger.error("order_placement_failed", error=str(e), side=side, price=price, size=size)
            return OrderResult(success=False, error=str(e))

    def cancel_order(self, order_id: str) -> bool:
        """Cancel a specific order.

        Addresses: CORE-06
        """
        try:
            self.clob.cancel(order_id)
            logger.info("order_cancelled", order_id=order_id)
            return True
        except Exception as e:
            logger.error("order_cancel_failed", order_id=order_id, error=str(e))
            return False

    def cancel_all_orders(self) -> bool:
        """Cancel all open orders.

        Addresses: CORE-06, RISK-05 (kill switch)
        """
        try:
            self.clob.cancel_all()
            logger.info("all_orders_cancelled")
            return True
        except Exception as e:
            logger.error("cancel_all_failed", error=str(e))
            return False

    def get_open_orders(self) -> list[dict[str, Any]]:
        """Get all open orders."""
        try:
            orders = self.clob.get_orders()
            if isinstance(orders, list):
                return orders
            return orders if orders else []
        except Exception as e:
            logger.error("get_orders_failed", error=str(e))
            return []

    # ─── Data API: Portfolio & Whale Tracking ────────────────────

    async def get_positions(self, wallet_address: str | None = None) -> list[dict[str, Any]]:
        """Get positions for a wallet address.

        If wallet_address is None, returns the bot's own positions.
        Used for copy trading (tracking whale wallets).
        """
        if wallet_address is None:
            # Use CLOB client for own positions
            try:
                positions = self.clob.get_positions()
                return positions if isinstance(positions, list) else []
            except Exception as e:
                logger.error("get_own_positions_failed", error=str(e))
                return []

        # Use Data API for external wallet positions
        url = f"{self.settings.data_api_url}/positions"
        params = {"user": wallet_address}
        try:
            resp = await self.http.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.error(
                "get_wallet_positions_failed",
                wallet=wallet_address[:10] + "...",
                error=str(e),
            )
            return []

    async def get_price(self, token_id: str) -> float | None:
        """Get current price for a token from the order book."""
        try:
            book = self.clob.get_order_book(token_id)
            if book and hasattr(book, "bids") and book.bids:
                return float(book.bids[0].price)
            return None
        except Exception as e:
            logger.error("get_price_failed", token_id=token_id[:16] + "...", error=str(e))
            return None
