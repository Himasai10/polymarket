"""
SQLite database for persisting trades, positions, and strategy state.

Addresses: CORE-08

Audit fixes applied:
- H-18: INSERT OR IGNORE for trades (prevent overwriting history)
- H-19: Transaction context manager for multi-step operations
- M-03: Proper JSON extraction instead of LIKE for metadata queries
- M-22: WAL mode + busy timeout for concurrent access
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

import structlog

from .config import Settings

logger = structlog.get_logger()


def _utcnow() -> str:
    """UTC timestamp string."""
    return datetime.now(UTC).isoformat()


class Database:
    """SQLite database manager for trade history, positions, and state.

    Uses WAL mode for better concurrent read/write performance.
    """

    def __init__(self, settings: Settings):
        self.db_path = settings.db_path
        self._conn: sqlite3.Connection | None = None
        self._in_transaction: bool = False

    def initialize(self) -> None:
        """Create database and tables."""
        # Ensure directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        # M-22 FIX: WAL mode for concurrent reads + busy timeout to avoid lock errors
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")  # Wait up to 5s for lock
        self._conn.execute("PRAGMA synchronous=NORMAL")  # Good balance for WAL mode

        self._create_tables()
        logger.info("database_initialized", path=str(self.db_path))

    def _create_tables(self) -> None:
        """Create all required tables."""
        assert self._conn is not None

        self._conn.executescript("""
            -- Trade history: every order placed
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT UNIQUE,
                strategy TEXT NOT NULL,
                market_id TEXT NOT NULL,
                token_id TEXT NOT NULL,
                side TEXT NOT NULL,
                price REAL NOT NULL,
                size REAL NOT NULL,
                order_type TEXT NOT NULL DEFAULT 'GTC',
                status TEXT NOT NULL DEFAULT 'pending',
                reasoning TEXT,
                fees REAL DEFAULT 0,
                fill_price REAL,
                fill_size REAL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                metadata TEXT
            );

            -- Open positions
            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_id TEXT NOT NULL,
                token_id TEXT NOT NULL,
                strategy TEXT NOT NULL,
                side TEXT NOT NULL,
                entry_price REAL NOT NULL,
                size REAL NOT NULL,
                current_price REAL,
                unrealized_pnl REAL DEFAULT 0,
                realized_pnl REAL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'open',
                stop_loss_price REAL,
                take_profit_triggered INTEGER DEFAULT 0,
                trailing_stop_price REAL,
                opened_at TEXT NOT NULL,
                closed_at TEXT,
                close_reason TEXT,
                metadata TEXT
            );

            -- Daily P&L snapshots
            CREATE TABLE IF NOT EXISTS daily_pnl (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL UNIQUE,
                starting_balance REAL NOT NULL,
                ending_balance REAL,
                realized_pnl REAL DEFAULT 0,
                unrealized_pnl REAL DEFAULT 0,
                trades_count INTEGER DEFAULT 0,
                wins INTEGER DEFAULT 0,
                losses INTEGER DEFAULT 0,
                fees_paid REAL DEFAULT 0,
                metadata TEXT
            );

            -- Strategy state (for persistence across restarts)
            CREATE TABLE IF NOT EXISTS strategy_state (
                strategy TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            -- Copy trading: tracked wallet positions (for diff detection)
            CREATE TABLE IF NOT EXISTS whale_positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wallet_address TEXT NOT NULL,
                market_id TEXT NOT NULL,
                token_id TEXT NOT NULL,
                size REAL NOT NULL,
                avg_price REAL,
                last_seen_at TEXT NOT NULL,
                UNIQUE(wallet_address, market_id, token_id)
            );

            -- Key-value metadata store (H-15: persist kill switch, etc.)
            CREATE TABLE IF NOT EXISTS bot_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            -- Indexes for common queries
            CREATE INDEX IF NOT EXISTS idx_trades_strategy ON trades(strategy);
            CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
            CREATE INDEX IF NOT EXISTS idx_trades_market ON trades(market_id);
            CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status);
            CREATE INDEX IF NOT EXISTS idx_positions_strategy ON positions(strategy);
            CREATE INDEX IF NOT EXISTS idx_whale_wallet ON whale_positions(wallet_address);
        """)
        self._conn.commit()

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Database not initialized. Call initialize() first.")
        return self._conn

    def close(self) -> None:
        """Close database connection."""
        if self._conn:
            self._conn.close()
            logger.info("database_closed")

    # H-19 FIX: Transaction context manager for multi-step operations
    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection, None, None]:
        """Wrap multiple DB operations in an explicit transaction.

        Usage:
            with db.transaction() as conn:
                db.record_trade(...)
                db.open_position(...)
            # COMMIT on success, ROLLBACK on exception

        Individual methods skip their own commit() when inside a transaction.
        """
        conn = self.conn
        conn.execute("BEGIN IMMEDIATE")
        self._in_transaction = True
        try:
            yield conn
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            self._in_transaction = False

    def _commit(self) -> None:
        """Commit unless we're inside an explicit transaction (H-19)."""
        if not self._in_transaction:
            self.conn.commit()

    # ─── Trade Operations ─────────────────────────────────────────

    def record_trade(
        self,
        order_id: str,
        strategy: str,
        market_id: str,
        token_id: str,
        side: str,
        price: float,
        size: float,
        order_type: str = "GTC",
        reasoning: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> int:
        """Record a new trade.

        H-18 FIX: Uses INSERT OR IGNORE to prevent overwriting existing trades.
        If a trade with the same order_id already exists, returns the existing row ID.
        """
        now = _utcnow()
        cursor = self.conn.execute(
            """INSERT OR IGNORE INTO trades
               (order_id, strategy, market_id, token_id, side, price, size,
                order_type, status, reasoning, created_at, updated_at, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'submitted', ?, ?, ?, ?)""",
            (
                order_id,
                strategy,
                market_id,
                token_id,
                side,
                price,
                size,
                order_type,
                reasoning,
                now,
                now,
                json.dumps(metadata) if metadata else None,
            ),
        )
        self._commit()

        if cursor.rowcount == 0:
            # Trade already existed — return existing row ID
            row = self.conn.execute(
                "SELECT id FROM trades WHERE order_id = ?", (order_id,)
            ).fetchone()
            existing_id = row["id"] if row else -1
            logger.warning(
                "trade_already_exists",
                order_id=order_id,
                existing_id=existing_id,
            )
            return existing_id

        logger.info(
            "trade_recorded",
            trade_id=cursor.lastrowid,
            order_id=order_id,
            strategy=strategy,
            side=side,
        )
        return cursor.lastrowid  # type: ignore

    def update_trade_status(self, order_id: str, status: str, **kwargs: Any) -> None:
        """Update trade status (filled, cancelled, etc.)."""
        sets = ["status = ?", "updated_at = ?"]
        vals: list[Any] = [status, _utcnow()]

        for key in ("fill_price", "fill_size", "fees"):
            if key in kwargs:
                sets.append(f"{key} = ?")
                vals.append(kwargs[key])

        vals.append(order_id)
        self.conn.execute(
            f"UPDATE trades SET {', '.join(sets)} WHERE order_id = ?",
            vals,
        )
        self._commit()

    def get_trades(
        self,
        strategy: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Query trade history."""
        query = "SELECT * FROM trades WHERE 1=1"
        params: list[Any] = []

        if strategy:
            query += " AND strategy = ?"
            params.append(strategy)
        if status:
            query += " AND status = ?"
            params.append(status)

        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        rows = self.conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    # ─── Position Operations ──────────────────────────────────────

    def open_position(
        self,
        market_id: str,
        token_id: str,
        strategy: str,
        side: str,
        entry_price: float,
        size: float,
        stop_loss_price: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        """Record a new open position."""
        now = _utcnow()
        cursor = self.conn.execute(
            """INSERT INTO positions
               (market_id, token_id, strategy, side, entry_price, size,
                current_price, status, stop_loss_price, opened_at, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?)""",
            (
                market_id,
                token_id,
                strategy,
                side,
                entry_price,
                size,
                entry_price,
                stop_loss_price,
                now,
                json.dumps(metadata) if metadata else None,
            ),
        )
        self._commit()
        return cursor.lastrowid  # type: ignore

    def set_position_closing(self, position_id: int, close_reason: str) -> None:
        """Mark a position as 'closing' (exit order submitted, awaiting fill).

        C-07: Intermediate state prevents the position from being treated as
        fully closed before the exit order actually fills.
        """
        self.conn.execute(
            "UPDATE positions SET status = 'closing', close_reason = ?"
            " WHERE id = ? AND status = 'open'",
            (close_reason, position_id),
        )
        self._commit()

    def close_position(
        self,
        position_id: int,
        realized_pnl: float,
        close_reason: str,
    ) -> None:
        """Close a position (finalize after fill confirmation)."""
        now = _utcnow()
        self.conn.execute(
            """UPDATE positions
               SET status = 'closed', realized_pnl = ?, close_reason = ?, closed_at = ?
               WHERE id = ? AND status IN ('open', 'closing')""",
            (realized_pnl, close_reason, now, position_id),
        )
        self._commit()

    def update_position_price(self, position_id: int, current_price: float) -> None:
        """Update current price and unrealized P&L for a position."""
        row = self.conn.execute(
            "SELECT entry_price, size, side FROM positions WHERE id = ?",
            (position_id,),
        ).fetchone()

        if not row:
            return

        entry_price = row["entry_price"]
        size = row["size"]
        side = row["side"]

        if side == "BUY":
            unrealized_pnl = (current_price - entry_price) * size
        else:
            unrealized_pnl = (entry_price - current_price) * size

        self.conn.execute(
            "UPDATE positions SET current_price = ?, unrealized_pnl = ? WHERE id = ?",
            (current_price, unrealized_pnl, position_id),
        )
        self._commit()

    def update_position_trailing_stop(self, position_id: int, trailing_stop_price: float) -> None:
        """Update the trailing stop price for a position."""
        self.conn.execute(
            "UPDATE positions SET trailing_stop_price = ? WHERE id = ?",
            (trailing_stop_price, position_id),
        )
        self._commit()

    def update_position_partial_close(
        self, position_id: int, remaining_size: float, take_profit_triggered: int
    ) -> None:
        """Update position after a partial close (TP tier triggered)."""
        self.conn.execute(
            "UPDATE positions SET size = ?, take_profit_triggered = ? WHERE id = ?",
            (remaining_size, take_profit_triggered, position_id),
        )
        self._commit()

    def get_open_positions(self, strategy: str | None = None) -> list[dict[str, Any]]:
        """Get all open or closing positions (both need price monitoring)."""
        query = "SELECT * FROM positions WHERE status IN ('open', 'closing')"
        params: list[Any] = []

        if strategy:
            query += " AND strategy = ?"
            params.append(strategy)

        query += " ORDER BY opened_at DESC"
        rows = self.conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def count_open_positions(self) -> int:
        """Count open/closing positions (for risk limit checks)."""
        row = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM positions WHERE status IN ('open', 'closing')"
        ).fetchone()
        return row["cnt"] if row else 0

    # ─── Daily P&L ────────────────────────────────────────────────

    def record_daily_pnl(self, date: str, starting_balance: float) -> None:
        """Initialize or update daily P&L record."""
        self.conn.execute(
            """INSERT OR IGNORE INTO daily_pnl (date, starting_balance)
               VALUES (?, ?)""",
            (date, starting_balance),
        )
        self._commit()

    def get_daily_pnl(self, date: str) -> dict[str, Any] | None:
        """Get P&L for a specific date."""
        row = self.conn.execute("SELECT * FROM daily_pnl WHERE date = ?", (date,)).fetchone()
        return dict(row) if row else None

    def get_today_realized_pnl(self) -> float:
        """Get today's realized P&L."""
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        row = self.conn.execute(
            """SELECT COALESCE(SUM(realized_pnl), 0) as total
               FROM positions WHERE status = 'closed'
               AND closed_at >= ?""",
            (today,),
        ).fetchone()
        return float(row["total"]) if row else 0.0

    # ─── Strategy State ───────────────────────────────────────────

    def save_strategy_state(self, strategy: str, state: dict[str, Any]) -> None:
        """Save strategy state for persistence across restarts."""
        self.conn.execute(
            """INSERT OR REPLACE INTO strategy_state (strategy, state, updated_at)
               VALUES (?, ?, ?)""",
            (strategy, json.dumps(state), _utcnow()),
        )
        self._commit()

    def load_strategy_state(self, strategy: str) -> dict[str, Any] | None:
        """Load strategy state from last run."""
        row = self.conn.execute(
            "SELECT state FROM strategy_state WHERE strategy = ?", (strategy,)
        ).fetchone()
        return json.loads(row["state"]) if row else None

    # ─── Whale Positions (Copy Trading) ───────────────────────────

    def upsert_whale_position(
        self,
        wallet_address: str,
        market_id: str,
        token_id: str,
        size: float,
        avg_price: float | None = None,
    ) -> None:
        """Update or insert a whale's position."""
        self.conn.execute(
            """INSERT OR REPLACE INTO whale_positions
               (wallet_address, market_id, token_id, size, avg_price, last_seen_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (wallet_address, market_id, token_id, size, avg_price, _utcnow()),
        )
        self._commit()

    def get_whale_positions(self, wallet_address: str) -> list[dict[str, Any]]:
        """Get stored positions for a whale wallet."""
        rows = self.conn.execute(
            "SELECT * FROM whale_positions WHERE wallet_address = ?",
            (wallet_address,),
        ).fetchall()
        return [dict(row) for row in rows]

    def delete_whale_position(self, wallet_address: str, market_id: str, token_id: str) -> None:
        """Delete a whale position (when whale exits)."""
        self.conn.execute(
            """DELETE FROM whale_positions
               WHERE wallet_address = ? AND market_id = ? AND token_id = ?""",
            (wallet_address, market_id, token_id),
        )
        self._commit()

    def get_all_whale_positions(self) -> list[dict[str, Any]]:
        """Get all stored whale positions across all wallets."""
        rows = self.conn.execute("SELECT * FROM whale_positions").fetchall()
        return [dict(row) for row in rows]

    # ─── Whale Copy Performance ───────────────────────────────────

    def get_positions_by_wallet_source(self, wallet_address: str) -> list[dict[str, Any]]:
        """Get all positions opened due to copying a specific wallet.

        M-03 FIX: Uses json_extract() instead of LIKE for reliable JSON queries.
        """
        rows = self.conn.execute(
            """SELECT * FROM positions
               WHERE json_extract(metadata, '$.source_wallet') = ?
               ORDER BY opened_at DESC""",
            (wallet_address,),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_closed_positions(
        self, strategy: str | None = None, limit: int = 500
    ) -> list[dict[str, Any]]:
        """Get closed positions, optionally filtered by strategy."""
        query = "SELECT * FROM positions WHERE status = 'closed'"
        params: list[Any] = []

        if strategy:
            query += " AND strategy = ?"
            params.append(strategy)

        query += " ORDER BY closed_at DESC LIMIT ?"
        params.append(limit)

        rows = self.conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def update_daily_pnl_end_of_day(
        self,
        date_str: str,
        ending_balance: float,
        realized_pnl: float,
        unrealized_pnl: float,
        trades_count: int,
        wins: int,
        losses: int,
        fees_paid: float,
    ) -> None:
        """Finalize end-of-day P&L record."""
        self.conn.execute(
            """UPDATE daily_pnl
               SET ending_balance = ?, realized_pnl = ?, unrealized_pnl = ?,
                   trades_count = ?, wins = ?, losses = ?, fees_paid = ?
               WHERE date = ?""",
            (
                ending_balance,
                realized_pnl,
                unrealized_pnl,
                trades_count,
                wins,
                losses,
                fees_paid,
                date_str,
            ),
        )
        self._commit()

    # ─── Bot Metadata (key-value store, H-15) ─────────────────────

    def set_metadata(self, key: str, value: str) -> None:
        """Upsert a metadata key-value pair."""
        now = _utcnow()
        self.conn.execute(
            """INSERT INTO bot_metadata (key, value, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value,
               updated_at = excluded.updated_at""",
            (key, value, now),
        )
        self._commit()

    def get_metadata(self, key: str) -> str | None:
        """Get a metadata value by key, or None if not found."""
        row = self.conn.execute(
            "SELECT value FROM bot_metadata WHERE key = ?",
            (key,),
        ).fetchone()
        return row["value"] if row else None
