# Project Research Summary

**Project:** Polymarket Trading Bot
**Domain:** Automated Prediction Market Trading
**Researched:** 2026-02-13
**Confidence:** HIGH

## Executive Summary

Polymarket is a $9B blockchain-based prediction market on Polygon with a mature API ecosystem (CLOB, Gamma, Data APIs + WebSockets). Automated trading bots have earned an estimated $40M+ between 2024-2025, with copy trading and AI prediction showing the highest ROI for small-capital operators. The Python ecosystem (py-clob-client, web3.py, litellm) provides the strongest foundation.

The recommended approach is a **Strategy-Signal-Execution pipeline** where multiple strategies (copy trading, AI prediction, arbitrage, stink bids) produce signals independently, and a centralized execution layer handles order management, rate limiting, and risk controls. This architecture is proven by the official Polymarket/agents framework and community bots like poly-maker.

Key risks are: web3.py version conflicts (must pin 6.14.0), proxy wallet misconfiguration, fee structure compression (5%+ edge needed after all fees), and LLM overconfidence in AI predictions. All are preventable with proper architecture.

## Key Findings

### Recommended Stack

Python 3.11+ with the official `py-clob-client` SDK forms the core. The stack is purpose-built for async multi-strategy operation.

**Core technologies:**
- `py-clob-client` + `python-order-utils`: Official Polymarket SDKs for trading — no alternatives
- `web3.py==6.14.0`: Blockchain queries — MUST be pinned to this exact version
- `litellm`: Unified multi-LLM interface (Claude, Gemini, etc.) — avoids vendor lock-in
- `chromadb`: Vector DB for RAG pipeline — used by official Polymarket/agents
- `httpx` + `websockets`: Async HTTP and real-time data — critical for concurrent strategies
- `pydantic-settings`: Type-safe configuration — prevents misconfigurations that lose money

### Expected Features

**Must have (table stakes):**
- API authentication with proxy wallet support
- Order placement with rate limiting (60/min)
- Position tracking and P&L calculation
- Kill switch for emergency stop
- Structured logging for audit trail

**Should have (competitive):**
- Copy trading with whale wallet tracking (primary income)
- AI probability estimation with multi-model ensemble
- Full position management (TP/SL/trailing stops)
- Telegram bot for notifications and control
- Paper trading mode for risk-free validation

**Defer (v2+):**
- Web dashboard — Telegram provides mobile monitoring
- Market making — requires $5K+ capital
- Backtesting engine — paper trading validates first

### Architecture Approach

A 4-layer architecture: Data Layer (API clients, WebSocket, news) → Strategy Layer (independent strategy modules with common interface) → Execution Layer (order manager, position manager, risk manager) → Support Layer (database, notifications, logging). Strategies never place orders directly — they emit signals that the execution layer processes. This prevents conflicts, enforces risk limits, and makes adding/removing strategies trivial.

**Major components:**
1. **Core Client** — Unified Polymarket API wrapper (CLOB + Gamma + Data)
2. **Strategy Engine** — BaseStrategy abstraction with per-strategy tick loops
3. **Execution Pipeline** — Signal queue → Risk check → Rate limiter → Order submission
4. **Position Manager** — Event-driven TP/SL/trailing via WebSocket price feeds
5. **Telegram Bot** — Two-way control: alerts out, commands in

### Critical Pitfalls

1. **web3.py version hell** — Pin to 6.14.0 or nothing installs. Address in Phase 1.
2. **Proxy wallet confusion** — Signing key ≠ funded address. Must configure both correctly. Address in Phase 1.
3. **First trade requirement** — Must complete one manual trade before API works. Include in onboarding.
4. **Fee miscalculation** — 2% winner + 3.15% taker + gas = need 5%+ edge. Build into every signal evaluation.
5. **LLM overconfidence** — AI says "90% sure" but isn't. Require multi-model agreement + position size caps.

## Implications for Roadmap

### Phase 1: Foundation & Core Infrastructure
**Rationale:** Everything depends on this — can't trade without API client, wallet, orders
**Delivers:** Working API connection, order placement, risk controls, configuration, database
**Addresses:** AUTH, CONFIG, ORDER, RISK table stakes from FEATURES.md
**Avoids:** web3.py version hell, proxy wallet confusion, private key exposure, first trade requirement

### Phase 2: Copy Trading (Primary Income)
**Rationale:** Highest ROI strategy for <$1K capital; gets money flowing early
**Delivers:** Whale tracking, position replication, slippage protection, basic position management
**Addresses:** COPY trading features, position management (TP/SL)
**Avoids:** Copy trading latency, overtrading on small capital

