"""Lightweight HTTP health endpoint for Docker and external monitoring.

DEPLOY-03: Health checks via HTTP endpoint.
Uses stdlib asyncio.Server — no extra dependencies.

Endpoints:
    GET /health  → 200 with JSON health report (or 503 if unhealthy)
    GET /ready   → 200 when bot is initialized and accepting trades
    GET /         → 200 simple liveness probe
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from .health import HealthChecker

logger = structlog.get_logger()

# Simple HTTP response templates
_HTTP_200 = (
    "HTTP/1.1 200 OK\r\n"
    "Content-Type: application/json\r\n"
    "Content-Length: {length}\r\n"
    "Connection: close\r\n\r\n"
    "{body}"
)

_HTTP_503 = (
    "HTTP/1.1 503 Service Unavailable\r\n"
    "Content-Type: application/json\r\n"
    "Content-Length: {length}\r\n"
    "Connection: close\r\n\r\n"
    "{body}"
)

_HTTP_404 = (
    "HTTP/1.1 404 Not Found\r\n"
    "Content-Type: application/json\r\n"
    "Content-Length: {length}\r\n"
    "Connection: close\r\n\r\n"
    "{body}"
)


class HealthServer:
    """Minimal async HTTP server for health checks.

    Runs alongside the bot event loop. No external dependencies — uses
    asyncio.start_server directly with raw HTTP parsing.
    """

    def __init__(
        self,
        health_checker: HealthChecker,
        host: str = "0.0.0.0",
        port: int = 8080,
    ) -> None:
        self._health_checker = health_checker
        self._host = host
        self._port = port
        self._server: asyncio.Server | None = None
        self._ready = False

    def set_ready(self, ready: bool = True) -> None:
        """Mark the bot as ready (called after initialization completes)."""
        self._ready = ready

    async def start(self) -> None:
        """Start the health HTTP server."""
        self._server = await asyncio.start_server(self._handle_connection, self._host, self._port)
        logger.info("health_server_started", host=self._host, port=self._port)

        async with self._server:
            await self._server.serve_forever()

    async def stop(self) -> None:
        """Stop the health HTTP server."""
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            logger.info("health_server_stopped")

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Handle an incoming HTTP connection."""
        try:
            # Read the request line (we only need the method + path)
            request_line = await asyncio.wait_for(reader.readline(), timeout=5.0)
            request_str = request_line.decode("utf-8", errors="replace").strip()

            # Parse method and path
            parts = request_str.split(" ")
            if len(parts) < 2:
                await self._send_response(writer, 404, {"error": "bad request"})
                return

            path = parts[1]

            # Drain remaining headers (we don't need them)
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=5.0)
                if line in (b"\r\n", b"\n", b""):
                    break

            # Route
            if path == "/":
                await self._handle_liveness(writer)
            elif path == "/health":
                await self._handle_health(writer)
            elif path == "/ready":
                await self._handle_readiness(writer)
            else:
                await self._send_response(writer, 404, {"error": "not found"})

        except asyncio.TimeoutError:
            pass
        except Exception:
            logger.debug("health_server_connection_error", exc_info=True)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _handle_liveness(self, writer: asyncio.StreamWriter) -> None:
        """GET / — simple liveness probe."""
        await self._send_response(writer, 200, {"status": "alive"})

    async def _handle_health(self, writer: asyncio.StreamWriter) -> None:
        """GET /health — full health check with component status."""
        try:
            health = await self._health_checker.get_system_health()
            status_code = 200 if health.is_healthy else 503
            await self._send_response(writer, status_code, health.to_dict())
        except Exception as e:
            await self._send_response(writer, 503, {"overall": "down", "error": str(e)})

    async def _handle_readiness(self, writer: asyncio.StreamWriter) -> None:
        """GET /ready — is the bot initialized and accepting trades?"""
        if self._ready:
            await self._send_response(writer, 200, {"ready": True})
        else:
            await self._send_response(writer, 503, {"ready": False})

    async def _send_response(
        self,
        writer: asyncio.StreamWriter,
        status_code: int,
        body: dict,
    ) -> None:
        """Send an HTTP response."""
        body_json = json.dumps(body, default=str)
        if status_code == 200:
            response = _HTTP_200.format(length=len(body_json), body=body_json)
        elif status_code == 503:
            response = _HTTP_503.format(length=len(body_json), body=body_json)
        else:
            response = _HTTP_404.format(length=len(body_json), body=body_json)

        writer.write(response.encode("utf-8"))
        await writer.drain()
