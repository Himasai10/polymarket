# Requirements: Polymarket Trading Bot

**Defined:** 2026-02-13
**Core Value:** The bot must consistently make profitable trades with real money on Polymarket — tangible P&L, not theoretical backtests.

## v1 Requirements

Requirements for initial release. Each maps to roadmap phases.

### Core Infrastructure

- [ ] **CORE-01**: Bot loads configuration from .env file (API keys, private key, Telegram token) and strategies.yaml (strategy params, wallet lists, thresholds)
- [ ] **CORE-02**: Bot authenticates with Polymarket CLOB API using API Key, Secret, Passphrase, and wallet private key with correct funder/proxy wallet configuration
- [ ] **CORE-03**: Bot queries USDC balance on Polygon via web3.py (pinned to 6.14.0) before placing any trade
- [ ] **CORE-04**: Bot discovers active markets via Gamma API with filtering by category, volume, and liquidity
- [ ] **CORE-05**: Bot places limit orders (GTC, FOK, IOC) via CLOB API with proper order signing
- [ ] **CORE-06**: Bot cancels individual or all open orders via CLOB API
- [ ] **CORE-07**: Bot enforces rate limiting at 60 orders/minute with exponential backoff on rate limit errors
- [ ] **CORE-08**: Bot persists trade history, positions, and strategy state to SQLite database
- [ ] **CORE-09**: Bot produces structured JSON logs (structlog) with full trade context: strategy, market, reasoning, price, size, fees
- [ ] **CORE-10**: Bot connects to CLOB WebSocket for real-time order book updates and price feeds with auto-reconnect on disconnect
- [ ] **CORE-11**: Bot includes guided onboarding wizard that walks user through: account creation, wallet funding, first manual trade, API key generation, .env configuration, and connection verification

### Copy Trading Strategy

- [ ] **COPY-01**: Bot tracks one or more target wallet addresses (configurable in wallets.yaml) by polling the Data API for position changes
- [ ] **COPY-02**: Bot detects when a target wallet enters a new position and generates a copy trade signal
- [ ] **COPY-03**: Bot sizes copy trades based on configurable method: fixed dollar amount OR percentage of portfolio OR percentage of whale's trade size
- [ ] **COPY-04**: Bot applies conviction filter: only copies trades where whale's position size exceeds a configurable threshold (e.g., $500+)
- [ ] **COPY-05**: Bot applies slippage protection: skips copy trade if current market price is >X% worse than whale's entry price (configurable, default 5%)
- [ ] **COPY-06**: Bot tracks per-wallet copy performance (win rate, P&L) to identify which wallets to keep following

### Arbitrage Strategy

- [ ] **ARB-01**: Bot continuously scans all active binary markets for parity arbitrage: Yes price + No price < $0.95 (accounting for 2% winner fee + gas)
- [ ] **ARB-02**: Bot executes simultaneous FOK orders on both Yes and No sides when arbitrage opportunity is detected
- [ ] **ARB-03**: Bot logs all detected opportunities (including ones too small to execute) for strategy analysis

### Stink Bid Strategy

- [ ] **STINK-01**: Bot places GTC limit orders at 70-90% below current market price on selected high-volume markets
- [ ] **STINK-02**: Bot auto-refreshes expired or cancelled stink bid orders
- [ ] **STINK-03**: Bot limits total capital allocated to stink bids (configurable, default 20% of portfolio)

### Position Management

- [ ] **POS-01**: Bot tracks all open positions with real-time unrealized P&L using WebSocket price feeds
- [ ] **POS-02**: Bot executes configurable take-profit rules: sell X% of position when Y% gain reached (default: sell 50% at +50%, sell remaining at +100%)
- [ ] **POS-03**: Bot executes configurable stop-loss rules: exit entire position when loss exceeds Z% (default: -25%)
- [ ] **POS-04**: Bot executes trailing stop: after reaching take-profit threshold, trail by configurable percentage (default: 10%)
- [ ] **POS-05**: Bot handles market resolution automatically: mark positions as settled, update P&L, log outcome

### Risk Management

- [ ] **RISK-01**: Bot enforces maximum position size: no single position exceeds X% of portfolio (default: 15%)
- [ ] **RISK-02**: Bot enforces maximum open positions: no more than N simultaneous positions (default: 10)
- [ ] **RISK-03**: Bot enforces per-strategy capital allocation limits (copy: 40%, arb: 10%, stink: 20%, reserve: 10%)
- [ ] **RISK-04**: Bot enforces daily loss limit: halt all new trades if daily P&L drops below -X% of portfolio (default: -10%)
- [ ] **RISK-05**: Bot provides kill switch that instantly cancels all open orders and optionally exits all positions, accessible via CLI command and Telegram /kill
- [ ] **RISK-06**: Bot enforces minimum edge threshold: only trade when expected edge exceeds 5% (accounting for all fees)
- [ ] **RISK-07**: Bot maintains minimum cash reserve at all times (default: 10% of portfolio)

### Telegram Integration

