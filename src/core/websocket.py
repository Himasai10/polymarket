"""
WebSocket manager for real-time order book and price feeds.

Addresses: CORE-10
Prevents: Pitfall 7 (WebSocket disconnections - auto-reconnect with backoff)

Audit fixes applied:
- H-20: Auth headers added when API key is available
- H-21: Clear stale WS reference on disconnect, resubscribe on reconnect
- M-14: Application-level heartbeat monitoring to detect dead connections
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable, Coroutine
from typing import Any

import structlog
import websockets

from .config import Settings

logger = structlog.get_logger()

# Type alias for price update callbacks
PriceCallback = Callable[[str, float, float], Coroutine[Any, Any, None]]
# callback(token_id, price, timestamp)

# M-14: Heartbeat / stale data thresholds
_STALE_THRESHOLD_SEC = 30  # Data is stale after 30s silence
_HEARTBEAT_CHECK_INTERVAL_SEC = 10  # Check for staleness every 10s
_FORCE_RECONNECT_AFTER_SEC = 60  # Force reconnect if no data for 60s


class WebSocketManager:
    """Manages WebSocket connection to Polymarket CLOB for real-time data.

    Features:
    - Auto-reconnect with exponential backoff
    - Subscribe/unsubscribe to specific token price feeds
    - Distributes price updates to registered callbacks
    - Stale data detection with forced reconnect (M-14)
    - Clear stale references on disconnect (H-21)
    - Auth headers when API key available (H-20)
    """

    def __init__(self, settings: Settings):
        self.ws_url = settings.ws_url
        self._settings = settings
        self._ws: Any = None
        self._subscribed_tokens: set[str] = set()
        self._callbacks: list[PriceCallback] = []
        self._last_message_time: float = 0
        self._running: bool = False
        self._reconnect_delay: float = 1.0
        self._max_reconnect_delay: float = 60.0
        self._latest_prices: dict[str, float] = {}
        self._heartbeat_task: asyncio.Task[None] | None = None

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
        # Remove stale prices for unsubscribed tokens
        for tid in token_ids:
            self._latest_prices.pop(tid, None)
        if self.is_connected:
            asyncio.ensure_future(self._send_unsubscribe(token_ids))

    def get_latest_price(self, token_id: str) -> float | None:
        """Get last known price for a token.

        Returns None if data is stale (no updates for >30s).
        """
        if self.is_stale:
            return None
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
        """Data is stale if no message received in threshold seconds."""
        return self.seconds_since_last_message > _STALE_THRESHOLD_SEC

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
                # H-21 FIX: Clear stale reference on disconnect
                self._clear_connection()
                await self._backoff()

    async def stop(self) -> None:
        """Stop the WebSocket connection."""
        self._running = False
        # Cancel heartbeat monitor
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
            self._heartbeat_task = None
        if self._ws and not self._ws.closed:
            await self._ws.close()
        # H-21 FIX: Clear reference on stop
        self._clear_connection()
        logger.info("ws_closed")

    def _clear_connection(self) -> None:
        """H-21 FIX: Clear stale WebSocket reference and mark data as stale."""
        self._ws = None
        self._last_message_time = 0  # Mark as stale

    async def _connect_and_listen(self) -> None:
        """Connect to WebSocket and process messages."""
        # Skip WebSocket in paper mode without wallet - can't authenticate
        if not self._settings.is_live:
            logger.info("ws_skipped_paper_mode", reason="WebSocket requires wallet auth")
            self._running = False
            return

        logger.info("ws_connecting", url=self.ws_url)

        # H-20 FIX: Build extra headers with API key if available
        extra_headers = self._build_auth_headers()

        async with websockets.connect(
            self.ws_url,
            ping_interval=20,
            ping_timeout=10,
            additional_headers=extra_headers if extra_headers else None,
        ) as ws:
            self._ws = ws
            self._reconnect_delay = 1.0  # Reset backoff on successful connect
            logger.info("ws_connected")

            # H-21 FIX: Re-subscribe to all tracked tokens on reconnect
            if self._subscribed_tokens:
                await self._send_subscribe(list(self._subscribed_tokens))

            # M-14 FIX: Start heartbeat monitor
            self._heartbeat_task = asyncio.create_task(self._heartbeat_monitor())

            try:
                # Listen for messages
                async for message in ws:
                    self._last_message_time = time.monotonic()
                    await self._handle_message(message)
            finally:
                # Cancel heartbeat on disconnect
                if self._heartbeat_task and not self._heartbeat_task.done():
                    self._heartbeat_task.cancel()
                    self._heartbeat_task = None
                # H-21 FIX: Clear reference after disconnect
                self._clear_connection()

    def _build_auth_headers(self) -> dict[str, str]:
        """H-20 FIX: Build auth headers if API key is available.

        Polymarket's public WS doesn't require auth for book data,
        but including the key may provide higher rate limits or access.
        """
        headers: dict[str, str] = {}
        api_key = self._settings.polymarket_api_key.get_secret_value()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    # M-14 FIX: Application-level heartbeat monitor
    async def _heartbeat_monitor(self) -> None:
        """Monitor for dead connections and force reconnect if needed.

        The websockets library handles protocol-level ping/pong,
        but this detects application-level silence (no market data).
        """
        try:
            while self._running and self.is_connected:
                await asyncio.sleep(_HEARTBEAT_CHECK_INTERVAL_SEC)

                silence = self.seconds_since_last_message
                if silence > _FORCE_RECONNECT_AFTER_SEC:
                    logger.warning(
                        "ws_force_reconnect",
                        silence_seconds=round(silence, 1),
                        threshold=_FORCE_RECONNECT_AFTER_SEC,
                    )
                    # Force close — outer loop will reconnect
                    if self._ws and not self._ws.closed:
                        await self._ws.close()
                    break
                elif silence > _STALE_THRESHOLD_SEC:
                    logger.warning(
                        "ws_data_stale",
                        silence_seconds=round(silence, 1),
                        subscribed_tokens=len(self._subscribed_tokens),
                    )
        except asyncio.CancelledError:
            pass

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
