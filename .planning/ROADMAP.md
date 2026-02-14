# Roadmap: Polymarket Trading Bot

**Created:** 2026-02-13
**Core Value:** The bot must consistently make profitable trades with real money on Polymarket.
**Total v1 Requirements:** 43

## Phase Overview

| Phase | Name | Requirements | Goal | Est. Effort |
|-------|------|-------------|------|-------------|
| 1 | Foundation & Core Infrastructure | 15 | Working API connection, orders, risk controls, config, database, logging | Large |
| 2 | Copy Trading Strategy | 8 | Bot can copy trade profitable wallets and manage positions | Medium |
| 3 | Telegram & Notifications | 8 | Remote monitoring and control of the bot | Medium |
| 4 | Arbitrage & Stink Bids | 6 | Passive income strategies running alongside copy trading | Small |
| 5 | Deployment & Production | 6 | 24/7 operation on VPS with Docker | Small |

**Total:** 43 requirements across 5 phases

---

## Phase 1: Foundation & Core Infrastructure

**Goal:** Bot can authenticate, discover markets, place/cancel orders, enforce risk limits, persist state, and log everything. No trading strategies yet — just a solid, safe foundation.

**Why first:** Every strategy depends on this layer. Getting configuration, wallet, API client, rate limiting, risk management, and database right prevents catastrophic bugs when real money is involved.

### Requirements

| ID | Requirement | Success Criteria |
|----|-------------|-----------------|
| CORE-01 | Config from .env + strategies.yaml | Bot loads all settings on startup; missing required fields cause clear error messages |
| CORE-02 | CLOB API authentication with proxy wallet | `client.get_api_keys()` returns valid keys; test order succeeds |
| CORE-03 | USDC balance check via web3.py==6.14.0 | `wallet.get_balance()` returns correct USDC balance on Polygon |
| CORE-04 | Market discovery via Gamma API | Bot can list active markets filtered by category, volume, liquidity |
| CORE-05 | Place limit orders (GTC, FOK, IOC) | GTC order appears in open orders; FOK fills or cancels; IOC partial fill works |
| CORE-06 | Cancel orders | Individual cancel and cancel-all both confirmed via API |
| CORE-07 | Rate limiting (60 orders/min) | Under load, bot never exceeds 60 orders/min; exponential backoff on 429s |
| CORE-08 | Persist to SQLite | Trade history, positions, strategy state all queryable after restart |
| CORE-09 | Structured JSON logging | Every trade logged with: strategy, market, reasoning, price, size, fees |
| CORE-10 | WebSocket for real-time data | Connected to CLOB WS; receives order book updates; auto-reconnects after 60s drop |
| CORE-11 | Guided onboarding wizard | Interactive CLI walks through: account creation, wallet setup, first manual trade, API keys, .env config, verification |
| RISK-01 | Max position size (default 15%) | Order rejected if it would exceed max position % of portfolio |
| RISK-02 | Max open positions (default 10) | New trade rejected when at max open positions |
| RISK-06 | Minimum edge threshold (5%+) | Signal rejected if expected edge < 5% after all fees |
| RISK-07 | Cash reserve (10% minimum) | Order rejected if it would drop cash below 10% reserve |

### Pitfalls Addressed
- web3.py pinned to 6.14.0 (Pitfall 1)
- Proxy wallet correctly configured with funder (Pitfall 2)
- First trade requirement in onboarding (Pitfall 3)
- Fee calculation in every signal evaluation (Pitfall 4)
- Private key as SecretStr, .gitignore from day 1 (Pitfall 10)
- Data freshness checks on all market data (Pitfall 9)
- Position limits prevent overtrading (Pitfall 8)

### Exit Criteria
- [ ] Bot starts, loads config, authenticates with CLOB API
- [ ] Bot queries balance and discovers markets
- [ ] Bot places and cancels a GTC order on a real market (tiny amount)
- [ ] Risk manager rejects orders that violate limits
- [ ] All trades logged to SQLite and structured JSON logs
- [ ] WebSocket connects and receives price updates
- [ ] Onboarding wizard completes end-to-end
- [ ] No private keys in logs or git history

---

## Phase 2: Copy Trading Strategy + Position Management

**Goal:** Bot can track whale wallets, detect new positions, replicate trades with proper sizing, and manage positions with take-profit/stop-loss rules. This is the primary income strategy.

**Why second:** Copy trading is the highest ROI strategy for <$1K capital. Position management protects the capital being deployed. Together, these make the bot profitable.

### Requirements