### Phase 3: Telegram & Notifications
**Rationale:** Must know what the bot is doing; enables remote monitoring and control
**Delivers:** Trade alerts, P&L reports, bot commands (/status, /kill, /pause)
**Addresses:** NOTIFY features, kill switch remote access
**Avoids:** Running blind; delayed response to problems

### Phase 4: WebSocket & Real-Time Data
**Rationale:** Position management needs real-time prices; strategies need live data
**Delivers:** Persistent WebSocket connection, real-time TP/SL evaluation, live order book
**Addresses:** REALTIME data features, enhanced position management
**Avoids:** WebSocket disconnection pitfall (auto-reconnect with fallback)

### Phase 5: AI Prediction Engine
**Rationale:** Second income stream; requires news pipeline + RAG + LLM integration
**Delivers:** News ingestion, ChromaDB RAG, multi-model probability estimation, edge detection
**Addresses:** AI prediction features from FEATURES.md
**Avoids:** LLM overconfidence (multi-model agreement required)

### Phase 6: Arbitrage & Stink Bids
**Rationale:** Additional passive income; relatively simple once execution layer exists
**Delivers:** Parity arb scanner, stink bid placement and management
**Addresses:** ARB and STINK features from FEATURES.md
**Avoids:** Fee miscalculation (5%+ edge threshold built in)

### Phase 7: Paper Trading & Backtesting
**Rationale:** Validate strategies safely before risking more capital
**Delivers:** Simulated execution mode, historical data analysis, performance metrics
**Addresses:** PAPER and BACKTEST features from FEATURES.md
**Avoids:** Overtrading (paper trading reveals strategy weaknesses)

### Phase 8: Production Hardening & Docker Deployment
**Rationale:** Move from local dev to 24/7 VPS operation
**Delivers:** Dockerfile, docker-compose, health monitoring, auto-restart, production PostgreSQL
**Addresses:** DEPLOY features from FEATURES.md
**Avoids:** VPS reliability issues (health checks, auto-recovery)

### Phase Ordering Rationale

- Phases 1-2 are strictly sequential (can't trade without foundation; can't copy trade without orders)
- Phase 3 (Telegram) can start once Phase 2 produces trades to notify about
- Phase 4 (WebSocket) enhances Phase 2's position management significantly
- Phase 5 (AI) is independent of Phase 4 but benefits from the data infrastructure
- Phases 6-7 are relatively independent and could be reordered
- Phase 8 is always last — deploy what's working

### Research Flags

Phases likely needing deeper research during planning:
- **Phase 5:** AI prediction — LLM prompt engineering for probability estimation is nuanced; needs research on calibration techniques
- **Phase 7:** Backtesting — historical data availability from Polymarket is limited; may need creative approaches

Phases with standard patterns (skip research-phase):
- **Phase 1:** Foundation — well-documented APIs with official SDKs
- **Phase 3:** Telegram — standard bot implementation with python-telegram-bot
- **Phase 8:** Docker deployment — standard containerization

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | Official SDKs exist; version constraints well-documented |
| Features | HIGH | Multiple open-source implementations analyzed; clear feature landscape |
| Architecture | HIGH | Strategy-Signal-Execution pattern proven by Polymarket/agents and poly-maker |
| Pitfalls | HIGH | Validated by multiple community reports, GitHub issues, and NotebookLM sources |

**Overall confidence:** HIGH

### Gaps to Address

- **LLM prompt calibration:** Need to experiment with prompt engineering for accurate probability estimation during Phase 5 planning
- **Historical data access:** Polymarket doesn't provide a clean historical data API; may need to scrape or build data collection pipeline for backtesting
- **Dynamic taker fee calculation:** Exact algorithm for the 3.15% taker fee not fully documented; may need to reverse-engineer from on-chain data

## Sources

### Primary (HIGH confidence)
- Polymarket Developer Documentation (docs.polymarket.com) — API specs, authentication flow
- py-clob-client GitHub (Polymarket/py-clob-client v0.29.0) — SDK capabilities, version constraints
- Polymarket/agents GitHub — official AI trading framework architecture

### Secondary (MEDIUM confidence)
- NotebookLM research notebook — curated links from poly-maker, Alpha Stack, Moon Dev, ItsRagnar
- Web research (Feb 2026) — ecosystem analysis, profitability data, fee structure changes

### Tertiary (LOW confidence)
- Reddit/Twitter community reports — anecdotal pitfall reports (validated against official docs where possible)

---
*Research completed: 2026-02-13*
*Ready for roadmap: yes*
