# Polymarket Bot — Full Codebase Audit

**Date:** 2026-02-14
**Status:** Audit COMPLETE, fixes NOT YET APPLIED
**Commit at audit time:** `6ea6eb3` (Phase 5 — deployment hardening)

---

## Overview

A comprehensive 4-part audit was performed on every source file in the codebase. The bot has 3 strategies (Copy Trading, Parity Arbitrage, Stink Bidding) and must be safe to run with real money. The audit found **~12 CRITICAL**, **~25 HIGH**, **~25 MEDIUM**, and **~15 LOW** severity issues.

**No fixes have been applied yet.** This document serves as the complete bug list and fix plan.

---

## CRITICAL Issues (12) — Must fix before live trading

### C-01: Private key never passed to ClobClient
- **File:** `src/core/client.py:100-108`
- **Bug:** `ClobClient` is constructed without `private_key=pk`. Orders can't be signed.
- **Fix:** Pass `private_key=pk` to the `ClobClient` constructor.

### C-02: Orders signed but never submitted
- **File:** `src/core/client.py:232`
- **Bug:** Calls `create_order()` instead of `create_and_post_order()`. Orders are created locally but never sent to the exchange.
- **Fix:** Use `self.clob.create_and_post_order()`.

### C-03: Token YES/NO identified by array index
- **File:** `src/core/client.py:48-51`
- **Bug:** Assumes `tokens[0]`=YES, `tokens[1]`=NO. API doesn't guarantee ordering.
- **Fix:** Identify tokens by their `outcome` field.

### C-04: Wrong USDC contract address
- **File:** `src/core/wallet.py:19`
- **Bug:** Uses bridged USDC.e (`0x2791Bca1f...`) instead of native USDC (`0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359`). All balance checks return wrong values.
- **Fix:** Replace with correct native USDC address.

### C-05: Synchronous CLOB calls block the event loop
- **File:** `src/core/client.py` (multiple methods)
- **Bug:** `create_and_place_order`, `cancel_order`, `cancel_all_orders`, `get_open_orders`, `get_positions`, `get_price` are synchronous but called from async code. Blocks entire bot.
- **Fix:** Wrap all sync CLOB calls in `asyncio.to_thread()`.

### C-06: Signal.size unit mismatch
- **Files:** `src/strategies/copy_trader.py`, `src/strategies/arb_scanner.py`, `src/strategies/stink_bidder.py`, `src/execution/order_manager.py`, `src/execution/risk_manager.py`
- **Bug:** Copy Trader sends USD, Arb Scanner sends shares, Stink Bidder sends shares. Risk manager treats all as USD. CLOB API expects shares.
- **Fix:** Standardize Signal.size to always be USD. Convert to shares at order execution time in `order_manager.py` using current price.

### C-07: Position marked "closed" before exit order fills
- **File:** `src/execution/position_manager.py:183-186`
- **Bug:** Position status set to "closed" in DB immediately when exit signal is sent, before the order actually fills.
- **Fix:** Use "closing" intermediate state. Only mark "closed" on fill confirmation.

### C-08: Race condition — duplicate exit orders
- **File:** `src/execution/position_manager.py:60-149`
- **Bug:** Rapid price updates can trigger multiple exit orders for the same position simultaneously.
- **Fix:** Add position-level locking with a `_closing_positions: set` guard.

### C-09: Trades approved when wallet balance check fails
- **File:** `src/execution/risk_manager.py:110-120`
- **Bug:** If wallet balance check throws an exception, the risk check passes (fail-open).
- **Fix:** Fail-closed — deny trade when balance is uncertain.

### C-10: Kill switch doesn't drain pending signal queue
- **Files:** `src/execution/risk_manager.py:135-141`, `src/execution/order_manager.py:80-97`
- **Bug:** When kill switch activates, existing signals in the queue still execute.
- **Fix:** Drain the signal queue in `activate_kill_switch`.

