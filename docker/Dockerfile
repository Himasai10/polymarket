# ============================================================
# Polymarket Trading Bot — Multi-stage production Dockerfile
# ============================================================
# DEPLOY-01: docker-compose up starts the bot with all deps
# DEPLOY-02: Restart policy handled in docker-compose.yml
# DEPLOY-04: SIGTERM triggers clean shutdown (asyncio signal handlers)
# ============================================================

# --- Stage 1: Build dependencies ---
FROM python:3.13-slim AS builder

# Install uv for fast dependency resolution
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Copy dependency files first (layer caching)
COPY pyproject.toml uv.lock ./
COPY README.md ./

# Install production dependencies only (no dev deps)
RUN uv sync --frozen --no-dev --no-install-project

# Copy source code
COPY src/ src/
COPY config/ config/
COPY scripts/ scripts/

# Install the project itself
RUN uv sync --frozen --no-dev


# --- Stage 2: Production runtime ---
FROM python:3.13-slim AS runtime

# Security: run as non-root user
RUN groupadd -r polybot && useradd -r -g polybot -d /app -s /sbin/nologin polybot

WORKDIR /app

# Copy the virtual environment from builder
COPY --from=builder /app/.venv /app/.venv

# Copy source and config
COPY --from=builder /app/src /app/src
COPY --from=builder /app/config /app/config
COPY --from=builder /app/scripts /app/scripts
COPY --from=builder /app/pyproject.toml /app/pyproject.toml
COPY --from=builder /app/README.md /app/README.md

# Create data directory for SQLite persistence (mounted as volume)
RUN mkdir -p /app/data /app/logs && chown -R polybot:polybot /app

# Environment
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # Default log level for production
    LOG_LEVEL=INFO \
    # Database path inside container (persisted via volume)
    DATABASE_URL=sqlite:///app/data/polybot.db

# Health check: hit the HTTP health endpoint
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')" || exit 1

# Switch to non-root user
USER polybot

# Expose health check port
EXPOSE 8080

# Graceful shutdown: Docker sends SIGTERM, our bot handles it
STOPSIGNAL SIGTERM

# Entry point
ENTRYPOINT ["python", "-m", "src.main"]