- [ ] **TG-01**: Bot sends Telegram alert when a new position is entered: strategy, market, side, size, price, reasoning
- [ ] **TG-02**: Bot sends Telegram alert when a position is closed: P&L, hold duration, exit reason (TP/SL/manual/resolution)
- [ ] **TG-03**: Bot sends daily P&L summary with per-strategy breakdown
- [ ] **TG-04**: Bot responds to /status command: current portfolio value, open positions, active strategies
- [ ] **TG-05**: Bot responds to /pnl command: today's P&L with per-strategy breakdown
- [ ] **TG-06**: Bot responds to /kill command: execute kill switch (cancel all orders)
- [ ] **TG-07**: Bot responds to /pause and /resume commands: pause/resume individual strategies or all
- [ ] **TG-08**: Bot sends system alerts: connection issues, risk limit warnings, errors

### Deployment

- [ ] **DEPLOY-01**: Bot runs via Docker container with docker-compose for easy VPS deployment
- [ ] **DEPLOY-02**: Bot auto-restarts on crash (Docker restart policy)
- [ ] **DEPLOY-03**: Bot performs health checks: API connectivity, WebSocket status, database connection
- [ ] **DEPLOY-04**: Bot gracefully handles shutdown: cancel open orders, save state, close connections

## v2 Requirements

Deferred to future release. Tracked but not in current roadmap.

### AI Prediction Engine

- **AI-01**: Bot ingests breaking news from NewsAPI/GDELT/RSS feeds and stores in ChromaDB for RAG
- **AI-02**: Bot uses LLM (via LiteLLM) to estimate "true" probability of events and compare to market prices
- **AI-03**: Bot requires multi-model agreement (Claude + Gemini) before placing AI-driven trades
- **AI-04**: Bot sizes AI trades based on estimated edge magnitude (not raw LLM confidence)
- **AI-05**: Bot tracks AI prediction accuracy over time and adjusts trust weights

### Paper Trading & Backtesting

- **PAPER-01**: Bot can run in paper trading mode: live data, simulated orders, [PAPER] prefix on Telegram
- **PAPER-02**: Bot simulates realistic slippage and fees in paper mode
- **PAPER-03**: Bot can backtest strategies against historical market data
- **PAPER-04**: Bot generates backtesting reports: total return, Sharpe ratio, max drawdown, win rate

### Market Making (Capital > $5K)

- **MM-01**: Bot maintains two-sided quotes (bid + ask) with configurable spread
- **MM-02**: Bot manages inventory to prevent excessive directional exposure
- **MM-03**: Bot integrates with Polymarket Liquidity Rewards program

### Dashboard

- **DASH-01**: Web dashboard shows real-time P&L chart
- **DASH-02**: Web dashboard shows open positions table with live prices
- **DASH-03**: Web dashboard provides one-click kill switch

## Out of Scope

| Feature | Reason |
|---------|--------|
| High-frequency trading | Polymarket rate limit is 60 orders/min; dynamic taker fee penalizes rapid trading |
| Multi-exchange support | Massive complexity; master Polymarket first |
| Automated fund deposits | Security nightmare; automated private key handling for fund transfers |
| Social/copy following platform | This is a personal profit tool, not a SaaS product |
| Mobile app | Telegram provides mobile interface; no native app needed |
| OAuth / multi-user auth | Single-user bot; no need for user management |
| Unlimited concurrent strategies | Spreads capital too thin; capped at 4-5 strategies |
| On-chain stop-losses | CTF Exchange doesn't support conditional orders; must be bot-level |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| CORE-01 | TBD | Pending |
| CORE-02 | TBD | Pending |
| CORE-03 | TBD | Pending |
| CORE-04 | TBD | Pending |
| CORE-05 | TBD | Pending |
| CORE-06 | TBD | Pending |
| CORE-07 | TBD | Pending |
| CORE-08 | TBD | Pending |
| CORE-09 | TBD | Pending |
| CORE-10 | TBD | Pending |
| CORE-11 | TBD | Pending |
| COPY-01 | TBD | Pending |
| COPY-02 | TBD | Pending |
| COPY-03 | TBD | Pending |
| COPY-04 | TBD | Pending |
| COPY-05 | TBD | Pending |
| COPY-06 | TBD | Pending |
| ARB-01 | TBD | Pending |
| ARB-02 | TBD | Pending |
| ARB-03 | TBD | Pending |
| STINK-01 | TBD | Pending |
| STINK-02 | TBD | Pending |
| STINK-03 | TBD | Pending |
| POS-01 | TBD | Pending |
| POS-02 | TBD | Pending |
| POS-03 | TBD | Pending |
| POS-04 | TBD | Pending |
| POS-05 | TBD | Pending |
| RISK-01 | TBD | Pending |
| RISK-02 | TBD | Pending |
| RISK-03 | TBD | Pending |
| RISK-04 | TBD | Pending |
| RISK-05 | TBD | Pending |
| RISK-06 | TBD | Pending |
| RISK-07 | TBD | Pending |
| TG-01 | TBD | Pending |
| TG-02 | TBD | Pending |
| TG-03 | TBD | Pending |
| TG-04 | TBD | Pending |
| TG-05 | TBD | Pending |
| TG-06 | TBD | Pending |
| TG-07 | TBD | Pending |
| TG-08 | TBD | Pending |
| DEPLOY-01 | TBD | Pending |
| DEPLOY-02 | TBD | Pending |
| DEPLOY-03 | TBD | Pending |
| DEPLOY-04 | TBD | Pending |

**Coverage:**
- v1 requirements: 43 total
- Mapped to phases: 0 (awaiting roadmap creation)
- Unmapped: 43

---
*Requirements defined: 2026-02-13*
*Last updated: 2026-02-13 after initial definition*