### C-11: Daily loss limit ignores unrealized losses
- **File:** `src/execution/risk_manager.py:60-74`
- **Bug:** Daily loss calculation only considers realized P&L, not unrealized. Bot can keep opening positions while sitting on huge unrealized losses.
- **Fix:** Include unrealized P&L from open positions in daily loss calculation.

### C-12: Arb orders are not atomic — naked position risk
- **File:** `src/strategies/arb_scanner.py:235-282`
- **Bug:** Two FOK orders submitted sequentially. If first fills but second fails, bot holds naked directional exposure.
- **Fix:** Add rollback logic — if second leg fails, immediately submit a sell order for the first leg.

---

## HIGH Issues (25+) — Should fix before live trading

### H-01: Arb fee calculation math error
- **File:** `src/strategies/arb_scanner.py:192-195`
- **Bug:** Fee calculated as percentage-of-percentage (e.g., `fee_rate * price` twice). Overstates or understates true cost.
- **Fix:** Correct the fee formula: `fee = size * price * fee_rate` for each leg.

### H-02: No fill confirmation in OrderManager
- **File:** `src/execution/order_manager.py`
- **Bug:** After submitting an order, no check for fill status. Bot assumes success.
- **Fix:** Poll order status after submission, update position on fill/partial/reject.

### H-03: No retry on failed exit orders
- **File:** `src/execution/order_manager.py`
- **Bug:** If an exit order fails, the position is stuck with no further attempts.
- **Fix:** Implement retry logic with exponential backoff for failed exits.

### H-04: Unbounded signal queue
- **File:** `src/execution/order_manager.py`
- **Bug:** Signal queue has no max size. Under load, memory grows unbounded.
- **Fix:** Use `asyncio.Queue(maxsize=N)` with a reasonable limit.

### H-05: No risk check in `_execute_signal`
- **File:** `src/execution/order_manager.py`
- **Bug:** `_execute_signal` bypasses risk manager for some code paths.
- **Fix:** Always call risk manager before executing any signal.

### H-06: P&L ignores fees entirely
- **File:** `src/execution/position_manager.py:164-167`
- **Bug:** P&L calculation doesn't account for trading fees (2% winner + taker fee).
- **Fix:** Include fees in P&L: `pnl = gross_pnl - entry_fee - exit_fee`.

### H-07: Trailing stop ratchets wrong direction for SELL positions
- **File:** `src/execution/position_manager.py:140-149`
- **Bug:** Trailing stop logic only works correctly for BUY/long positions. For SELL (short) positions, the ratchet goes the wrong way.
- **Fix:** Invert trailing stop logic when position side is SELL.

### H-08: `_active_orders` never populated in StinkBidder
- **File:** `src/strategies/stink_bidder.py:240-265`
- **Bug:** `_active_orders` dict is initialized but never written to after order placement. This means duplicate bid detection is broken — unlimited duplicate bids can be placed.
- **Fix:** Populate `_active_orders` after successful order submission, and clean up on cancel/fill/expiry.

### H-09: Sync CLOB call in StinkBidder async context
- **File:** `src/strategies/stink_bidder.py:209`
- **Bug:** Direct synchronous CLOB API call inside async method.
- **Fix:** Wrap in `asyncio.to_thread()`.

### H-10: Copy Trader has no exit signals when whale sells
- **File:** `src/strategies/copy_trader.py:170-302`
- **Bug:** Bot copies whale entries but never generates exit signals when the whale sells their position.
- **Fix:** Detect whale position reductions/exits and generate corresponding SELL signals.

### H-11: Whale conviction uses cost basis, not current value
- **File:** `src/strategies/copy_trader.py:193`
- **Bug:** Whale "conviction" metric uses historical cost basis instead of current portfolio value. Stale data leads to wrong sizing.
- **Fix:** Use current position value (shares * current_price) for conviction calculation.

