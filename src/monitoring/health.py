"""Health check module for monitoring bot system status.

Foundation for DEPLOY-03. Checks API connectivity, WebSocket status,
database connection, and wallet balance.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING

import structlog

from ..core.client import PolymarketClient
from ..core.db import Database
from ..core.wallet import WalletManager
from ..core.websocket import WebSocketManager

if TYPE_CHECKING:
    from ..notifications.telegram import TelegramNotifier

logger = structlog.get_logger()


class ComponentStatus(str, Enum):
    """Health status for a single component."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DOWN = "down"
    UNKNOWN = "unknown"


@dataclass
class ComponentHealth:
    """Health report for one component."""

    name: str
    status: ComponentStatus
    message: str = ""
    latency_ms: float | None = None


@dataclass
class SystemHealth:
    """Aggregated health report for all components."""

    timestamp: datetime
    overall: ComponentStatus
    components: list[ComponentHealth]
    uptime_seconds: float

    @property
    def is_healthy(self) -> bool:
        return self.overall == ComponentStatus.HEALTHY

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "overall": self.overall.value,
            "uptime_seconds": round(self.uptime_seconds, 1),
            "components": [
                {
                    "name": c.name,
                    "status": c.status.value,
                    "message": c.message,
                    "latency_ms": c.latency_ms,
                }
                for c in self.components
            ],
        }


class HealthChecker:
    """Monitors the health of all bot subsystems."""

    def __init__(
        self,
        client: PolymarketClient,
        db: Database,
        wallet: WalletManager,
        ws_manager: WebSocketManager,
        notifier: TelegramNotifier | None = None,
    ) -> None:
        self._client = client
        self._db = db
        self._wallet = wallet
        self._ws = ws_manager
        self._notifier = notifier
        self._start_time = datetime.now(timezone.utc)

    @property
    def uptime_seconds(self) -> float:
        return (datetime.now(timezone.utc) - self._start_time).total_seconds()

    def check_websocket(self) -> ComponentHealth:
        """Check WebSocket connection status."""
        if self._ws.is_connected and not self._ws.is_stale:
            return ComponentHealth(
                name="websocket",
                status=ComponentStatus.HEALTHY,
                message=f"Connected, last msg {self._ws.seconds_since_last_message:.0f}s ago",
            )
        elif self._ws.is_connected and self._ws.is_stale:
            return ComponentHealth(
                name="websocket",
                status=ComponentStatus.DEGRADED,
                message=f"Connected but stale ({self._ws.seconds_since_last_message:.0f}s)",
            )
        else:
            return ComponentHealth(
                name="websocket",
                status=ComponentStatus.DOWN,
                message="Disconnected",
            )

    def check_database(self) -> ComponentHealth:
        """Check database connectivity."""
        try:
            # Simple query to verify DB is accessible
            count = self._db.count_open_positions()
            return ComponentHealth(
                name="database",
                status=ComponentStatus.HEALTHY,
                message=f"{count} open positions tracked",
            )
        except Exception as e:
            return ComponentHealth(
                name="database",
                status=ComponentStatus.DOWN,
                message=f"Error: {e}",
            )

    def check_wallet(self) -> ComponentHealth:
        """Check wallet balance."""
        try:
            balance = self._wallet.get_usdc_balance()
            if balance < 1.0:
                return ComponentHealth(
                    name="wallet",
                    status=ComponentStatus.DEGRADED,
                    message=f"Low USDC balance: ${balance:.2f}",
                )
            return ComponentHealth(
                name="wallet",
                status=ComponentStatus.HEALTHY,
                message=f"USDC: ${balance:.2f}",
            )
        except Exception as e:
            return ComponentHealth(
                name="wallet",
                status=ComponentStatus.DOWN,
                message=f"Error: {e}",
            )

    async def check_api(self) -> ComponentHealth:
        """Check CLOB/Gamma API connectivity."""
        try:
            import time

            start = time.monotonic()
            markets = await self._client.get_markets(limit=1)
            latency = (time.monotonic() - start) * 1000

            if markets:
                return ComponentHealth(
                    name="api",
                    status=ComponentStatus.HEALTHY,
                    message="Gamma API responsive",
                    latency_ms=round(latency, 1),
                )
            else:
                return ComponentHealth(
                    name="api",
                    status=ComponentStatus.DEGRADED,
                    message="API returned no markets",
                    latency_ms=round(latency, 1),
                )
        except Exception as e:
            return ComponentHealth(
                name="api",
                status=ComponentStatus.DOWN,
                message=f"Error: {e}",
            )

    async def get_system_health(self) -> SystemHealth:
        """Run all health checks and return aggregated result."""
        # Run sync checks immediately, async check awaited
        components = [
            self.check_database(),
            self.check_wallet(),
            self.check_websocket(),
            await self.check_api(),
        ]

        # Determine overall status
        statuses = [c.status for c in components]
        if ComponentStatus.DOWN in statuses:
            overall = ComponentStatus.DOWN
        elif ComponentStatus.DEGRADED in statuses:
            overall = ComponentStatus.DEGRADED
        else:
            overall = ComponentStatus.HEALTHY

        health = SystemHealth(
            timestamp=datetime.now(timezone.utc),
            overall=overall,
            components=components,
            uptime_seconds=self.uptime_seconds,
        )

        logger.info(
            "health_check",
            overall=overall.value,
            uptime_seconds=round(self.uptime_seconds, 1),
            components={c.name: c.status.value for c in components},
        )

        return health