| ID | Requirement | Success Criteria |
|----|-------------|-----------------|
| COPY-01 | Track target wallets from wallets.yaml | Bot polls Data API for configured wallet addresses; detects position changes |
| COPY-02 | Detect new whale positions | Bot identifies when tracked wallet enters a new market; generates copy signal |
| COPY-03 | Configurable copy trade sizing | Supports: fixed $, % of portfolio, % of whale size; each configurable per wallet |
| COPY-04 | Conviction filter | Trade skipped if whale's position < configurable threshold (default $500) |
| COPY-05 | Slippage protection | Trade skipped if current price >5% worse than whale's entry price |
| COPY-06 | Per-wallet performance tracking | Win rate and P&L tracked per tracked wallet; visible in logs/status |
| POS-01 | Real-time position tracking with unrealized P&L | All open positions show current value using WebSocket prices |
| POS-02 | Take-profit rules | Sells 50% at +50% gain, remaining at +100% gain (configurable) |
| POS-03 | Stop-loss rules | Exits position at -25% loss (configurable) |
| POS-04 | Trailing stop | After reaching TP threshold, trail by 10% (configurable) |
| POS-05 | Market resolution handling | Resolved markets auto-close positions, update P&L, log outcome |
| RISK-03 | Per-strategy capital allocation | Copy trading capped at 40% of portfolio; enforced at signal processing |
| RISK-04 | Daily loss limit (-10%) | All new trades halted if daily P&L drops below -10% of portfolio |
| RISK-05 | Kill switch | CLI command and future Telegram /kill instantly cancel all orders, optionally exit positions |

### Pitfalls Addressed
- Copy trading slippage protection (Pitfall 5)
- WebSocket disconnection recovery with REST fallback (Pitfall 7)
- Overtrading prevention via allocation limits (Pitfall 8)

### Exit Criteria
- [ ] Bot detects new positions from at least 2 tracked wallets
- [ ] Copy trades placed with correct sizing and conviction filter
- [ ] Slippage protection skips trades when price moved >5%
- [ ] Take-profit triggers sell at configured gain thresholds
- [ ] Stop-loss exits position at configured loss threshold
- [ ] Trailing stop activates after TP threshold reached
- [ ] Market resolution auto-closes positions
- [ ] Kill switch cancels all open orders within 5 seconds
- [ ] Daily loss limit halts trading when triggered
- [ ] Per-wallet P&L tracking shows accurate win rates

---

## Phase 3: Telegram Integration & Notifications

**Goal:** Full remote monitoring and control of the bot via Telegram. Know what the bot is doing, get P&L summaries, and control it from your phone.

**Why third:** Once the bot is actively trading (Phase 2), you need visibility and control. Running blind is dangerous with real money.

### Requirements

| ID | Requirement | Success Criteria |
|----|-------------|-----------------|
| TG-01 | Alert on new position | Telegram message with: strategy, market, side, size, price, reasoning |
| TG-02 | Alert on position close | Telegram message with: P&L, hold duration, exit reason |
| TG-03 | Daily P&L summary | Automatic daily message with per-strategy P&L breakdown |
| TG-04 | /status command | Returns: portfolio value, open positions, active strategies |
| TG-05 | /pnl command | Returns: today's P&L with per-strategy breakdown |
| TG-06 | /kill command | Executes kill switch; confirms all orders cancelled |
| TG-07 | /pause and /resume commands | Pause/resume individual strategies or all strategies |
| TG-08 | System alerts | Connection issues, risk limit warnings, errors sent to Telegram |

### Exit Criteria
- [ ] Trade entry/exit alerts arrive within 5 seconds of execution
- [ ] Daily P&L summary sent at configured time
- [ ] All 5 commands (/status, /pnl, /kill, /pause, /resume) work correctly
- [ ] System alerts fire on API disconnection and risk limit warnings
- [ ] Bot doesn't crash on Telegram API errors (graceful degradation)

---

## Phase 4: Arbitrage & Stink Bids

**Goal:** Additional passive income strategies running alongside copy trading. Arbitrage captures risk-free profit; stink bids catch fat-finger trades.

**Why fourth:** These are simpler strategies that leverage the execution layer built in Phases 1-2. Additional income streams with minimal additional complexity.

### Requirements

| ID | Requirement | Success Criteria |
|----|-------------|-----------------|
| ARB-01 | Scan all markets for parity arbitrage | Continuous scan; detects when Yes + No < $0.95 (after fees) |
| ARB-02 | Execute simultaneous FOK orders | Both Yes and No FOK orders submitted within 1 second of detection |
| ARB-03 | Log all opportunities | Every detected opportunity logged (including ones too small to execute) |
| STINK-01 | Place stink bids 70-90% below market | GTC orders placed on selected high-volume markets |
| STINK-02 | Auto-refresh expired orders | Cancelled/expired stink bids auto-replaced |
| STINK-03 | Capital allocation limit (20%) | Total stink bid exposure never exceeds 20% of portfolio |

