# Polymarket Trading Bot

Profit-first automated trading bot for [Polymarket](https://polymarket.com) prediction markets on Polygon. Combines copy trading (whale tracking), parity arbitrage scanning, and stink bid placement into a single 24/7 system.

## Features

- **Copy Trading** -- Track profitable wallets from the Polymarket leaderboard and mirror their trades with configurable sizing (fixed, portfolio %, or whale %)
- **Parity Arbitrage** -- Scan for Yes+No pricing inefficiencies where the combined cost falls below $1.00 after fees
- **Stink Bids** -- Place discounted limit orders (10-30% below market) on high-volume markets and wait for fills
- **Risk Management** -- Per-position limits, daily loss caps, kill switch, cash reserve enforcement, and tiered take-profit/stop-loss
- **Telegram Control** -- `/status`, `/pnl`, `/kill`, `/pause`, `/resume` commands with real-time trade alerts
- **Paper Trading** -- Full simulation mode for testing strategies without risking capital
- **Docker Deployment** -- Single `docker-compose up` with health checks, auto-restart, and persistent storage

## Architecture

```
src/
  main.py              # TradingBot orchestrator, CLI entry point
  core/
    client.py          # Polymarket CLOB API wrapper
    config.py          # Settings (pydantic-settings), YAML loaders
    db.py              # SQLite persistence (positions, orders, P&L)
    wallet.py          # Polygon wallet management (web3)
    websocket.py       # Real-time price feeds via WebSocket
    rate_limiter.py    # Token-bucket rate limiter for API calls
  strategies/
    base.py            # BaseStrategy abstract class
    copy_trader.py     # Whale wallet copy trading
    arb_scanner.py     # Parity arbitrage scanner
    stink_bidder.py    # Discounted limit order placement
  execution/
    order_manager.py   # Order queue, submission, paper-mode simulation
    position_manager.py# TP/SL/trailing stop, market resolution
    risk_manager.py    # Pre-trade checks, kill switch, exposure limits
  monitoring/
    health.py          # Component health checks
    health_server.py   # HTTP /health endpoint (Docker HEALTHCHECK)
    logger.py          # Structured logging (structlog, JSON output)
    pnl.py             # Portfolio P&L tracking and snapshots
  notifications/
    telegram.py        # Alerts + command bot (/status, /kill, etc.)
config/
  .env.example         # Environment variable template
  strategies.yaml      # Strategy parameters, risk thresholds
  wallets.yaml         # Tracked whale wallet addresses
docker/
  Dockerfile           # Multi-stage production build
  docker-compose.yml   # Full deployment with volumes and health checks
```

## Prerequisites

- Python 3.11+ (tested on 3.13)
- [uv](https://docs.astral.sh/uv/) package manager (recommended) or pip
- A funded Polygon wallet with USDC
- Polymarket API credentials (API key, secret, passphrase)
- Telegram bot token and chat ID (optional, for notifications)

## Quick Start

### 1. Clone and install

```bash
git clone <repo-url> polymarket-bot
cd polymarket-bot
uv sync          # Install all dependencies (including dev)
```

### 2. Configure environment

```bash
cp config/.env.example .env
```

Edit `.env` with your credentials:

| Variable | Required | Description |
|---|---|---|
| `POLYMARKET_API_KEY` | Yes | CLOB API key from Polymarket |
| `POLYMARKET_API_SECRET` | Yes | CLOB API secret |
| `POLYMARKET_API_PASSPHRASE` | Yes | CLOB API passphrase |
| `WALLET_PRIVATE_KEY` | Yes | Polygon wallet private key |
| `FUNDER_ADDRESS` | No | Browser wallet address (derived from key if empty) |
| `POLYGON_RPC_URL` | No | Defaults to `https://polygon-rpc.com` |
| `TELEGRAM_BOT_TOKEN` | No | For Telegram notifications |
| `TELEGRAM_CHAT_ID` | No | For Telegram notifications |
| `TRADING_MODE` | No | `paper` (default) or `live` |
| `LOG_LEVEL` | No | `DEBUG`, `INFO` (default), `WARNING`, `ERROR` |
| `DATABASE_URL` | No | SQLite path (default: `sqlite:///data/polybot.db`) |
| `HEALTH_PORT` | No | Health check HTTP port (default: `8080`) |

### 3. Configure strategies

Edit `config/strategies.yaml` to tune parameters:

- **Global limits**: max position size, max open positions, daily loss limit, cash reserve
- **Copy trader**: sizing method, min whale position, max slippage, poll interval
- **Arb scanner**: min parity gap threshold, scan interval
- **Stink bidder**: discount range, max active bids, min market volume

### 4. Add tracked wallets

Edit `config/wallets.yaml` with whale addresses from the [Polymarket leaderboard](https://polymarket.com/leaderboard):

```yaml
wallets:
  - address: "0xabc..."
    name: "Top Trader"
    notes: "High win rate on politics markets"
    enabled: true
    max_allocation_usd: 200.0
```

### 5. Run

```bash
# Paper trading (default, no real money)
polybot

# Live trading
polybot --live

# With debug logging
polybot --log-level DEBUG

# Check current status
polybot --status

# Emergency stop: cancel all orders
polybot --kill
```

## Docker Deployment

```bash
# Start in paper mode (default)
docker compose -f docker/docker-compose.yml up -d

# Start in live mode
TRADING_MODE=live docker compose -f docker/docker-compose.yml up -d

# View logs
docker logs -f polybot

# Stop gracefully (cancels open orders, saves state)
docker compose -f docker/docker-compose.yml down
```

The Docker setup includes:
- Multi-stage build with `uv` for fast installs
- Non-root user (`polybot`)
- Health check endpoint on port 8080
- Auto-restart on crash (`unless-stopped`)
- Persistent volumes for database and logs
- 512MB memory limit
- 30s graceful shutdown window for order cancellation

## Telegram Commands

| Command | Description |
|---|---|
| `/status` | Bot mode, portfolio value, health, strategy states |
| `/pnl` | Daily P&L summary with realized/unrealized breakdown |
| `/kill` | Emergency stop -- cancels all orders, halts trading |
| `/pause [strategy]` | Pause one or all strategies |
| `/resume [strategy]` | Resume one or all strategies |

## Development

```bash
# Install with dev dependencies
uv sync

# Run tests (175 tests)
.venv/bin/pytest

# Lint
.venv/bin/ruff check src/ tests/

# Type check
.venv/bin/mypy src/

# Auto-fix lint issues
.venv/bin/ruff check --fix src/ tests/
```

### Project tools

| Tool | Config | Purpose |
|---|---|---|
| pytest | `pyproject.toml` | Test suite (async mode via pytest-asyncio) |
| ruff | `pyproject.toml` | Linting + import sorting (E, F, W, I, N, UP) |
| mypy | `pyproject.toml` | Strict type checking (Python 3.11 target) |

## Risk Warnings

- **This bot trades real money in live mode.** Start with paper mode and small positions.
- Polymarket is a prediction market -- outcomes are binary and positions can go to zero.
- The bot does **not** guarantee profits. Past whale performance does not predict future results.
- Always set a daily loss limit and cash reserve in `strategies.yaml`.
- Use the kill switch (`polybot --kill` or `/kill` in Telegram) if anything looks wrong.

## License

Private -- not for redistribution.
