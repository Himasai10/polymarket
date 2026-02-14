# Polymarket Trading Bot - Product Requirements Document (PRD)

## 1. Executive Summary

A profit-first automated trading bot for Polymarket that combines multiple battle-tested strategies to generate consistent returns starting from under $1K in capital. The bot prioritizes **tangible, consistent profits** over feature completeness, using a phased approach that starts with the highest-ROI strategies and scales as capital grows.

**Core Philosophy:** "Make money first, add features second."

---

## 2. User Profile

| Attribute | Detail |
|-----------|--------|
| **Starting Capital** | Under $1,000 USDC |
| **Polymarket Experience** | Starting fresh (no account, no API keys) |
| **Technical Level** | Agentic coding tools (OpenCode, Antigravity, Claude Opus 4.6) |
| **LLM Access** | Claude Opus 4.6, Gemini, Kimi K2.5, and others |
| **Deployment** | Local development -> Cloud VPS for 24/7 operation |
| **Risk Tolerance** | Profit-maximizing (balanced-aggressive given small capital) |
| **Notification Channel** | Telegram |

---

## 3. Strategy Stack (Priority-Ordered by ROI for <$1K Capital)

### Strategy 1: Copy Trading (PRIMARY - Highest Expected ROI)
**Why it's #1:** Proven profitable wallets exist on-chain with 60-85%+ win rates. With <$1K, piggybacking on whales' deep research is the highest-leverage play.

- Track top-performing wallets from Polymarket leaderboard
- Replicate trades with configurable position sizing (% of portfolio or fixed $ amount)
- Smart filtering: only copy when whale's position size exceeds threshold (conviction filter)
- Configurable delay/speed settings to avoid front-running detection
- Support tracking multiple wallets simultaneously with per-wallet allocation limits
- Use Polymarket Data API to poll target wallet positions and detect new trades
- Use CLOB API to execute copy trades with appropriate order types (GTC for larger positions, FOK for time-sensitive)

### Strategy 2: AI Prediction Engine (SECONDARY - Alpha Generator)
**Why it's #2:** LLMs can evaluate event probabilities from news/data faster than manual analysis. When the bot finds a market where its estimated probability diverges significantly from the market price, it bets.

- Multi-LLM ensemble approach (Claude + Gemini for diverse reasoning)
- News ingestion pipeline: scan news APIs, RSS feeds, and social media for events relevant to open markets
- Probability estimation: LLM evaluates "true" probability of event based on available evidence
- Edge detection: only trade when estimated probability diverges from market price by configurable threshold (e.g., >10% edge)
- Confidence scoring: LLM provides confidence level; position size scales with confidence
- RAG pipeline using ChromaDB to store and query relevant context (news articles, historical outcomes)
- Market category scanning across all Polymarket categories (politics, crypto, sports, pop culture, economics)

### Strategy 3: Parity Arbitrage Scanner (BONUS - Guaranteed Profit When Available)
**Why it's #3:** Zero-risk profit when Yes + No < $1.00 after fees. Opportunities are rare but free money.

- Continuously scan all active markets for Yes + No combined price < $0.97 (accounting for 2% winner fee + gas)
- Execute simultaneous FOK orders on both sides when opportunity detected
- Alert via Telegram even if auto-execution is disabled
- Track historical frequency and size of arb opportunities for strategy refinement

### Strategy 4: Stink Bids (PASSIVE - Set and Forget)
**Why it's #4:** Low-effort passive strategy. Place extreme limit orders to catch fat-finger trades and flash crashes.

- Scan markets for appropriate stink bid opportunities
- Place limit orders at 70-90% below current market price on selected markets
- Configurable bid levels and maximum capital allocation to stink bids
- Auto-refresh expired/cancelled orders
- Monitor and alert on fills

### Strategy 5: Market Making (FUTURE - When Capital > $5K)
**Why it's deferred:** Requires significant capital to maintain both sides of the order book. Not viable under $5K. Included in architecture for future activation.