### H-12: Arb Scanner uses stale Gamma prices
- **File:** `src/strategies/arb_scanner.py:170-171`
- **Bug:** Opportunity detection uses Gamma API prices instead of live CLOB orderbook. Gamma prices can be stale/delayed.
- **Fix:** Fetch best bid/ask from CLOB orderbook for accurate opportunity detection.

### H-13: portfolio_value=0 silently disables risk checks
- **File:** `src/execution/risk_manager.py:62,85,101`
- **Bug:** Several risk checks use `portfolio_value` as denominator. When it's 0, checks are bypassed or produce division errors.
- **Fix:** Deny all trades when portfolio_value is 0 or unknown.

### H-14: No duplicate market check in RiskManager
- **File:** `src/execution/risk_manager.py`
- **Bug:** No check to prevent multiple strategies from opening positions on the same market simultaneously.
- **Fix:** Track active markets and reject duplicate entries.

### H-15: Kill switch not persisted
- **File:** `src/execution/risk_manager.py`
- **Bug:** Kill switch state is in-memory only. If bot restarts, kill switch is lost.
- **Fix:** Persist kill switch state to database, load on startup.

### H-16: Rate limiter backoff sleep inside lock
- **File:** `src/core/rate_limiter.py:64-87`
- **Bug:** Exponential backoff sleep is held inside the async lock. All other coroutines waiting for rate limiter are blocked.
- **Fix:** Release lock before sleeping, re-acquire after.

### H-17: Consecutive error counter reset is premature
- **File:** `src/core/rate_limiter.py`
- **Bug:** Error counter resets after first successful call, even if preceding failures should still be penalized.
- **Fix:** Only reset counter after N consecutive successes.

### H-18: `INSERT OR REPLACE` silently overwrites trade history
- **File:** `src/core/db.py:173-176`
- **Bug:** Using `INSERT OR REPLACE` means re-inserting a trade with the same ID overwrites the original record. Trade history can be lost.
- **Fix:** Use `INSERT OR IGNORE` or handle conflicts explicitly.

### H-19: No transaction boundaries in DB
- **File:** `src/core/db.py`
- **Bug:** Multi-step DB operations (e.g., update position + insert trade) are not wrapped in transactions. Partial writes on crash.
- **Fix:** Use explicit `BEGIN`/`COMMIT` transaction blocks for multi-step operations.

### H-20: WebSocket has no auth
- **File:** `src/core/websocket.py`
- **Bug:** WebSocket connection has no authentication. May not receive all required data.
- **Fix:** Add auth headers/params if required by Polymarket WS API.

### H-21: Stale WebSocket reference after disconnect
- **File:** `src/core/websocket.py`
- **Bug:** After disconnection, old WebSocket reference may still be used. No resubscription to channels after reconnect.
- **Fix:** Clear reference on disconnect, resubscribe on reconnect.

### H-22: No credential validation for live mode
- **File:** `src/core/config.py`
- **Bug:** Bot can start in live mode without valid API keys. Will fail at first trade.
- **Fix:** Validate credentials exist and are non-empty before allowing live mode.

### H-23: yaml.safe_load returns None for empty files
- **File:** `src/core/config.py`
- **Bug:** If YAML config file is empty, `yaml.safe_load()` returns `None`, causing attribute errors downstream.
- **Fix:** Default to empty dict when `safe_load` returns `None`.

### H-24: Strategy crash during start() kills entire bot
- **File:** `src/main.py`
- **Bug:** If any strategy's `start()` method throws, it propagates up and crashes the entire bot.
- **Fix:** Wrap each strategy start in try/except, log error, continue with remaining strategies.

### H-25: Health ready set before strategies actually start
- **File:** `src/main.py`
- **Bug:** Health check reports "ready" before strategies have finished initialization.
- **Fix:** Set ready flag after all strategies have started successfully.

---

## MEDIUM Issues (~25) — Fix for robustness

