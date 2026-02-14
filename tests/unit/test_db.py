"""Unit tests for the database module."""

from __future__ import annotations

import pytest

from src.core.db import Database


class TestDatabase:
    """Tests for Database CRUD operations."""

    def test_initialize(self, db: Database):
        """DB initializes without error and tables exist."""
        # Should not raise
        count = db.count_open_positions()
        assert count == 0

    def test_record_trade(self, db: Database):
        """Can record a trade and retrieve it."""
        row_id = db.record_trade(
            order_id="order-123",
            strategy="copy_trading",
            market_id="market-abc",
            token_id="token-xyz",
            side="BUY",
            price=0.45,
            size=10.0,
            order_type="GTC",
            reasoning="test trade",
        )
        assert row_id > 0

        trades = db.get_trades(strategy="copy_trading")
        assert len(trades) == 1
        assert trades[0]["order_id"] == "order-123"
        assert trades[0]["price"] == 0.45
        assert trades[0]["size"] == 10.0

    def test_update_trade_status(self, db: Database):
        """Can update trade status after fill."""
        db.record_trade(
            order_id="order-456",
            strategy="arb",
            market_id="market-abc",
            token_id="token-xyz",
            side="BUY",
            price=0.50,
            size=20.0,
        )

        db.update_trade_status("order-456", "filled", fill_price=0.49, fill_size=20.0, fees=0.02)

        trades = db.get_trades(status="filled")
        assert len(trades) == 1
        assert trades[0]["status"] == "filled"

    def test_open_close_position(self, db: Database):
        """Can open and close a position."""
        pos_id = db.open_position(
            market_id="market-abc",
            token_id="token-xyz",
            strategy="copy_trading",
            side="BUY",
            entry_price=0.45,
            size=10.0,
            stop_loss_price=0.34,
        )
        assert pos_id > 0
        assert db.count_open_positions() == 1

        positions = db.get_open_positions()
        assert len(positions) == 1
        assert positions[0]["entry_price"] == 0.45

        db.close_position(pos_id, realized_pnl=1.50, close_reason="take_profit")
        assert db.count_open_positions() == 0

    def test_update_position_price(self, db: Database):
        """Price update computes unrealized PnL correctly."""
        pos_id = db.open_position(
            market_id="market-abc",
            token_id="token-xyz",
            strategy="copy_trading",
            side="BUY",
            entry_price=0.40,
            size=10.0,
        )

        db.update_position_price(pos_id, current_price=0.50)

        positions = db.get_open_positions()
        assert len(positions) == 1
        assert positions[0]["current_price"] == 0.50
        # Unrealized PnL = (0.50 - 0.40) * 10 = 1.0
        assert abs(positions[0]["unrealized_pnl"] - 1.0) < 0.01

    def test_get_open_positions_by_strategy(self, db: Database):
        """Filtering positions by strategy works."""
        db.open_position("m1", "t1", "copy_trading", "BUY", 0.45, 10.0)
        db.open_position("m2", "t2", "arbitrage", "BUY", 0.30, 5.0)

        copy = db.get_open_positions(strategy="copy_trading")
        assert len(copy) == 1
        assert copy[0]["strategy"] == "copy_trading"

        all_positions = db.get_open_positions()
        assert len(all_positions) == 2

    def test_strategy_state_persistence(self, db: Database):
        """Can save and load strategy state."""
        state = {"last_scan": "2026-02-13", "tracked_markets": ["m1", "m2"]}
        db.save_strategy_state("copy_trading", state)

        loaded = db.load_strategy_state("copy_trading")
        assert loaded is not None
        assert loaded["last_scan"] == "2026-02-13"
        assert len(loaded["tracked_markets"]) == 2

    def test_load_missing_strategy_state(self, db: Database):
        """Loading non-existent state returns None."""
        result = db.load_strategy_state("nonexistent")
        assert result is None

    def test_daily_pnl(self, db: Database):
        """Can record and retrieve daily PnL."""
        db.record_daily_pnl("2026-02-13", starting_balance=500.0)

        daily = db.get_daily_pnl("2026-02-13")
        assert daily is not None
        assert daily["starting_balance"] == 500.0

        # Missing day returns None
        assert db.get_daily_pnl("2026-02-14") is None

    def test_whale_positions(self, db: Database):
        """Can upsert and retrieve whale positions."""
        db.upsert_whale_position(
            wallet_address="0xwhale1",
            market_id="market-abc",
            token_id="token-xyz",
            size=1000.0,
            avg_price=0.55,
        )

        positions = db.get_whale_positions("0xwhale1")
        assert len(positions) == 1
        assert positions[0]["size"] == 1000.0

        # Upsert updates existing
        db.upsert_whale_position(
            wallet_address="0xwhale1",
            market_id="market-abc",
            token_id="token-xyz",
            size=2000.0,
            avg_price=0.60,
        )
        positions = db.get_whale_positions("0xwhale1")
        assert len(positions) == 1
        assert positions[0]["size"] == 2000.0

    def test_get_today_realized_pnl(self, db: Database):
        """Today's realized PnL starts at 0."""
        assert db.get_today_realized_pnl() == 0.0
