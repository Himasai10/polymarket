# Pitfalls Research

**Domain:** Polymarket Automated Trading Bot
**Researched:** 2026-02-13
**Confidence:** HIGH

## Critical Pitfalls

### Pitfall 1: web3.py Version Hell

**What goes wrong:**
Installing web3.py without pinning the version causes `eth-typing` dependency conflicts. The bot fails to install or crashes with cryptic import errors.

**Why it happens:**
web3.py v7+ changed its dependency tree significantly. py-clob-client was built against v6.x. Mixing versions causes `eth-typing`, `eth-abi`, and `eth-account` version mismatches.

**How to avoid:**
Pin `web3==6.14.0` in requirements. Never let it auto-resolve. Add a comment explaining why.

**Warning signs:**
- `ModuleNotFoundError: eth_typing`
- `ImportError: cannot import name 'encode_structured_data'`
- Dependency resolver takes >5 minutes

**Phase to address:** Phase 1 (Foundation)

---

### Pitfall 2: Proxy Wallet Confusion

**What goes wrong:**
Orders fail silently or get attributed to the wrong account. Funds appear "locked" or unavailable.

**Why it happens:**
Polymarket uses a proxy wallet architecture. The signing key (private key in .env) is different from the "funder" address that holds the USDC. The `funder` parameter in ClobClient must be set correctly, or orders are signed but never matched.

**How to avoid:**
- Always configure both the private key AND the funder/browser address
- The funder address is the Polymarket "browser wallet" visible in the UI
- Test with a tiny order ($0.10) before running strategies

**Warning signs:**
- Orders submit successfully but never appear in the order book
- Balance shows 0 even though USDC is in the wallet
- "Insufficient balance" errors despite having funds

**Phase to address:** Phase 1 (Foundation — wallet setup)

---

### Pitfall 3: First Trade Requirement

**What goes wrong:**
API key is generated, wallet is funded, but every order returns a permission error or gets rejected.

**Why it happens:**
Polymarket requires at least one manual trade through the web UI to initialize on-chain approvals and USDC spending allowances for the CTF Exchange contract. Without this, the proxy wallet doesn't have the necessary token approvals.

**How to avoid:**
Include in onboarding: "Before running the bot, place one small manual trade ($1) through polymarket.com to initialize wallet permissions."

**Warning signs:**
- "Execution reverted" errors on first API trade
- USDC allowance is 0 when queried on-chain

**Phase to address:** Phase 1 (Onboarding wizard)

---

### Pitfall 4: Fee Miscalculation Leading to Negative Edge

**What goes wrong:**
Bot places trades that appear profitable but lose money after fees. Especially devastating for arbitrage.

**Why it happens:**
Polymarket has a complex fee structure:
- 2% winner fee (on profit at resolution)
- Up to 3.15% dynamic taker fee (for trades within 15-min window)
- Polygon gas costs ($0.01-$0.05 per trade)
Combined, minimum profitable edge needs to be ~5%+, not the 2-3% that looks profitable on paper.

**How to avoid:**
- Build fee calculation into every signal evaluation
- Arb scanner must check: `(1.00 - yes_price - no_price) > 0.05` (not just > 0.00)
- Include gas costs in P&L calculation
- Log effective fee rate on every executed trade

**Warning signs:**
- Win rate is high but P&L is flat or negative
- Arb trades show tiny "profit" that disappears after settlement

**Phase to address:** Phase 1 (Order Manager — fee calculation built in from day 1)

---

### Pitfall 5: Copy Trading Latency = Worse Prices

**What goes wrong:**
Bot copies a whale's trade but enters at a significantly worse price because the whale's trade already moved the market.

**Why it happens:**
Detection lag: Data API polling has latency (seconds to minutes). By the time the bot detects the whale's new position and submits an order, the price has already moved in the whale's direction.