| ID | File | Issue |
|----|------|-------|
| M-01 | `config.py` | API keys not wrapped in SecretStr — leak risk in logs |
| M-02 | `config.py` | Strategy allocation percentages not validated (can exceed 100%) |
| M-03 | `db.py` | JSON column queried with SQL LIKE instead of proper JSON functions |
| M-04 | `main.py` | Cancel orders only runs in live mode (should be unconditional) |
| M-05 | `main.py` | No rate limiting in market resolution polling loop |
| M-06 | `pnl.py:70` | Uses `date.today()` (local time) not UTC — P&L date boundaries inconsistent |
| M-07 | `health_server.py:162` | Content-Length uses `len(string)` not `len(bytes)` — wrong for non-ASCII |
| M-08 | `telegram.py:80-89` | Only drains 10 messages on stop — remaining lost |
| M-09 | `telegram.py` | Kill command has no confirmation prompt |
| M-10 | `telegram.py` | No CancelledError handling in message loop |
| M-11 | `pnl.py` | Fees not included in P&L tracker calculations |
| M-12 | `monitoring/health.py` | Alert deduplication missing — same alert can fire repeatedly |
| M-13 | `strategies/base.py` | No dedup check on startup for positions already being monitored |
| M-14 | `websocket.py` | No heartbeat/ping to detect dead connections |
| M-15 | `position_manager.py` | P&L formula may be wrong due to size=USD vs shares ambiguity |
| M-16 | `stink_bidder.py` | No check for market resolution status before bidding |
| M-17 | `arb_scanner.py` | Signal size in shares, not USD (inconsistent with convention) |
| M-18 | `copy_trader.py` | Portfolio value calculation may use stale data |
| M-19 | `risk_manager.py` | No per-strategy position limit enforcement |
| M-20 | `order_manager.py` | No logging of order rejection reasons from CLOB |
| M-21 | `main.py` | Shutdown doesn't wait for all pending operations |
| M-22 | `db.py` | No WAL mode — concurrent reads blocked by writes |
| M-23 | `wallet.py` | No connection timeout for web3 RPC calls |
| M-24 | `config.py` | No validation of RPC URL format |
| M-25 | `stink_bidder.py` | Size units inconsistent with Signal convention |

---

## LOW Issues (~15) — Nice to have

| ID | File | Issue |
|----|------|-------|
| L-01 | Various | Missing type hints on several method returns |
| L-02 | `db.py` | No database migration strategy for schema changes |
| L-03 | `config.py` | No config hot-reload capability |
| L-04 | `health_server.py` | No request logging |
| L-05 | `telegram.py` | No rate limiting on outbound messages |
| L-06 | `pnl.py` | No rolling window metrics (only daily/total) |
| L-07 | `websocket.py` | No message queue size limit |
| L-08 | `main.py` | No startup banner with config summary |
| L-09 | `strategies/` | No strategy performance metrics collection |
| L-10 | `docker/` | No .dockerignore file |
| L-11 | Various | Some magic numbers not extracted to config |
| L-12 | `wallet.py` | No MATIC balance warning threshold |
| L-13 | `db.py` | No periodic VACUUM/optimize |
| L-14 | `tests/` | Some tests mock too aggressively — don't catch integration bugs |
| L-15 | `config.py` | No environment-specific config profiles |

---

## Fix Plan (16 steps, in priority order)

Execute using the **GSD framework**: Fix fast, fix right, move on.

### Step 1: Fix `client.py` — Core CLOB client (C-01, C-02, C-03, C-05)
- Pass `private_key=pk` to ClobClient constructor
- Change `create_order()` → `create_and_post_order()`
- Identify tokens by `outcome` field, not array index
- Wrap all sync CLOB methods in `asyncio.to_thread()`
- Build OrderArgs correctly with proper token_id

### Step 2: Fix `wallet.py` — USDC address (C-04, M-23)
- Replace USDC.e address with native USDC: `0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359`
- Add connection timeout for web3 RPC calls

### Step 3: Fix `order_manager.py` — Execution layer (C-06, C-10, H-02 to H-05)
- Standardize Signal.size to USD everywhere
- Convert USD → shares at execution time using current price
- Add risk check in `_execute_signal`
- Drain signal queue on kill switch
- Add retry logic for failed exits
- Bound queue size with `maxsize`

