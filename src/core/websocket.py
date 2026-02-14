"""
WebSocket manager for real-time order book and price feeds.

Addresses: CORE-10
Prevents: Pitfall 7 (WebSocket disconnections - auto-reconnect with backoff)
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict
from typing import Any, Callable, Coroutine

import structlog
import websockets
from websockets.exceptions import ConnectionClosed

from .config import Settings

logger = structlog.get_logger()

# Type alias for price update callbacks
PriceCallback = Callable[[str, float, float], Coroutine[Any, Any, None]]
# callback(token_id, price, timestamp)


class WebSocketManager:
    """Manages WebSocket connection to Polymarket CLOB for real-time data.

    Features:
    - Auto-reconnect with exponential backoff
    - Subscribe/unsubscribe to specific token price feeds
    - Distributes price updates to registered callbacks
    - Stale data detection (REST fallback after 30s silence)
    """

    def __init__(self, settings: Settings):
        self.ws_url = settings.ws_url
        self._ws: Any = None
        self._subscribed_tokens: set[str] = set()
        self._callbacks: list[PriceCallback] = []
        self._last_message_time: float = 0
        self._running: bool = False
        self._reconnect_delay: float = 1.0
        self._max_reconnect_delay: float = 60.0
        self._latest_prices: dict[str, float] = {}

    def register_callback(self, callback: PriceCallback) -> None:
        """Register a callback for price updates."""
        self._callbacks.append(callback)

    def subscribe(self, token_ids: list[str]) -> None:
        """Add token IDs to subscription list.

        If already connected, sends subscription message immediately.
        """
        new_tokens = set(token_ids) - self._subscribed_tokens
        self._subscribed_tokens.update(token_ids)
        logger.info("ws_tokens_subscribed", count=len(token_ids), new=len(new_tokens))

        # If already connected, send subscribe for the new tokens immediately
        if new_tokens and self.is_connected:
            asyncio.ensure_future(self._send_subscribe(list(new_tokens)))

    def unsubscribe(self, token_ids: list[str]) -> None:
        """Remove token IDs from subscription list.

        If already connected, sends unsubscribe message immediately.
        """
        self._subscribed_tokens -= set(token_ids)
        if self.is_connected:
            asyncio.ensure_future(self._send_unsubscribe(token_ids))

    def get_latest_price(self, token_id: str) -> float | None:
        """Get last known price for a token."""
        return self._latest_prices.get(token_id)

    @property
    def is_connected(self) -> bool:
        return self._ws is not None and not self._ws.closed

    @property
    def seconds_since_last_message(self) -> float:
        if self._last_message_time == 0:
            return float("inf")
        return time.monotonic() - self._last_message_time

    @property
    def is_stale(self) -> bool:
        """Data is stale if no message received in 30 seconds."""
        return self.seconds_since_last_message > 30

    async def start(self) -> None:
        """Start the WebSocket connection loop."""
        self._running = True
        while self._running:
            try:
                await self._connect_and_listen()
            except Exception as e:
                if not self._running:
                    break
                logger.error("ws_connection_error", error=str(e))
                await self._backoff()

    async def stop(self) -> None:
        """Stop the WebSocket connection."""
        self._running = False
        if self._ws and not self._ws.closed:
            await self._ws.close()
            logger.info("ws_closed")

    async def _connect_and_listen(self) -> None:
        """Connect to WebSocket and process messages."""
        logger.info("ws_connecting", url=self.ws_url)

        async with websockets.connect(self.ws_url, ping_interval=20, ping_timeout=10) as ws:
            self._ws = ws
            self._reconnect_delay = 1.0  # Reset backoff on successful connect
            logger.info("ws_connected")

            # Subscribe to all tracked tokens
            if self._subscribed_tokens:
                await self._send_subscribe(list(self._subscribed_tokens))

            # Listen for messages
            async for message in ws:
                self._last_message_time = time.monotonic()
                await self._handle_message(message)

    async def _send_subscribe(self, token_ids: list[str]) -> None:
        """Send subscription message for token IDs."""
        if not self._ws or self._ws.closed:
            return

        msg = {
            "type": "subscribe",
            "assets_ids": token_ids,
            "channels": ["book"],
        }
        await self._ws.send(json.dumps(msg))
        logger.info("ws_subscribed", token_count=len(token_ids))

    async def _send_unsubscribe(self, token_ids: list[str]) -> None:
        """Send unsubscribe message for token IDs."""
        if not self._ws or self._ws.closed:
            return

        msg = {
            "type": "unsubscribe",
            "assets_ids": token_ids,
            "channels": ["book"],
        }
        await self._ws.send(json.dumps(msg))
        logger.info("ws_unsubscribed", token_count=len(token_ids))

    async def _handle_message(self, raw_message: str | bytes) -> None:
        """Parse and distribute a WebSocket message."""
        try:
            data = json.loads(raw_message)
            msg_type = data.get("type", "")

            if msg_type in ("book", "price_change"):
                token_id = data.get("asset_id", data.get("token_id", ""))
                price = data.get("price", data.get("best_bid", 0))

                if token_id and price:
                    price_float = float(price)
                    timestamp = float(data.get("timestamp", time.time()))
                    self._latest_prices[token_id] = price_float

                    # Notify all callbacks
                    for callback in self._callbacks:
                        try:
                            await callback(token_id, price_float, timestamp)
                        except Exception as e:
                            logger.error("ws_callback_error", error=str(e))

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.debug("ws_message_parse_error", error=str(e))

    async def _backoff(self) -> None:
        """Exponential backoff before reconnection."""
        logger.info("ws_reconnecting", delay=self._reconnect_delay)
        await asyncio.sleep(self._reconnect_delay)
        self._reconnect_delay = min(
            self._reconnect_delay * 2,
            self._max_reconnect_delay,
        )