- Maintain two-sided quotes (bid + ask) with configurable spread
- Inventory management to prevent excessive directional exposure
- Integration with Polymarket Liquidity Rewards program
- Dynamic spread adjustment based on volatility
- Activated when portfolio value exceeds configurable threshold (default: $5,000)

---

## 4. Core Architecture

### 4.1 Tech Stack

| Layer | Technology | Rationale |
|-------|-----------|-----------|
| **Language** | Python 3.11+ | Best Polymarket SDK ecosystem (py-clob-client, python-order-utils) |
| **Polymarket SDK** | `py-clob-client` (latest) | Official Python CLOB client |
| **Order Utilities** | `python-order-utils` | Order generation and signing |
| **Blockchain** | `web3.py` (pin `web3==6.14.0`) | USDC balance checks, on-chain queries (pin to avoid eth-typing conflicts) |
| **Real-Time Data** | `websockets` | WebSocket connections for live order book and price feeds |
| **HTTP Client** | `httpx` | Async HTTP for API calls (faster than requests) |
| **AI/LLM** | `litellm` | Unified interface to Claude, Gemini, OpenAI, and 100+ models |
| **Vector DB** | `chromadb` | RAG pipeline for news context storage and retrieval |
| **Data** | `pandas`, `numpy` | Analytics, backtesting, P&L calculations |
| **Database** | `SQLite` (dev) / `PostgreSQL` (prod) | Trade history, market data, strategy state |
| **Task Queue** | `asyncio` | Async event loop for concurrent strategy execution |
| **Config** | `pydantic-settings` | Type-safe configuration with .env support |
| **Notifications** | `python-telegram-bot` | Telegram alerts and commands |
| **Logging** | `structlog` | Structured JSON logging for debugging and monitoring |
| **Testing** | `pytest`, `pytest-asyncio` | Unit and integration tests |
| **Deployment** | `Docker` + `docker-compose` | Consistent deployment to any VPS |

### 4.2 System Architecture

```
+------------------------------------------------------------------+
|                        POLYMARKET BOT                              |
|                                                                    |
|  +------------------+    +-------------------+    +--------------+ |
|  |  DATA LAYER      |    |  STRATEGY LAYER   |    | EXECUTION    | |
|  |                  |    |                   |    | LAYER        | |
|  |  - Gamma API     |    |  - Copy Trader    |    |              | |
|  |  - CLOB API      |--->|  - AI Predictor   |--->| - Order Mgr  | |
|  |  - WebSocket     |    |  - Arb Scanner    |    | - Position   | |
|  |  - News APIs     |    |  - Stink Bidder   |    |   Manager    | |
|  |  - On-chain Data |    |  - Market Maker   |    | - Risk Mgr   | |
|  +------------------+    +-------------------+    +--------------+ |
|           |                       |                      |         |
|           v                       v                      v         |
|  +------------------+    +-------------------+    +--------------+ |
|  |  STORAGE LAYER   |    |  MONITORING LAYER |    | NOTIFICATION | |
|  |                  |    |                   |    | LAYER        | |
|  |  - SQLite/PG     |    |  - P&L Tracker    |    |              | |
|  |  - ChromaDB      |    |  - Trade Logger   |    | - Telegram   | |
|  |  - Market Cache  |    |  - Health Checks  |    | - CLI Output | |
|  +------------------+    +-------------------+    +--------------+ |
+------------------------------------------------------------------+
```

### 4.3 Module Structure

