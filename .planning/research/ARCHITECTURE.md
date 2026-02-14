# Architecture Research

**Domain:** Polymarket Automated Trading Bot
**Researched:** 2026-02-13
**Confidence:** HIGH

## Standard Architecture

### System Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        DATA LAYER                                │
│  ┌───────────┐  ┌───────────┐  ┌───────────┐  ┌─────────────┐  │
│  │ Gamma API │  │ CLOB API  │  │ WebSocket │  │ News/Social │  │
│  │ (markets) │  │ (orders)  │  │ (realtime)│  │ (signals)   │  │
│  └─────┬─────┘  └─────┬─────┘  └─────┬─────┘  └──────┬──────┘  │
│        │              │              │               │          │
├────────┴──────────────┴──────────────┴───────────────┴──────────┤
│                      STRATEGY LAYER                              │
│  ┌────────────┐ ┌────────────┐ ┌──────────┐ ┌──────────────┐   │
│  │   Copy     │ │    AI      │ │ Arb      │ │   Stink      │   │
│  │  Trader    │ │ Predictor  │ │ Scanner  │ │   Bidder     │   │
│  └─────┬──────┘ └─────┬──────┘ └────┬─────┘ └──────┬───────┘   │
│        │              │             │              │            │
│        └──────────────┴─────────────┴──────────────┘            │
│                          │ (signals)                            │
├──────────────────────────┴──────────────────────────────────────┤
│                      EXECUTION LAYER                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐   │
│  │ Order Manager│  │  Position    │  │   Risk Manager       │   │
│  │ (create,     │  │  Manager     │  │   (limits, kill      │   │
│  │  cancel,     │  │  (TP/SL,    │  │    switch, exposure)  │   │
│  │  track)      │  │  trailing)   │  │                      │   │
│  └──────────────┘  └──────────────┘  └──────────────────────┘   │
│                                                                  │
├──────────────────────────────────────────────────────────────────┤
│                      SUPPORT LAYER                               │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────────────┐  │
│  │ Database │  │ ChromaDB │  │ Telegram │  │ Logger/Monitor │  │
│  │ (trades, │  │ (RAG     │  │ (alerts, │  │ (audit trail,  │  │
│  │  state)  │  │  context) │  │  control)│  │  health)       │  │
│  └──────────┘  └──────────┘  └──────────┘  └────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
```

### Component Responsibilities

| Component | Responsibility | Typical Implementation |
|-----------|----------------|------------------------|
| **Gamma Client** | Market discovery, event metadata, category browsing | REST client wrapping Gamma API; caches market data locally |
| **CLOB Client** | Order placement, cancellation, order book queries | py-clob-client SDK; handles signing and submission |
| **WebSocket Manager** | Real-time price feeds, order book updates, trade notifications | Persistent connection with auto-reconnect; distributes events to subscribers |
| **News Ingester** | Fetch breaking news, RSS feeds, social signals | Async polling of news APIs; deduplication; relevance filtering |
| **Copy Trader** | Monitor whale wallets, detect new positions, generate copy signals | Poll Data API for target wallet changes; apply conviction filters |
| **AI Predictor** | Evaluate event probabilities, detect market mispricing | LiteLLM for multi-model queries; ChromaDB for context retrieval; edge threshold filtering |
| **Arb Scanner** | Detect Yes+No < $0.97 opportunities across all markets | Continuous market scanning via Gamma API; FOK order execution |
| **Stink Bidder** | Place far-below-market limit orders on selected markets | Market analysis for opportunity selection; GTC order placement and refresh |
| **Order Manager** | Create, submit, track, and cancel orders; enforce rate limits | Queue-based order submission; 60/min throttling; order state machine |
| **Position Manager** | Track open positions; execute TP/SL/trailing stop rules | WebSocket-driven price monitoring; rule evaluation engine |
| **Risk Manager** | Enforce limits: daily loss, position size, exposure, kill switch | Cross-strategy exposure calculation; circuit breakers |
| **Database** | Persist trade history, market data, strategy state, P&L | SQLite for dev; PostgreSQL for prod; async drivers |
| **Telegram Bot** | Send alerts, receive commands, provide status reports | python-telegram-bot async; command handlers for /status, /kill, /pnl |
| **Logger** | Structured audit trail of all decisions and trades | structlog JSON output; every order logged with strategy + reasoning |

## Recommended Project Structure

```
polymarket-bot/
├── src/
│   ├── __init__.py
│   ├── main.py                  # Entry point: async event loop, strategy orchestration
│   ├── core/                    # Shared infrastructure
│   │   ├── __init__.py
│   │   ├── config.py            # Pydantic settings from .env + strategies.yaml
│   │   ├── client.py            # Polymarket API client (wraps py-clob-client, Gamma, Data)
│   │   ├── websocket.py         # WebSocket manager with auto-reconnect + event distribution
│   │   ├── wallet.py            # Wallet management, balance checks, signing helpers
│   │   ├── db.py                # Database models, connection, migrations
│   │   └── rate_limiter.py      # Token bucket rate limiter (60 orders/min)
│   │
│   ├── strategies/              # Strategy implementations (all inherit BaseStrategy)
│   │   ├── __init__.py
│   │   ├── base.py              # Abstract base: start(), stop(), on_tick(), get_signals()
│   │   ├── copy_trader.py       # Whale tracking + position replication
│   │   ├── ai_predictor.py      # LLM probability estimation + edge detection
│   │   ├── arb_scanner.py       # Parity arbitrage detection + execution
│   │   └── stink_bidder.py      # Low-ball limit order placement
│   │
│   ├── ai/                      # AI/LLM components
│   │   ├── __init__.py
│   │   ├── llm.py               # LiteLLM wrapper: multi-model calls, caching
│   │   ├── prompts.py           # Prompt templates for probability estimation
│   │   ├── news.py              # News ingestion: APIs, RSS, deduplication
│   │   └── rag.py               # ChromaDB pipeline: embed, store, retrieve context
│   │
│   ├── execution/               # Trade execution layer
│   │   ├── __init__.py
│   │   ├── order_manager.py     # Order creation, queuing, submission, tracking
│   │   ├── position_manager.py  # Position tracking, TP/SL/trailing evaluation
│   │   └── risk_manager.py      # Exposure limits, daily loss, kill switch
│   │
│   ├── notifications/           # Alert system
│   │   ├── __init__.py
│   │   ├── telegram.py          # Telegram bot: send alerts, handle commands
│   │   └── formatter.py         # Message formatting for trade alerts, P&L reports
│   │
│   └── monitoring/              # Observability
│       ├── __init__.py
│       ├── pnl.py               # P&L calculation: per-trade, per-strategy, daily
│       ├── health.py            # Health checks: API connectivity, WebSocket status
│       └── logger.py            # structlog setup, JSON formatting
│
├── backtest/                    # Backtesting (separate from live code)
│   ├── __init__.py
│   ├── engine.py                # Backtesting engine: event replay, simulated execution
│   ├── paper_trader.py          # Paper trading: live data, simulated orders
│   └── data_loader.py           # Historical data loading and caching
│
├── scripts/                     # Utility scripts
│   ├── setup_account.py         # Guided Polymarket account setup wizard
│   ├── check_balance.py         # Quick balance and position check
│   └── export_trades.py         # Export trade history for tax/analysis
│
├── config/                      # Configuration files
│   ├── .env.example             # Template for secrets
│   ├── strategies.yaml          # Strategy parameters (allocations, thresholds, wallets)
│   └── markets.yaml             # Market filters and preferences
│
├── tests/
│   ├── unit/
│   │   ├── test_order_manager.py
│   │   ├── test_risk_manager.py
│   │   ├── test_copy_trader.py
│   │   └── test_arb_scanner.py
│   ├── integration/
│   │   ├── test_api_client.py
│   │   └── test_websocket.py
│   └── fixtures/
│       ├── mock_orderbook.json
│       └── mock_positions.json
│
├── docker/
│   ├── Dockerfile
│   └── docker-compose.yml
│
├── .env.example
├── pyproject.toml
└── README.md
```

### Structure Rationale

- **src/core/:** Shared infrastructure that every strategy uses. Strategies never call Polymarket APIs directly — they go through the core client.
- **src/strategies/:** Each strategy is isolated with a common interface (BaseStrategy). Easy to add/remove strategies without touching other code.
- **src/execution/:** Centralized trade execution. All strategies produce "signals"; the execution layer converts them to orders. This prevents strategies from conflicting or exceeding risk limits.
- **backtest/:** Separate from src/ because backtesting imports strategy code but uses simulated execution. Clean boundary between live and simulated.

## Architectural Patterns

### Pattern 1: Strategy-Signal-Execution Pipeline

**What:** Strategies produce signals (buy/sell recommendations); execution layer converts to orders. Strategies never place orders directly.
**When to use:** Always in multi-strategy bots.
**Trade-offs:** Adds indirection but prevents strategies from conflicting, exceeding rate limits, or violating risk rules.

```python
# Strategy produces a signal
class Signal:
    strategy: str      # "copy_trader"
    market_id: str     # Polymarket condition ID
    side: str          # "BUY" or "SELL"
    token_id: str      # Yes or No token
    price: float       # Target price
    size: float        # USDC amount
    urgency: str       # "high" (FOK) or "normal" (GTC)
    reasoning: str     # Why this trade

