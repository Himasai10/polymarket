# Stack Research

**Domain:** Polymarket Automated Trading Bot
**Researched:** 2026-02-13
**Confidence:** HIGH

## Recommended Stack

### Core Technologies

| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| Python | 3.11+ | Runtime | Best Polymarket SDK ecosystem; strongest AI/ML libraries; async support via asyncio |
| py-clob-client | latest (v0.29.0+) | CLOB API SDK | Official Polymarket Python SDK for order placement, book data, and trade execution |
| python-order-utils | latest | Order signing | Official utility for generating and signing orders for CTF Exchange |
| web3.py | 6.14.0 (PINNED) | Blockchain interaction | Query USDC balances, allowances, on-chain data on Polygon; MUST pin to avoid eth-typing conflicts |
| litellm | latest | Multi-LLM interface | Unified API for Claude, Gemini, OpenAI, and 100+ models without vendor lock-in |
| chromadb | latest | Vector database | AI-native vector DB for RAG pipeline; used by official Polymarket/agents framework |
| SQLite / PostgreSQL | 3.x / 16+ | Database | SQLite for zero-config local dev; PostgreSQL for production on VPS |

### Supporting Libraries

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| httpx | latest | Async HTTP client | All API calls (Gamma, CLOB, Data, News APIs); async-native, faster than requests |
| websockets | latest | WebSocket client | Real-time order book feeds from CLOB WebSocket and RTDS |
| pandas | latest | Data analysis | P&L calculations, backtesting data manipulation, trade history analysis |
| numpy | latest | Numerical computing | Statistical calculations for strategy optimization |
| pydantic | 2.x | Data modeling | Type-safe config, API response parsing, order validation |
| pydantic-settings | latest | Configuration | .env file loading with type validation |
| python-dotenv | latest | Environment vars | Secret management (API keys, private keys) |
| python-telegram-bot | 20.x+ | Telegram integration | Trade notifications, bot control commands, P&L alerts |
| structlog | latest | Structured logging | JSON logging for debugging, trade audit trail |
| aiosqlite | latest | Async SQLite | Non-blocking database operations in async event loop |
| asyncpg | latest | Async PostgreSQL | Production database driver (when on VPS) |
| tenacity | latest | Retry logic | Exponential backoff for rate-limited API calls |
| APScheduler | latest | Job scheduling | Periodic tasks: market scanning, position checking, P&L snapshots |

### Development Tools

| Tool | Purpose | Notes |
|------|---------|-------|
| uv | Package manager | 10-100x faster than pip; manages venvs; recommended over pip |
| pytest | Testing | Unit and integration tests |
| pytest-asyncio | Async testing | Testing async strategy code and API clients |
| ruff | Linter + formatter | Replaces flake8 + black + isort; extremely fast |
| mypy | Type checking | Catch type errors before runtime, especially important for financial code |
| Docker + docker-compose | Deployment | Consistent behavior across local dev and VPS |

## Installation

```bash
# Create project with uv
uv init polymarket-bot
cd polymarket-bot

# Core dependencies
uv add py-clob-client python-order-utils "web3==6.14.0" litellm chromadb

# HTTP and WebSocket
uv add httpx websockets

# Data and config
uv add pandas numpy pydantic pydantic-settings python-dotenv

# Notifications and scheduling
uv add "python-telegram-bot>=20.0" apscheduler

# Logging and resilience
uv add structlog tenacity

# Database
uv add aiosqlite asyncpg

# Dev dependencies
uv add --dev pytest pytest-asyncio ruff mypy
```

## Alternatives Considered

| Recommended | Alternative | When to Use Alternative |
|-------------|-------------|-------------------------|
| Python | TypeScript/Node.js | If building a web-first dashboard; @polymarket/clob-client exists but AI/ML ecosystem is weaker |
| litellm | Direct OpenAI/Anthropic SDKs | If only using one LLM provider; litellm adds flexibility at minimal overhead |
| httpx | requests | Never for this project; requests is synchronous and blocks the event loop |
| chromadb | Pinecone / Weaviate | If scaling to millions of documents; ChromaDB is simpler for our news corpus size |
| SQLite | MongoDB | If data is highly unstructured; our trade data is relational |
| uv | pip + venv | If uv unavailable; pip works but is slower |
| structlog | loguru | Personal preference; structlog produces better JSON for log analysis |
| APScheduler | Celery | If distributed task processing needed; overkill for single-bot operation |

## What NOT to Use

| Avoid | Why | Use Instead |
|-------|-----|-------------|
| web3.py > 6.14.0 | eth-typing version conflicts break installation | Pin web3==6.14.0 exactly |
| requests (sync) | Blocks the async event loop; kills concurrent strategy execution | httpx (async-native) |
| Flask/Django | We don't need a web framework for v1; adds unnecessary complexity | Direct API calls + Telegram |
| Celery | Overkill for single-process bot; adds Redis dependency unnecessarily | asyncio + APScheduler |
| langchain | Heavy abstraction layer; adds complexity without proportional value for our use case | Direct litellm + chromadb |
| ccxt | Crypto exchange library that doesn't support Polymarket's CLOB API | py-clob-client (official) |
| Brownie/Hardhat | Solidity development frameworks; we're not writing smart contracts | web3.py for read-only chain queries |

## Version Compatibility

| Package A | Compatible With | Notes |
|-----------|-----------------|-------|
| web3==6.14.0 | py-clob-client latest | MUST pin web3; newer versions have eth-typing conflicts |
| python-telegram-bot>=20.0 | asyncio | v20+ is async-native; v13.x was synchronous |
| pydantic>=2.0 | pydantic-settings | Pydantic v2 has breaking changes from v1; use v2 exclusively |
| chromadb | litellm | Both work with embedding functions; ChromaDB includes default embeddings |

## Sources

- Polymarket Developer Documentation (docs.polymarket.com)
- py-clob-client GitHub (Polymarket/py-clob-client) — v0.29.0 Dec 2025
- Polymarket/agents GitHub — official AI agent framework
- warproxxx/poly-maker GitHub — market making bot stack
- NotebookLM research notebook — curated implementation links
- Web research on current Polymarket bot ecosystem (Feb 2026)

---
*Stack research for: Polymarket Automated Trading Bot*
*Researched: 2026-02-13*
