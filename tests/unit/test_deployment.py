"""Tests for Phase 5: Deployment & Production Hardening.

Covers:
- DEPLOY-01: Dockerfile and docker-compose validation
- DEPLOY-03: HTTP health check server
- DEPLOY-04: Graceful shutdown (SIGTERM handling)
- CORE-09: Production log rotation
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.monitoring.health import ComponentHealth, ComponentStatus, HealthChecker, SystemHealth
from src.monitoring.health_server import HealthServer


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_health_checker() -> MagicMock:
    """Provide a mock HealthChecker that returns controllable health results."""
    checker = MagicMock(spec=HealthChecker)
    checker.uptime_seconds = 1234.5

    healthy_result = SystemHealth(
        timestamp=MagicMock(),
        overall=ComponentStatus.HEALTHY,
        components=[
            ComponentHealth(name="database", status=ComponentStatus.HEALTHY, message="OK"),
            ComponentHealth(name="api", status=ComponentStatus.HEALTHY, message="OK"),
            ComponentHealth(name="websocket", status=ComponentStatus.HEALTHY, message="OK"),
            ComponentHealth(name="wallet", status=ComponentStatus.HEALTHY, message="$500"),
        ],
        uptime_seconds=1234.5,
    )

    checker.get_system_health = AsyncMock(return_value=healthy_result)
    return checker


@pytest.fixture
def mock_unhealthy_checker() -> MagicMock:
    """Provide a mock HealthChecker that returns unhealthy status."""
    checker = MagicMock(spec=HealthChecker)

    unhealthy_result = SystemHealth(
        timestamp=MagicMock(),
        overall=ComponentStatus.DOWN,
        components=[
            ComponentHealth(name="database", status=ComponentStatus.HEALTHY, message="OK"),
            ComponentHealth(name="api", status=ComponentStatus.DOWN, message="Connection refused"),
        ],
        uptime_seconds=5.0,
    )

    checker.get_system_health = AsyncMock(return_value=unhealthy_result)
    return checker


# ---------------------------------------------------------------------------
# HealthServer Tests
# ---------------------------------------------------------------------------


class TestHealthServer:
    """Test the HTTP health check server."""

    def test_init_defaults(self, mock_health_checker: MagicMock) -> None:
        server = HealthServer(mock_health_checker)
        assert server._host == "0.0.0.0"
        assert server._port == 8080
        assert server._ready is False

    def test_set_ready(self, mock_health_checker: MagicMock) -> None:
        server = HealthServer(mock_health_checker)
        assert server._ready is False
        server.set_ready(True)
        assert server._ready is True
        server.set_ready(False)
        assert server._ready is False

    @pytest.mark.asyncio
    async def test_liveness_endpoint(self, mock_health_checker: MagicMock) -> None:
        """GET / should return 200 with alive status."""
        server = HealthServer(mock_health_checker, port=0)

        # Start server on random port
        tcp_server = await asyncio.start_server(server._handle_connection, "127.0.0.1", 0)
        port = tcp_server.sockets[0].getsockname()[1]

        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
            await writer.drain()

            response = await asyncio.wait_for(reader.read(4096), timeout=5.0)
            response_str = response.decode()

            assert "200 OK" in response_str
            body = response_str.split("\r\n\r\n", 1)[1]
            data = json.loads(body)
            assert data["status"] == "alive"

            writer.close()
            await writer.wait_closed()
        finally:
            tcp_server.close()
            await tcp_server.wait_closed()

    @pytest.mark.asyncio
    async def test_health_endpoint_healthy(self, mock_health_checker: MagicMock) -> None:
        """GET /health should return 200 when system is healthy."""
        server = HealthServer(mock_health_checker, port=0)

        tcp_server = await asyncio.start_server(server._handle_connection, "127.0.0.1", 0)
        port = tcp_server.sockets[0].getsockname()[1]

        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"GET /health HTTP/1.1\r\nHost: localhost\r\n\r\n")
            await writer.drain()

            response = await asyncio.wait_for(reader.read(4096), timeout=5.0)
            response_str = response.decode()

            assert "200 OK" in response_str
            body = response_str.split("\r\n\r\n", 1)[1]
            data = json.loads(body)
            assert data["overall"] == "healthy"

            writer.close()
            await writer.wait_closed()
        finally:
            tcp_server.close()
            await tcp_server.wait_closed()

    @pytest.mark.asyncio
    async def test_health_endpoint_unhealthy(self, mock_unhealthy_checker: MagicMock) -> None:
        """GET /health should return 503 when system is unhealthy."""
        server = HealthServer(mock_unhealthy_checker, port=0)

        tcp_server = await asyncio.start_server(server._handle_connection, "127.0.0.1", 0)
        port = tcp_server.sockets[0].getsockname()[1]

        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"GET /health HTTP/1.1\r\nHost: localhost\r\n\r\n")
            await writer.drain()

            response = await asyncio.wait_for(reader.read(4096), timeout=5.0)
            response_str = response.decode()

            assert "503 Service Unavailable" in response_str
            body = response_str.split("\r\n\r\n", 1)[1]
            data = json.loads(body)
            assert data["overall"] == "down"

            writer.close()
            await writer.wait_closed()
        finally:
            tcp_server.close()
            await tcp_server.wait_closed()

    @pytest.mark.asyncio
    async def test_ready_endpoint_not_ready(self, mock_health_checker: MagicMock) -> None:
        """GET /ready should return 503 when not ready."""
        server = HealthServer(mock_health_checker, port=0)
        # Don't call set_ready

        tcp_server = await asyncio.start_server(server._handle_connection, "127.0.0.1", 0)
        port = tcp_server.sockets[0].getsockname()[1]

        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"GET /ready HTTP/1.1\r\nHost: localhost\r\n\r\n")
            await writer.drain()

            response = await asyncio.wait_for(reader.read(4096), timeout=5.0)
            response_str = response.decode()

            assert "503" in response_str
            body = response_str.split("\r\n\r\n", 1)[1]
            data = json.loads(body)
            assert data["ready"] is False

            writer.close()
            await writer.wait_closed()
        finally:
            tcp_server.close()
            await tcp_server.wait_closed()

    @pytest.mark.asyncio
    async def test_ready_endpoint_when_ready(self, mock_health_checker: MagicMock) -> None:
        """GET /ready should return 200 when bot is ready."""
        server = HealthServer(mock_health_checker, port=0)
        server.set_ready(True)

        tcp_server = await asyncio.start_server(server._handle_connection, "127.0.0.1", 0)
        port = tcp_server.sockets[0].getsockname()[1]

        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"GET /ready HTTP/1.1\r\nHost: localhost\r\n\r\n")
            await writer.drain()

            response = await asyncio.wait_for(reader.read(4096), timeout=5.0)
            response_str = response.decode()

            assert "200 OK" in response_str
            body = response_str.split("\r\n\r\n", 1)[1]
            data = json.loads(body)
            assert data["ready"] is True

            writer.close()
            await writer.wait_closed()
        finally:
            tcp_server.close()
            await tcp_server.wait_closed()

    @pytest.mark.asyncio
    async def test_404_unknown_path(self, mock_health_checker: MagicMock) -> None:
        """Unknown paths should return 404."""
        server = HealthServer(mock_health_checker, port=0)

        tcp_server = await asyncio.start_server(server._handle_connection, "127.0.0.1", 0)
        port = tcp_server.sockets[0].getsockname()[1]

        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"GET /nonexistent HTTP/1.1\r\nHost: localhost\r\n\r\n")
            await writer.drain()

            response = await asyncio.wait_for(reader.read(4096), timeout=5.0)
            response_str = response.decode()

            assert "404" in response_str

            writer.close()
            await writer.wait_closed()
        finally:
            tcp_server.close()
            await tcp_server.wait_closed()

    @pytest.mark.asyncio
    async def test_server_stop(self, mock_health_checker: MagicMock) -> None:
        """Server should stop cleanly."""
        server = HealthServer(mock_health_checker, host="127.0.0.1", port=0)

        # Start the actual server
        server._server = await asyncio.start_server(server._handle_connection, "127.0.0.1", 0)

        # Should stop without error
        await server.stop()
        assert server._server is not None  # Reference kept but server closed


# ---------------------------------------------------------------------------
# Docker Config Validation Tests
# ---------------------------------------------------------------------------


class TestDockerConfig:
    """Validate Docker configuration files exist and have correct structure."""

    def test_dockerfile_exists(self) -> None:
        dockerfile = Path(__file__).resolve().parents[2] / "docker" / "Dockerfile"
        assert dockerfile.exists(), "docker/Dockerfile must exist"

    def test_dockerfile_has_healthcheck(self) -> None:
        dockerfile = Path(__file__).resolve().parents[2] / "docker" / "Dockerfile"
        content = dockerfile.read_text()
        assert "HEALTHCHECK" in content, "Dockerfile must have HEALTHCHECK instruction"

    def test_dockerfile_has_sigterm(self) -> None:
        dockerfile = Path(__file__).resolve().parents[2] / "docker" / "Dockerfile"
        content = dockerfile.read_text()
        assert "STOPSIGNAL SIGTERM" in content, "Dockerfile must use SIGTERM for graceful shutdown"

    def test_dockerfile_non_root_user(self) -> None:
        dockerfile = Path(__file__).resolve().parents[2] / "docker" / "Dockerfile"
        content = dockerfile.read_text()
        assert "USER polybot" in content, "Dockerfile must run as non-root user"

    def test_dockerfile_multi_stage(self) -> None:
        dockerfile = Path(__file__).resolve().parents[2] / "docker" / "Dockerfile"
        content = dockerfile.read_text()
        assert content.count("FROM ") >= 2, "Dockerfile must use multi-stage build"

    def test_compose_exists(self) -> None:
        compose = Path(__file__).resolve().parents[2] / "docker" / "docker-compose.yml"
        assert compose.exists(), "docker/docker-compose.yml must exist"

    def test_compose_has_restart_policy(self) -> None:
        compose = Path(__file__).resolve().parents[2] / "docker" / "docker-compose.yml"
        content = compose.read_text()
        assert "restart:" in content, "docker-compose must have restart policy"
        assert "unless-stopped" in content, "Restart policy should be unless-stopped"

    def test_compose_has_volumes(self) -> None:
        compose = Path(__file__).resolve().parents[2] / "docker" / "docker-compose.yml"
        content = compose.read_text()
        assert "polybot-data" in content, "docker-compose must persist data volume"
        assert "polybot-logs" in content, "docker-compose must persist logs volume"

    def test_compose_has_stop_grace_period(self) -> None:
        compose = Path(__file__).resolve().parents[2] / "docker" / "docker-compose.yml"
        content = compose.read_text()
        assert "stop_grace_period" in content, (
            "docker-compose must have stop_grace_period for clean shutdown"
        )


# ---------------------------------------------------------------------------
# Production Logging Tests
# ---------------------------------------------------------------------------


class TestProductionLogging:
    """Test production logging configuration."""

    def test_setup_logging_with_rotation(self, tmp_path: Path) -> None:
        """Log rotation should create RotatingFileHandler."""
        import logging

        from src.monitoring.logger import setup_logging

        log_file = str(tmp_path / "test.log")

        # Clear existing handlers
        root = logging.getLogger()
        root.handlers.clear()

        setup_logging(log_level="INFO", json_output=True, log_file=log_file)

        # Find the rotating file handler
        rotating_handlers = [
            h for h in root.handlers if isinstance(h, logging.handlers.RotatingFileHandler)
        ]
        assert len(rotating_handlers) == 1, "Should have exactly one RotatingFileHandler"

        handler = rotating_handlers[0]
        assert handler.maxBytes == 10 * 1024 * 1024  # 10 MB default
        assert handler.backupCount == 5

        # Cleanup
        root.handlers.clear()

    def test_setup_logging_custom_rotation(self, tmp_path: Path) -> None:
        """Custom rotation parameters should be respected."""
        import logging

        from src.monitoring.logger import setup_logging

        log_file = str(tmp_path / "test.log")

        root = logging.getLogger()
        root.handlers.clear()

        setup_logging(
            log_level="DEBUG",
            json_output=True,
            log_file=log_file,
            max_bytes=5 * 1024 * 1024,
            backup_count=3,
        )

        rotating_handlers = [
            h for h in root.handlers if isinstance(h, logging.handlers.RotatingFileHandler)
        ]
        assert len(rotating_handlers) == 1

        handler = rotating_handlers[0]
        assert handler.maxBytes == 5 * 1024 * 1024
        assert handler.backupCount == 3

        # Cleanup
        root.handlers.clear()


# ---------------------------------------------------------------------------
# Config Tests
# ---------------------------------------------------------------------------


class TestDeploymentConfig:
    """Test deployment-related configuration."""

    def test_health_port_default(self) -> None:
        """health_port should default to 8080."""
        from src.core.config import Settings

        s = Settings(
            polymarket_api_key="k",
            polymarket_api_secret="s",
            polymarket_api_passphrase="p",
            wallet_private_key="0x" + "ab" * 32,
        )
        assert s.health_port == 8080

    def test_health_port_override(self) -> None:
        """health_port should be overridable."""
        from src.core.config import Settings

        s = Settings(
            polymarket_api_key="k",
            polymarket_api_secret="s",
            polymarket_api_passphrase="p",
            wallet_private_key="0x" + "ab" * 32,
            health_port=9090,
        )
        assert s.health_port == 9090

    def test_env_example_has_health_port(self) -> None:
        """config/.env.example should document HEALTH_PORT."""
        env_example = Path(__file__).resolve().parents[2] / "config" / ".env.example"
        content = env_example.read_text()
        assert "HEALTH_PORT" in content