```
polymarket-bot/
├── src/
│   ├── core/                    # Core infrastructure
│   │   ├── config.py            # Pydantic settings, .env loading
│   │   ├── client.py            # Polymarket API client wrapper (CLOB, Gamma, Data)
│   │   ├── websocket.py         # WebSocket manager for real-time feeds
│   │   ├── wallet.py            # Wallet management, signing, balance checks
│   │   └── db.py                # Database models and connection
│   │
│   ├── strategies/              # Trading strategy implementations
│   │   ├── base.py              # Abstract base strategy class
│   │   ├── copy_trader.py       # Copy trading strategy
│   │   ├── ai_predictor.py      # AI prediction engine
│   │   ├── arb_scanner.py       # Parity arbitrage scanner
│   │   ├── stink_bidder.py      # Stink bid placement
│   │   └── market_maker.py      # Market making (future)
│   │
│   ├── ai/                      # AI/LLM components
│   │   ├── llm.py               # LiteLLM wrapper for multi-model access
│   │   ├── prompts.py           # Prompt templates for probability estimation
│   │   ├── news.py              # News ingestion and processing
│   │   └── rag.py               # ChromaDB RAG pipeline
│   │
│   ├── execution/               # Trade execution and management
│   │   ├── order_manager.py     # Order creation, submission, tracking
│   │   ├── position_manager.py  # Position tracking, P&L, exposure
│   │   └── risk_manager.py      # Risk controls, kill switch, limits
│   │
│   ├── notifications/           # Alert system
│   │   ├── telegram.py          # Telegram bot integration
│   │   └── formatter.py         # Message formatting
│   │
│   ├── monitoring/              # Observability
│   │   ├── pnl.py               # P&L calculation and tracking
│   │   ├── health.py            # System health checks
│   │   └── logger.py            # Structured logging setup
│   │
│   └── backtest/                # Backtesting and paper trading
│       ├── engine.py            # Backtesting engine
│       ├── paper_trader.py      # Paper trading mode (simulated execution)
│       └── data_loader.py       # Historical data loading
│
├── tests/                       # Test suite
│   ├── unit/
│   ├── integration/
│   └── fixtures/
│
├── scripts/                     # Utility scripts
│   ├── setup_account.py         # Guided Polymarket account setup
│   ├── generate_api_keys.py     # API key generation helper
│   └── migrate_db.py            # Database migrations
│
├── config/                      # Configuration files
│   ├── .env.example             # Environment variable template
│   ├── strategies.yaml          # Strategy-specific configuration
│   └── wallets.yaml             # Tracked wallets for copy trading
│
├── docker/
│   ├── Dockerfile
│   └── docker-compose.yml
│
├── PRD.md                       # This document
├── CLAUDE.md                    # Claude Code project context
├── pyproject.toml               # Python project config (uv/pip)
├── requirements.txt             # Pinned dependencies
└── README.md                    # Setup and usage guide
```

---

## 5. Position & Risk Management

### 5.1 Position Management (Full Auto)
- **Take-Profit:** Configurable per-strategy (default: 50% gain → sell half, 100% gain → sell remaining)
- **Stop-Loss:** Configurable per-strategy (default: 25% loss → exit position)
- **Trailing Stop:** After reaching take-profit threshold, trail by configurable % (default: 10%)
- **Max Position Size:** No single position exceeds configurable % of portfolio (default: 15%)
- **Max Open Positions:** Configurable limit (default: 10 simultaneous positions)

### 5.2 Risk Controls
- **Kill Switch:** Instantly cancel all open orders and optionally exit all positions via CLI command or Telegram
- **Daily Loss Limit:** If daily P&L drops below configurable threshold (default: -10% of portfolio), halt all new trades
- **Per-Strategy Allocation:** Each strategy has a max capital allocation (e.g., copy trading: 40%, AI: 30%, arb: 15%, stink: 15%)
- **Slippage Protection:** Maximum slippage tolerance per trade (default: 2%)
- **Rate Limit Management:** Respect Polymarket's 60 orders/minute limit with built-in throttling and exponential backoff

### 5.3 Capital Allocation (Optimized for <$1K)