# Execution layer processes signals
async def process_signal(signal: Signal):
    if not risk_manager.approve(signal):
        logger.info("Signal rejected by risk manager", signal=signal)
        return
    order = order_manager.create_order(signal)
    await order_manager.submit(order)
```

### Pattern 2: Event-Driven Price Monitoring

**What:** WebSocket feeds push price updates; position manager evaluates TP/SL rules reactively instead of polling.
**When to use:** For position management (TP/SL/trailing stops).
**Trade-offs:** More complex setup but near-instant reaction to price changes vs. polling delay.

```python
# WebSocket distributes price events to subscribers
class PriceEvent:
    market_id: str
    token_id: str
    price: float
    timestamp: datetime

# Position manager subscribes to relevant markets
async def on_price_update(event: PriceEvent):
    for position in positions_for_market(event.market_id):
        if should_take_profit(position, event.price):
            emit_signal(close_position_signal(position, "take_profit"))
        elif should_stop_loss(position, event.price):
            emit_signal(close_position_signal(position, "stop_loss"))
```

### Pattern 3: Abstract Base Strategy

**What:** All strategies inherit from BaseStrategy with a common lifecycle: `start()`, `stop()`, `tick()`, `get_signals()`.
**When to use:** Multi-strategy orchestration.
**Trade-offs:** Enforces consistency; makes it trivial to add/remove strategies.

```python
class BaseStrategy(ABC):
    def __init__(self, config, client, db):
        self.config = config
        self.client = client
        self.db = db
        self.is_running = False

    @abstractmethod
    async def tick(self) -> list[Signal]:
        """Called periodically; return trading signals."""
        ...

    async def start(self):
        self.is_running = True

    async def stop(self):
        self.is_running = False
