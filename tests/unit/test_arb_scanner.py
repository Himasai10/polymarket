"""Unit tests for ArbScanner strategy (Phase 4)."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.core.client import Market
from src.core.config import StrategyConfig
from src.core.db import Database
from src.execution.order_manager import OrderManager, Signal
from src.execution.risk_manager import RiskManager
from src.strategies.arb_scanner import ArbScanner


@pytest.fixture
def mock_deps():
    client = MagicMock()
    client.get_markets = AsyncMock()
    client.get_best_bid_ask = AsyncMock(return_value=(None, None))

    db = MagicMock(spec=Database)
    db.load_strategy_state.return_value = {}

    order_manager = MagicMock(spec=OrderManager)

    risk_manager = MagicMock(spec=RiskManager)

    strategy_config = MagicMock(spec=StrategyConfig)
    strategy_config.get_strategy.return_value = {
        "min_gap_threshold": 0.95,
        "scan_interval_sec": 60,
        "allocation_pct": 10.0,
        "order_type": "FOK",
    }
    strategy_config.is_strategy_enabled.return_value = True
    strategy_config.min_position_size_usd = 25.0
    strategy_config.winner_fee_pct = 2.0
    strategy_config.max_taker_fee_pct = 3.15
    strategy_config.estimated_gas_usd = 0.03

    notifier = MagicMock()
    notifier.alert_system = AsyncMock()

    return {
        "client": client,
        "db": db,
        "order_manager": order_manager,
        "risk_manager": risk_manager,
        "strategy_config": strategy_config,
        "notifier": notifier,
    }


@pytest.fixture
def scanner(mock_deps):
    return ArbScanner(
        client=mock_deps["client"],
        db=mock_deps["db"],
        order_manager=mock_deps["order_manager"],
        risk_manager=mock_deps["risk_manager"],
        strategy_config=mock_deps["strategy_config"],
        notifier=mock_deps["notifier"],
    )


class TestArbScanner:
    @pytest.mark.asyncio
    async def test_initialization(self, scanner, mock_deps):
        # Simulate state being loaded by BaseStrategy.start()
        scanner._state = {"total_opportunities": 5, "total_executed": 2}

        await scanner.initialize()

        assert scanner._total_opportunities == 5
        assert scanner._total_executed == 2
        assert scanner._min_gap_threshold == 0.95
        assert scanner._eval_interval == 60

    @pytest.mark.asyncio
    async def test_evaluate_no_markets(self, scanner, mock_deps):
        mock_deps["client"].get_markets.return_value = []

        signals = await scanner.evaluate()

        assert len(signals) == 0
        mock_deps["client"].get_markets.assert_called_once()

    @pytest.mark.asyncio
    async def test_evaluate_no_opportunity(self, scanner, mock_deps):
        # Yes + No = 1.0 (no arb)
        market = Market(
            condition_id="123",
            question="Test?",
            slug="test",
            yes_token_id="y1",
            no_token_id="n1",
            yes_price=0.50,
            no_price=0.50,
            volume=1000,
            liquidity=1000,
            end_date="2024-12-31",
            active=True,
        )
        mock_deps["client"].get_markets.return_value = [market]

        signals = await scanner.evaluate()

        assert len(signals) == 0
        assert scanner._total_opportunities == 0

    @pytest.mark.asyncio
    async def test_evaluate_opportunity_too_small_profit(self, scanner, mock_deps):
        # Yes + No = 0.94 (Gap = 0.06)
        # H-01 corrected fee math:
        #   per_unit_cost = total_price * (1 + taker_fee_rate)
        #   per_unit_payout = 1.0 * (1 - winner_fee_rate)
        #   per_unit_profit = payout - cost
        # With taker_fee_rate=5%: 0.94 * 1.05 = 0.987, payout = 0.98 → -0.007 (unprofitable)
        scanner._taker_fee_rate = 0.05  # Override to 5% to make gap unprofitable

        market = Market(
            condition_id="123",
            question="Test?",
            slug="test",
            yes_token_id="y1",
            no_token_id="n1",
            yes_price=0.47,
            no_price=0.47,  # Sum = 0.94
            volume=1000,
            liquidity=1000,
            end_date="2024-12-31",
            active=True,
        )
        mock_deps["client"].get_markets.return_value = [market]

        signals = await scanner.evaluate()

        assert len(signals) == 0
        # It detects the "opportunity" (gap < threshold) but marks it not executable
        assert scanner._total_opportunities == 1
        assert scanner._opportunities_log[0].executable is False
        assert "negative_profit" in scanner._opportunities_log[0].reason_skipped

    @pytest.mark.asyncio
    async def test_evaluate_executable_opportunity(self, scanner, mock_deps):
        # Yes + No = 0.80 (Gap = 0.20) -> Profitable
        market = Market(
            condition_id="123",
            question="Test?",
            slug="test",
            yes_token_id="y1",
            no_token_id="n1",
            yes_price=0.40,
            no_price=0.40,  # Sum = 0.80
            volume=1000,
            liquidity=1000,
            end_date="2024-12-31",
            active=True,
        )
        mock_deps["client"].get_markets.return_value = [market]

        signals = await scanner.evaluate()

        assert len(signals) == 2  # One for Yes, one for No
        assert scanner._total_executed == 1

        yes_sig = next(s for s in signals if s.token_id == "y1")
        no_sig = next(s for s in signals if s.token_id == "n1")

        assert yes_sig.side == "BUY"
        assert yes_sig.order_type == "FOK"
        assert yes_sig.metadata["arb_opportunity"] is True

        assert no_sig.side == "BUY"
        assert no_sig.order_type == "FOK"

        # Verify notification sent
        mock_deps["notifier"].alert_system.assert_called_once()

    @pytest.mark.asyncio
    async def test_logging_opportunities(self, scanner, mock_deps):
        # Trigger an opportunity
        market = Market(
            condition_id="123",
            question="Test?",
            slug="test",
            yes_token_id="y1",
            no_token_id="n1",
            yes_price=0.40,
            no_price=0.40,
            volume=1000,
            liquidity=1000,
            end_date="2024-12-31",
            active=True,
        )
        mock_deps["client"].get_markets.return_value = [market]

        await scanner.evaluate()

        assert len(scanner._opportunities_log) == 1
        log_entry = scanner._opportunities_log[0]
        assert log_entry.gap == pytest.approx(0.20)
        assert log_entry.executable is True

    def test_get_max_arb_size_usd(self, scanner, mock_deps):
        # Should return max(min_pos * 2, 200)
        # min_pos is 25, so max(50, 200) = 200
        assert scanner._get_max_arb_size_usd() == 200.0