| Strategy | Allocation | Rationale |
|----------|-----------|-----------|
| Copy Trading | 40% (~$400) | Highest probability of consistent returns |
| AI Prediction | 30% (~$300) | Highest upside potential per trade |
| Stink Bids | 20% (~$200) | Low effort, occasional big wins |
| Arbitrage | 10% (~$100) | Reserved for guaranteed-profit opportunities |
| **Reserve** | Maintain 10% cash buffer at all times |

---

## 6. Data Sources

### 6.1 Market Data (First-Party)
- **Gamma API** (`https://gamma-api.polymarket.com/`) - Market discovery, metadata, categories
- **CLOB API** - Order book, trade execution, order management
- **CLOB WebSocket** (`wss://ws-subscriptions-clob.polymarket.com`) - Real-time order book, trade notifications
- **Data API** - User positions, trade history, portfolio data

### 6.2 Whale Tracking (Copy Trading Signals)
- **Polymarket Leaderboard** - Identify top performers by win rate and ROI
- **On-chain monitoring** - Track target wallets via Polygon for new position entries
- **Data API** - Poll target wallet positions for change detection

### 6.3 News & Sentiment (AI Prediction Signals)
- **NewsAPI / GDELT / Event Registry** - Breaking news detection
- **RSS Feeds** - Low-latency ingestion from major outlets
- **Social media** - Twitter/X API for sentiment analysis (if API access available)
- **Google News** - Broad aggregation as fallback