### Step 4: Fix `position_manager.py` — Position safety (C-07, C-08, H-06, H-07, M-15)
- Add "closing" intermediate state
- Add `_closing_positions` set guard against duplicate exits
- Include fees in P&L calculation
- Fix trailing stop direction for SELL positions
- Fix P&L formula for shares vs USD

### Step 5: Fix `risk_manager.py` — Risk safety (C-09, C-11, H-13 to H-15)
- Fail-closed: deny trade on balance check failure
- Include unrealized P&L in daily loss calculation
- Deny all trades when portfolio_value=0
- Add duplicate market check
- Persist kill switch to DB

### Step 6: Fix `rate_limiter.py` — Lock contention (H-16, H-17)
- Release lock before backoff sleep
- Only reset error counter after N consecutive successes

### Step 7: Fix `arb_scanner.py` — Arb safety (C-12, H-01, H-12, M-17)
- Add rollback logic for failed second leg
- Fix fee calculation formula
- Use CLOB orderbook prices instead of stale Gamma prices
- Standardize size to USD

### Step 8: Fix `stink_bidder.py` — Order tracking (H-08, H-09, M-16, M-25)
- Populate `_active_orders` after order placement
- Wrap sync CLOB call in `asyncio.to_thread()`
- Add market resolution check before bidding
- Standardize size to USD

### Step 9: Fix `copy_trader.py` — Exit signals (H-10, H-11, M-18)
- Generate SELL signals when whale reduces/exits position
- Use current value (not cost basis) for conviction
- Fix portfolio value calculation

### Step 10: Fix `config.py` — Validation (H-22, H-23, M-01, M-02, M-24)
- Validate credentials exist for live mode
- Handle `yaml.safe_load()` returning None
- Wrap API keys in SecretStr
- Validate allocation totals ≤ 100%
- Validate RPC URL format

### Step 11: Fix `db.py` — Data integrity (H-18, H-19, M-03, M-22)
- Change `INSERT OR REPLACE` → `INSERT OR IGNORE`
- Add transaction boundaries for multi-step operations
- Fix JSON column query
- Enable WAL mode

### Step 12: Fix `websocket.py` — Connection reliability (H-20, H-21, M-14)
- Add auth if required by Polymarket API
- Clear stale reference on disconnect
- Resubscribe on reconnect
- Add heartbeat/ping

### Step 13: Fix `main.py` — Orchestrator robustness (H-24, H-25, M-04, M-05, M-21)
- Wrap each strategy start in try/except
- Set ready flag after strategies start
- Make cancel-orders unconditional on shutdown
- Add rate limiting to resolution polling loop
- Wait for pending operations on shutdown

### Step 14: Fix monitoring — P&L and health (M-06, M-07, M-11, M-12)
- Use UTC for P&L date boundaries
- Fix Content-Length to use byte length
- Include fees in P&L tracker
- Add alert deduplication

### Step 15: Fix `telegram.py` — Message handling (M-08, M-09, M-10)
- Drain full queue on stop (not just 10)
- Add kill command confirmation
- Handle CancelledError in message loop

### Step 16: Run tests and verify
- Run full test suite
- Fix any broken tests
- Add new tests for critical fixes
- Verify bot starts cleanly

---

## Resume Instructions

To pick up where we left off:

```
We're building a Polymarket automated trading bot. A full codebase audit was completed
and documented in AUDIT.md. No fixes have been applied yet. The repo is at commit 6ea6eb3
on main. Read AUDIT.md for the complete bug list and 16-step fix plan, then start fixing
all critical and high-severity bugs using the GSD framework — fix fast, fix right, move on.
Start with Step 1 (src/core/client.py) and work through all 16 steps sequentially.
```

---

*Generated: 2026-02-14 | Audit of commit `6ea6eb3`*