```

## Data Flow

### Trade Execution Flow

```
[Strategy tick()] → [Signal] → [Risk Manager check] → [Order Manager queue]
                                      │                       │
                                   (rejected)           [Rate Limiter]
                                      │                       │
                                   [log+alert]          [CLOB API submit]
                                                              │
                                                        [Order confirmed]
                                                              │
                                                    [Position Manager track]
                                                              │
                                                    [Telegram alert sent]
                                                              │
                                                    [Database record saved]
```

### Copy Trading Data Flow

```
[Data API poll] → [Target wallet positions] → [Diff detection]
                                                     │
                                              [New position found]
                                                     │
                                        [Conviction filter (size > threshold)]
                                                     │
                                            [Calculate position size]
                                                     │
                                              [Emit BUY signal]
```

### AI Prediction Data Flow

```
[News APIs + RSS] → [Dedup + Relevance] → [ChromaDB store]
                                                │
[Gamma API markets] → [Active market list] ─────┤
                                                │
                                    [For each market:]
                                    [Retrieve relevant context from ChromaDB]
                                                │
                                    [LLM: estimate probability]
                                                │
                                    [Compare vs market price]
                                                │
                                    [Edge > threshold? → Emit signal]
```

### Main Event Loop

```
[asyncio.gather]
    ├── [WebSocket listener] → pushes events to subscribers
    ├── [Strategy 1: Copy Trader tick loop] → emits signals
    ├── [Strategy 2: AI Predictor tick loop] → emits signals
    ├── [Strategy 3: Arb Scanner tick loop] → emits signals
    ├── [Strategy 4: Stink Bidder tick loop] → emits signals
    ├── [Position Manager] → monitors TP/SL on price events
    ├── [Order Manager] → processes signal queue, submits orders
    ├── [Telegram Bot] → handles incoming commands
    ├── [Health Monitor] → periodic connectivity checks
    └── [P&L Reporter] → periodic summary generation