**How to avoid:**
- Set maximum slippage tolerance (e.g., 5% worse than whale's entry)
- If current price is >X% worse than whale's entry, skip the trade
- Track effective slippage per copied trade for optimization
- Consider shorter polling intervals for high-priority wallets

**Warning signs:**
- Consistently entering positions at 5-10%+ worse prices than the target wallet
- Copy trades showing immediate unrealized loss at entry

**Phase to address:** Phase 2 (Copy Trading strategy — slippage protection)

---

### Pitfall 6: LLM Confidence ≠ Accuracy

**What goes wrong:**
AI prediction engine bets large amounts on markets where the LLM is "90% confident" but wrong. Losses compound because the bot trusts AI confidence too literally.

**Why it happens:**
LLMs are calibration-poor — they express high confidence even when reasoning from insufficient data. "I'm 90% sure" from an LLM doesn't map to 90% historical accuracy.

**How to avoid:**
- Never use raw LLM confidence as position sizing input
- Require multi-model agreement (Claude AND Gemini must agree)
- Cap AI-driven positions at 30% of AI allocation regardless of stated confidence
- Track LLM accuracy over time; adjust trust weights based on historical performance
- Start with small positions until AI track record is established

**Warning signs:**
- AI strategy shows high conviction on most trades (everything looks 80%+ confident)
- AI strategy win rate is below 55% despite "high confidence" trades

**Phase to address:** Phase 3 (AI Prediction — calibration and sizing logic)

---

### Pitfall 7: WebSocket Disconnections During Critical Moments

**What goes wrong:**
WebSocket connection drops during a high-volatility event. Position manager can't evaluate TP/SL rules. Bot holds losing positions through a crash.

**Why it happens:**
WebSocket connections are inherently fragile. Server-side disconnects, network blips, and Polymarket maintenance all cause drops. High-volatility events (elections, crypto crashes) are exactly when connections are least reliable and most needed.

**How to avoid:**
- Implement exponential backoff reconnection (not immediate retry — that gets rate limited)
- Position manager must have a "stale data" fallback: if no price update for >30s, poll REST API
- Critical positions should have on-chain stop-losses if possible (defense in depth)
- Log all disconnection events with duration

**Warning signs:**
- Gaps in price history logs
- Position manager "freezes" — no TP/SL evaluations during volatile periods

**Phase to address:** Phase 2 (WebSocket Manager — auto-reconnect with fallback)

---

### Pitfall 8: Overtrading on Small Capital

**What goes wrong:**
Bot spreads $800 across 20+ positions. Each position is too small to be meaningful after fees. Transaction costs eat all profits.

**Why it happens:**
Multiple strategies running simultaneously all generating signals. Without strict allocation limits, capital fragments across too many small positions.

**How to avoid:**
- Maximum 8-10 simultaneous positions with <$1K capital
- Minimum position size: $25 (below this, fees make it unprofitable)
- Per-strategy allocation limits enforced in risk manager
- Quality over quantity: only take high-conviction signals

**Warning signs:**
- Average position size drops below $30
- More than 10 open positions simultaneously
- Transaction fees exceed 5% of average trade size

**Phase to address:** Phase 1 (Risk Manager — position limits from day 1)

---

### Pitfall 9: Stale Market Data Leading to Bad Trades

**What goes wrong:**
Bot places orders based on cached/stale prices. By the time the order reaches the CLOB, the market has moved significantly.

**Why it happens:**
Gamma API data can be seconds to minutes old. If the bot uses cached data for trade decisions without freshness checks, it may act on outdated information.

**How to avoid:**
- Always fetch fresh order book data before placing any order
- Use CLOB API (not Gamma) for execution-critical price checks
- Add data freshness timestamps; reject data older than 10 seconds for trading decisions
- Use WebSocket for real-time data wherever possible

**Warning signs:**
- Orders frequently not filling (price has moved beyond limit)
- Unexpected slippage on "limit" orders

**Phase to address:** Phase 1 (Core client — freshness checks built in)

---

### Pitfall 10: Private Key Exposure

**What goes wrong:**
Private key gets committed to git, logged to console, or exposed in error messages. Attacker drains the wallet.

**Why it happens:**
During development, it's easy to log full config objects (which contain the PK), or commit .env files, or include keys in error traces.

**How to avoid:**
- .env in .gitignore from project inception
- PK fields use `SecretStr` in Pydantic (masked in logs/repr)
- Never log config objects without explicit field filtering
- Pre-commit hook that checks for private key patterns

**Warning signs:**
- Private key visible in any log output
- .env file in git history

**Phase to address:** Phase 1 (Config — SecretStr from day 1)

## Technical Debt Patterns

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|----------|-------------------|----------------|-----------------|
| Polling instead of WebSocket | Simpler implementation | Higher latency, more API calls, missed TP/SL triggers | Phase 1 only; must upgrade in Phase 2 |
| Single-model AI (one LLM) | Faster to build, cheaper | No calibration check, single point of failure | Phase 3 MVP; add ensemble in Phase 3.x |
| SQLite in production | Zero-config deployment | No concurrent writes, limited scale, no remote access | Acceptable until >1000 trades or multi-bot |
| Hardcoded news sources | Faster development | Brittle if APIs change, limited coverage | Phase 3 MVP; make configurable in Phase 4 |
| No backtesting | Ship faster | No historical validation, flying blind | Acceptable if paper trading validates first |

## Integration Gotchas

| Integration | Common Mistake | Correct Approach |
|-------------|----------------|------------------|
| py-clob-client | Not setting `funder` parameter | Always pass funder (browser wallet address) alongside private key |
| WebSocket | No heartbeat/ping handling | Implement ping/pong; reconnect on missed pong after 30s |
| Telegram Bot | Blocking the main event loop | Use async python-telegram-bot v20+; never use sync version |
| LiteLLM | No response caching | Cache identical market evaluations for 5-15 minutes; LLM costs add up fast |
| Gamma API | Treating `bestBid`/`bestAsk` as tradeable prices | These are indicative; always check CLOB order book for actual liquidity |
| ChromaDB | Unbounded document growth | Set max collection size; prune old news articles weekly |

## Performance Traps

| Trap | Symptoms | Prevention | When It Breaks |
|------|----------|------------|----------------|
| Scanning all markets every tick | CPU/API usage spikes; rate limits hit | Filter to active, liquid markets; cache market list | >500 active markets |
| LLM call per market per tick | $50+/day in API costs; slow cycle time | Batch evaluations; only re-evaluate when news changes | >20 markets evaluated per hour |
| Storing full order book history | Database grows to GB; queries slow down | Store snapshots, not tick data; prune old data | >1 month of operation |
| No connection pooling | New TCP connection per API call; latency spikes | Use httpx.AsyncClient with connection pooling | >100 API calls/minute |

## Security Mistakes

| Mistake | Risk | Prevention |
|---------|------|------------|
| Private key in source code | Complete wallet drain | .env + SecretStr + .gitignore + pre-commit hook |
| API keys in logs | Account compromise | Structured logging with field filtering; never log raw config |
| Unlimited USDC approval | Smart contract exploit drains all USDC | Approve only needed amounts; check existing approvals |
| No VPS firewall | Bot exposed to internet | UFW firewall; only allow SSH and outbound; no inbound ports needed |
| Telegram bot token in public repo | Attacker controls your bot | Same treatment as private key; .env only |

## "Looks Done But Isn't" Checklist

- [ ] **Order placement:** Often missing fee calculation in P&L tracking — verify post-fee P&L matches expected
- [ ] **Copy trading:** Often missing deduplication — verify bot doesn't copy the same trade twice
- [ ] **Position manager:** Often missing market resolution handling — verify positions close automatically when market resolves
- [ ] **WebSocket:** Often missing reconnection — verify bot recovers after 60s disconnect
- [ ] **Paper trading:** Often missing realistic slippage simulation — verify paper P&L ≈ live P&L
- [ ] **Kill switch:** Often missing order cancellation confirmation — verify all orders actually cancelled, not just requested
- [ ] **Risk manager:** Often missing cross-strategy exposure check — verify total exposure doesn't exceed portfolio
- [ ] **Telegram:** Often missing error handling for rate limits — verify bot doesn't crash on Telegram API errors
- [ ] **Database:** Often missing WAL mode for SQLite — verify concurrent reads don't block writes

## Recovery Strategies

| Pitfall | Recovery Cost | Recovery Steps |
|---------|---------------|----------------|
| web3.py version conflict | LOW | Pin version in requirements; rebuild venv |
| Proxy wallet misconfiguration | LOW | Reconfigure funder address; test with tiny trade |
| Fee miscalculation | MEDIUM | Recalculate historical P&L; adjust thresholds; may have lost money |
| Private key exposure | HIGH | Immediately transfer all funds to new wallet; rotate all API keys; audit git history |
| Overtrading losses | MEDIUM | Pause bot; review trade history; tighten position limits; reduce capital at risk |
| WebSocket data gap | LOW | Reconcile positions via REST API; verify all positions current |
| LLM confidence losses | MEDIUM | Reduce AI allocation; add calibration requirements; track accuracy metrics |

## Pitfall-to-Phase Mapping

| Pitfall | Prevention Phase | Verification |
|---------|------------------|--------------|
| web3.py version hell | Phase 1 | `pip freeze \| grep web3` shows 6.14.0 |
| Proxy wallet confusion | Phase 1 | Test trade succeeds with correct attribution |
| First trade requirement | Phase 1 | Onboarding wizard includes manual trade step |
| Fee miscalculation | Phase 1 | P&L report shows post-fee amounts |
| Copy trading latency | Phase 2 | Slippage metric tracked and <5% average |
| LLM overconfidence | Phase 3 | Multi-model agreement required; accuracy tracking |
| WebSocket disconnections | Phase 2 | Auto-reconnect test with 60s simulated drop |
| Overtrading | Phase 1 | Risk manager rejects when >10 positions open |
| Stale data | Phase 1 | Freshness timestamp on all data objects |
| Private key exposure | Phase 1 | Pre-commit hook + SecretStr + .gitignore verified |

## Sources

- Polymarket developer documentation — API constraints, proxy wallet architecture
- py-clob-client GitHub issues — common installation problems, web3.py conflicts
- NotebookLM research — Moon Dev, Alpha Stack debugging experiences
- Polymarket/agents GitHub — architecture pitfalls and design decisions
- Web research — "Polymarket bot common problems" (Feb 2026)
- Reddit r/polymarket — trader experiences with API issues

---
*Pitfalls research for: Polymarket Automated Trading Bot*
*Researched: 2026-02-13*
