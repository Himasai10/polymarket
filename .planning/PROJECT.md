# Polymarket Trading Bot

## What This Is

A profit-first automated trading bot for Polymarket that combines copy trading, AI-powered prediction, parity arbitrage, and stink bids to generate consistent returns starting from under $1K. The bot runs 24/7, makes trades autonomously, manages positions with stop-loss/take-profit, and sends Telegram notifications on every action.

## Core Value

The bot must consistently make profitable trades with real money on Polymarket — tangible P&L, not theoretical backtests. Every architectural decision optimizes for actual returns over feature completeness.

## Requirements

### Validated

(None yet — ship to validate)

### Active

- [ ] Copy trade profitable wallets from Polymarket leaderboard with configurable sizing
- [ ] AI-powered probability estimation using multi-LLM ensemble (Claude, Gemini) to find mispriced markets
- [ ] Parity arbitrage scanner detecting Yes+No < $0.97 opportunities across all markets
- [ ] Stink bid placement at 70-90% below market price to catch fat-finger trades
- [ ] Full position management: take-profit, stop-loss, trailing stops
- [ ] Risk controls: kill switch, daily loss limits, per-strategy capital allocation, slippage protection
- [ ] Paper trading mode with simulated execution against live data
- [ ] Backtesting engine against historical market data
- [ ] Telegram notifications for trades, P&L summaries, and bot control commands
- [ ] Guided onboarding: account creation, wallet setup, API key generation
- [ ] 24/7 operation via Docker deployment to VPS
- [ ] News ingestion and RAG pipeline for AI prediction context
- [ ] WebSocket integration for real-time order book and price feeds
- [ ] Per-strategy performance tracking and P&L attribution

### Out of Scope

- Market making strategy — requires $5K+ capital to be viable, deferred until portfolio grows
- Web dashboard — nice to have but not v1, CLI monitoring is sufficient to start
- Mobile app — unnecessary, Telegram bot provides mobile interface
- Multi-exchange support — Polymarket only, no Kalshi/PredictIt trading
- Social trading / sharing — this is a personal profit tool, not a platform
- High-frequency trading — rate limits (60 orders/min) make true HFT impossible on Polymarket

## Context

**Polymarket Ecosystem:**
- Blockchain-based prediction market on Polygon (L2), transactions in USDC
- CLOB (Central Limit Order Book) API for order placement, Gamma API for market discovery
- 2% winner fee + up to 3.15% dynamic taker fee — minimum profitable edge is ~5%+
- WebSocket feeds for real-time order book and price updates
- Proxy wallet architecture: signing key differs from funded address
- Must complete 1 manual trade through Polymarket UI before API trading works (initializes permissions)

**Competitive Landscape:**
- Bot-vs-bot competition is intensifying; arbitrage margins shrinking
- Sophisticated bots achieve 85%+ win rates, ~$206K average profits
- Copy trading remains high-ROI because it piggybacks on whale due diligence
- AI prediction is the emerging edge — LLMs can process news faster than manual traders
- Dynamic taker fee (introduced May 2025) reduced bot volume from ~25% to ~5%

**Capital Constraints (<$1K):**
- Copy trading: 40% allocation (~$400) — highest probability of consistent returns
- AI prediction: 30% (~$300) — highest upside per trade
- Stink bids: 20% (~$200) — low effort, occasional big wins
- Arbitrage: 10% (~$100) — reserved for guaranteed-profit opportunities
- 10% cash reserve maintained at all times

**Research Sources:**
- NotebookLM notebook with curated links on existing Polymarket bot implementations
- Official Polymarket developer docs (CLOB, Gamma, Data APIs)
- Open-source repos: Polymarket/agents, poly-maker, polybot, various copy trading bots
- Web research on strategies, profitability data, and ecosystem analysis

## Constraints

- **Capital**: Under $1K starting capital — strategies must be viable at small scale
- **Fees**: 2% winner fee + up to 3.15% taker fee — only trade when edge exceeds 5%
- **Rate Limits**: 60 orders/minute per API key — built-in throttling required
- **Starting Fresh**: No existing Polymarket account — onboarding flow needed
- **Tech Stack**: Python 3.11+ with py-clob-client (official SDK) — best ecosystem support
- **LLM Costs**: Must be efficient with AI API calls — cache results, batch evaluations
- **Regulatory**: User responsible for ensuring Polymarket is legal in their jurisdiction
- **Web3 Dependency**: Pin web3==6.14.0 to avoid eth-typing conflicts

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Python over TypeScript | Best Polymarket SDK ecosystem (py-clob-client), strongest AI/ML libraries | — Pending |
| Copy trading as primary strategy | Highest ROI for <$1K capital, proven whale track records on-chain | — Pending |
| LiteLLM for multi-model AI | Unified interface to Claude, Gemini, and 100+ models without vendor lock-in | — Pending |
| SQLite for dev, PostgreSQL for prod | Zero-config locally, scalable in production | — Pending |
| Paper trading before live | 48h minimum in paper mode validates strategies without risking capital | — Pending |
| Telegram for notifications | Mobile access to bot status and control without building a dashboard | — Pending |
| Docker for deployment | Consistent behavior across local dev and any $5-10/mo VPS | — Pending |
| ChromaDB for RAG | AI-native vector DB, used by official Polymarket/agents framework | — Pending |

---
*Last updated: 2026-02-13 after initialization*