```

## Scaling Considerations

| Scale | Architecture Adjustments |
|-------|--------------------------|
| 1 bot, <$1K | Single process, SQLite, single VPS — current architecture |
| 1 bot, $5K-$25K | Add market making strategy; switch to PostgreSQL; add more whale wallets |
| Multiple bots | Separate processes per strategy; shared PostgreSQL; Redis for inter-process communication |
| 100+ markets monitored | Optimize WebSocket subscriptions; batch Gamma API calls; market data caching layer |

### Scaling Priorities

1. **First bottleneck:** Rate limits (60 orders/min). Fix: prioritize high-value signals, queue lower-priority ones.
2. **Second bottleneck:** LLM API latency for AI predictions. Fix: batch market evaluations, cache recent analyses, skip markets with no new news.

## Anti-Patterns

### Anti-Pattern 1: Strategy-Direct-Execution

**What people do:** Each strategy directly calls the CLOB API to place orders.
**Why it's wrong:** Multiple strategies can exceed rate limits, place conflicting orders, or violate risk limits simultaneously.
**Do this instead:** All strategies emit signals; centralized execution layer processes them.

### Anti-Pattern 2: Synchronous API Calls

**What people do:** Use `requests` library and sequential API calls.
**Why it's wrong:** Each API call blocks ~100-500ms; with 4 strategies polling, the bot becomes sluggish and misses opportunities.
**Do this instead:** Use `httpx` with `asyncio` for concurrent API calls across all strategies.

### Anti-Pattern 3: Hardcoded Strategy Parameters

**What people do:** Embed thresholds, allocation percentages, and wallet addresses in code.
**Why it's wrong:** Every parameter change requires code modification, redeployment, and restart.
**Do this instead:** External configuration via `.env` + `strategies.yaml`; hot-reload where possible.

### Anti-Pattern 4: No Separation Between Paper and Live

**What people do:** Use boolean flags scattered throughout code to toggle paper mode.
**Why it's wrong:** Paper mode becomes unreliable; bugs slip through; hard to maintain.
**Do this instead:** Paper trading uses the same strategy code but swaps the execution layer for a simulated one.

## Integration Points

### External Services

| Service | Integration Pattern | Notes |
|---------|---------------------|-------|
| Polymarket CLOB API | REST + WebSocket via py-clob-client | Rate limited to 60 orders/min; requires API key + private key signing |
| Polymarket Gamma API | REST via httpx | Public data; no auth needed; rate limited to 100 req/min |
| Polygon Network | web3.py RPC calls | For USDC balance queries; use public RPC or Alchemy/Infura |
| Telegram Bot API | python-telegram-bot | Requires bot token from @BotFather; async handlers |
| News APIs (NewsAPI/GDELT) | REST via httpx | API keys required; rate limits vary by provider |
| LLM Providers | litellm unified API | API keys for each provider; response caching to reduce costs |

### Internal Boundaries

| Boundary | Communication | Notes |
|----------|---------------|-------|
| Strategy → Execution | Signal objects in async queue | Strategies never call APIs directly |
| WebSocket → Position Manager | Event subscription pattern | Position manager subscribes to markets it has open positions in |
| Risk Manager → Order Manager | Approval/rejection before submission | Every order passes through risk check |
| Any component → Telegram | Notification service (fire-and-forget) | Non-blocking; failure to notify doesn't block trading |

## Suggested Build Order

Based on component dependencies:

1. **Core infrastructure** (config, client, wallet, db) — everything depends on this
2. **Order Manager + Rate Limiter** — can't trade without it
3. **Copy Trading strategy** — first money-making capability
4. **Position Manager (TP/SL)** — protect the capital copy trading deploys
5. **Telegram notifications** — awareness of what's happening
6. **WebSocket integration** — real-time data for position management
7. **AI components (LLM, RAG, news)** — second strategy layer
8. **Arbitrage + Stink Bids** — additional income streams
9. **Paper trading** — safe validation mode
10. **Backtesting** — historical validation

## Sources

- Polymarket/agents GitHub — modular architecture with gamma.py, polymarket.py, chroma.py
- warproxxx/poly-maker GitHub — poly_data, poly_stats, data_updater architecture
- Polymarket developer documentation — API architecture and data flow
- NotebookLM research — Alpha Stack (Vite+React dashboard), Moon Dev (Python bot architecture)
- asyncio best practices for trading bots

---
*Architecture research for: Polymarket Automated Trading Bot*
*Researched: 2026-02-13*