### Exit Criteria
- [ ] Arb scanner runs continuously without exceeding rate limits
- [ ] Arb opportunities detected and logged (may be rare — frequency logged)
- [ ] Stink bids placed on at least 5 high-volume markets
- [ ] Expired stink bids auto-refreshed within 5 minutes
- [ ] Total stink bid + arb allocation respects configured limits
- [ ] Telegram alerts on arb detection and stink bid fills

---

## Phase 5: Deployment & Production Hardening

**Goal:** Bot runs 24/7 on a VPS with Docker, auto-restarts on crashes, health monitoring, and graceful shutdown.

**Why last:** Deploy what's working. All strategies are validated locally before moving to always-on production.

### Requirements

| ID | Requirement | Success Criteria |
|----|-------------|-----------------|
| DEPLOY-01 | Docker container with docker-compose | `docker-compose up` starts the bot with all dependencies |
| DEPLOY-02 | Auto-restart on crash | Docker restart policy brings bot back within 30 seconds |
| DEPLOY-03 | Health checks | API connectivity, WebSocket status, database connection all monitored |
| DEPLOY-04 | Graceful shutdown | SIGTERM cancels open orders, saves state, closes connections cleanly |
| CORE-09 | Production logging | Logs rotated, structured, and queryable on VPS |
| CORE-08 | Database persistence across restarts | SQLite file persisted via Docker volume; no data loss on restart |

### Exit Criteria
- [ ] `docker-compose up` starts bot from clean state
- [ ] Bot recovers from simulated crash within 30 seconds
- [ ] Health check endpoint reports all systems status
- [ ] SIGTERM triggers clean shutdown (no orphaned orders)
- [ ] Bot runs for 24+ hours on VPS without manual intervention
- [ ] Logs and database survive container restart

---

## Phase Dependencies

```
Phase 1: Foundation ──────────────┐
                                  ├──> Phase 2: Copy Trading + Position Mgmt
                                  │         │
                                  │         ├──> Phase 3: Telegram
                                  │         │
                                  │         ├──> Phase 4: Arb + Stink Bids
                                  │         │
                                  │         └──> Phase 5: Deployment
                                  │
                                  └──> (Phases 3, 4, 5 can run in parallel after Phase 2)
```

**Strict dependencies:**
- Phase 2 requires Phase 1 (can't trade without infrastructure)
- Phase 3 requires Phase 2 (nothing to notify about without trades)

**Flexible ordering:**
- Phases 3, 4, 5 can be developed in parallel after Phase 2
- Phase 5 should be last to deploy validated strategies

---

## Requirement Coverage

All 43 v1 requirements mapped:

| Phase | Count | IDs |
|-------|-------|-----|
| Phase 1 | 15 | CORE-01 through CORE-11, RISK-01, RISK-02, RISK-06, RISK-07 |
| Phase 2 | 14 | COPY-01 through COPY-06, POS-01 through POS-05, RISK-03, RISK-04, RISK-05 |
| Phase 3 | 8 | TG-01 through TG-08 |
| Phase 4 | 6 | ARB-01 through ARB-03, STINK-01 through STINK-03 |
| Phase 5 | 6 | DEPLOY-01 through DEPLOY-04, CORE-08 (production), CORE-09 (production) |
| **Total** | **43** | **Note: CORE-08, CORE-09 appear in Phase 1 (dev) and Phase 5 (production hardening)** |

**Unmapped:** 0

---

## Risk Register

| Risk | Impact | Probability | Mitigation | Phase |
|------|--------|-------------|------------|-------|
| web3.py version conflicts | Bot won't install | HIGH (if unpinned) | Pin web3==6.14.0; test in CI | 1 |
| Proxy wallet misconfiguration | Orders fail silently | MEDIUM | Onboarding wizard verifies; test trade required | 1 |
| Copy trading enters at worse prices | Reduced profit | HIGH | Slippage protection; max 5% worse than whale entry | 2 |
| WebSocket drops during volatility | Missed TP/SL triggers | MEDIUM | Auto-reconnect + REST API fallback | 1-2 |
| Rate limit exceeded | Orders rejected | LOW (if throttled) | Token bucket limiter; exponential backoff | 1 |
| Fee structure changes | Edge calculations wrong | LOW | Configurable fee parameters; monitor Polymarket announcements | 1 |
| Whale wallet changes address | Lost tracking | MEDIUM | Multiple wallets tracked; periodic leaderboard refresh | 2 |

---
*Roadmap created: 2026-02-13*
*Last updated: 2026-02-13*