### 6.4 Cross-Platform Reference
- **Kalshi, Metaculus, PredictIt** - Probability comparison for edge detection (when AI evaluating a market, compare with other platforms' implied probabilities)

---

## 7. Notification System (Telegram)

### 7.1 Trade Alerts
- New position entered (strategy, market, side, size, price, reasoning)
- Position closed (P&L, hold duration, exit reason)
- Stop-loss / take-profit triggered
- Arbitrage opportunity detected

### 7.2 System Alerts
- Daily P&L summary with per-strategy breakdown
- Portfolio value update
- Risk limit warnings (approaching daily loss limit, high exposure)
- System errors or connection issues
- Kill switch activation confirmation

### 7.3 Telegram Commands (Bot Control)
- `/status` - Current portfolio, open positions, active strategies
- `/pnl` - Today's P&L with breakdown
- `/kill` - Emergency kill switch (cancel all orders)
- `/pause [strategy]` - Pause a specific strategy or all
- `/resume [strategy]` - Resume paused strategy
- `/balance` - Current USDC balance and positions value

---

## 8. Backtesting & Paper Trading

### 8.1 Backtesting Engine
- Load historical market data from Polymarket (price history, resolution outcomes)
- Simulate strategy execution against historical data
- Generate performance reports: total return, Sharpe ratio, max drawdown, win rate
- Compare strategies head-to-head with same historical data
- Configurable start date, end date, and initial capital

### 8.2 Paper Trading Mode
- Connect to live Polymarket data feeds (real prices, real order books)
- Simulate order execution without submitting real orders
- Track simulated P&L in real-time
- Identical logging and Telegram notifications as live mode (prefixed with "[PAPER]")
- Configurable duration before auto-switching to live mode
- Minimum paper trading period: 48 hours recommended before going live

---

## 9. Onboarding Flow (Account Setup)

Since the user is starting fresh, the bot includes a guided setup:

1. **Create Polymarket Account** - Guide user through account creation
2. **Fund Wallet** - Instructions to deposit USDC to Polygon wallet
3. **First Manual Trade** - User must execute 1 manual trade to initialize wallet permissions and USDC spending approvals
4. **Generate API Keys** - Guided CLOB API key generation (API Key, Secret, Passphrase)
5. **Configure Environment** - Interactive `.env` file creation with wallet PK, API keys, Telegram bot token
6. **Verify Connection** - Test API connectivity, check balances, confirm permissions
7. **Select Strategies** - Choose which strategies to activate
8. **Paper Trading** - Start in paper mode for 48h before going live

---

## 10. Dashboard (Phase 2 - Nice to Have)

A lightweight web dashboard for visual monitoring:

- **Tech:** Vite + React + Tailwind CSS
- **Features:**
  - Real-time P&L chart
  - Open positions table with live prices
  - Strategy performance comparison
  - Trade history log
  - Risk metrics (exposure, drawdown, daily P&L)
  - One-click kill switch
- **Deployment:** Runs alongside the bot on the same VPS, accessible via browser

---

## 11. Development Phases

### Phase 1: Foundation & Copy Trading (MVP)
**Goal:** Bot can copy trade profitable wallets and make money

- Project setup (Python, dependencies, Docker)
- Polymarket API client (CLOB, Gamma, Data API wrappers)
- Wallet management and authentication
- Account setup wizard
- Copy trading strategy implementation
- Basic position management (TP, SL)
- Telegram notifications
- Paper trading mode
- CLI interface for bot control

### Phase 2: AI Prediction Engine
**Goal:** Bot can independently find and exploit mispriced markets

- News ingestion pipeline
- LiteLLM integration (Claude, Gemini)
- Probability estimation prompts
- ChromaDB RAG pipeline
- Edge detection and bet sizing
- AI strategy integration into main bot loop

### Phase 3: Arbitrage & Stink Bids
**Goal:** Passive income streams running alongside primary strategies

- Parity arbitrage scanner
- Stink bid placement and management
- Combined strategy orchestration
- Advanced risk management (cross-strategy exposure)

### Phase 4: Backtesting & Optimization
**Goal:** Validate and improve strategies with historical data

- Historical data collection and storage
- Backtesting engine
- Strategy performance analysis
- Parameter optimization

### Phase 5: Dashboard & Polish
**Goal:** Visual monitoring and production hardening

- React dashboard
- Advanced analytics
- Production deployment hardening
- Market making strategy (when capital threshold reached)

---

## 12. Success Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| **Monthly ROI** | >10% on deployed capital | P&L tracker |
| **Win Rate** | >55% across all strategies | Trade history analysis |
| **Max Drawdown** | <20% of portfolio | Risk manager |
| **Uptime** | >99% when deployed to VPS | Health monitoring |
| **Latency** | <2s from signal to order submission | Execution logging |
| **Paper Trading** | Profitable for 48h before going live | Paper trade P&L |

---

## 13. Key Constraints & Risks

| Constraint | Mitigation |
|-----------|-----------|
| **Polymarket rate limits** (60 orders/min) | Built-in throttling, order batching, exponential backoff |
| **2% winner fee + up to 3.15% taker fee** | Only trade when edge exceeds 5%+ to ensure profitability after fees |
| **Small capital (<$1K)** | Focus on high-conviction trades, avoid spreading too thin |
| **Starting fresh (no account)** | Guided setup wizard included in Phase 1 |
| **Whale copy latency** | Poll frequency optimization; accept that we trade slightly after whales |
| **LLM API costs** | Use efficient prompting, cache results, batch market evaluations |
| **Regional restrictions** | User is responsible for ensuring Polymarket is legal in their jurisdiction |
| **Smart contract risk** | Use official SDKs only, never approve unlimited token spending |
| **Oracle resolution disputes** | Avoid binary markets with ambiguous resolution criteria |

---

## 14. Non-Functional Requirements

- **Security:** Private keys stored in `.env` file only, never hardcoded or logged. `.env` in `.gitignore`
- **Reliability:** Graceful reconnection on WebSocket/API failures. Persistent state across restarts
- **Observability:** Structured JSON logging. All trades logged with full context (strategy, reasoning, market state)
- **Configurability:** All parameters configurable via `.env` and `strategies.yaml` without code changes
- **Testability:** All strategies unit-testable with mocked API responses
- **Portability:** Docker deployment works on any Linux VPS ($5-10/month DigitalOcean/Hetzner)
