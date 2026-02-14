"""Unit tests for Telegram notifications and commands (Phase 3).

Tests: TG-01 through TG-08
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.notifications.telegram import TelegramCommandBot, TelegramNotifier, _esc


class TestTelegramEsc:
    """Test HTML escaping utility."""

    def test_escape_basic_text(self):
        assert _esc("hello") == "hello"

    def test_escape_html_chars(self):
        assert _esc("<b>bold</b>") == "&lt;b&gt;bold&lt;/b&gt;"

    def test_escape_ampersand(self):
        assert _esc("A & B") == "A &amp; B"

    def test_escape_quotes(self):
        assert _esc('"quoted"') == "&quot;quoted&quot;"


class TestTelegramNotifier:
    """Test TelegramNotifier (TG-01, TG-02, TG-03, TG-08)."""

    @pytest.fixture
    def mock_settings(self):
        settings = MagicMock()
        settings.telegram_bot_token = MagicMock()
        settings.telegram_bot_token.get_secret_value.return_value = "test_token"
        settings.telegram_chat_id = "123456789"
        return settings

    @pytest.fixture
    def notifier(self, mock_settings):
        return TelegramNotifier(mock_settings)

    def test_not_enabled_when_missing_token(self):
        settings = MagicMock()
        settings.telegram_bot_token = MagicMock()
        settings.telegram_bot_token.get_secret_value.return_value = ""
        settings.telegram_chat_id = "123456789"
        notifier = TelegramNotifier(settings)
        assert not notifier.is_enabled

    def test_not_enabled_when_missing_chat_id(self, mock_settings):
        mock_settings.telegram_chat_id = ""
        notifier = TelegramNotifier(mock_settings)
        assert not notifier.is_enabled

    def test_is_enabled_when_configured(self, notifier):
        assert notifier.is_enabled

    @pytest.mark.asyncio
    async def test_initialize_creates_bot(self, notifier):
        with patch("src.notifications.telegram.Bot") as MockBot:
            mock_bot = AsyncMock()
            mock_bot.get_me.return_value = MagicMock(username="testbot")
            MockBot.return_value = mock_bot

            await notifier.initialize()

            MockBot.assert_called_once_with(token="test_token")
            mock_bot.get_me.assert_called_once()
            assert notifier._bot is not None

    @pytest.mark.asyncio
    async def test_initialize_handles_error(self, notifier):
        with patch("src.notifications.telegram.Bot") as MockBot:
            mock_bot = AsyncMock()
            mock_bot.get_me.side_effect = Exception("Connection failed")
            MockBot.return_value = mock_bot

            await notifier.initialize()

            assert not notifier.is_enabled

    @pytest.mark.asyncio
    async def test_send_loop_processes_queue(self, notifier):
        with patch("src.notifications.telegram.Bot") as MockBot:
            mock_bot = AsyncMock()
            MockBot.return_value = mock_bot
            notifier._bot = mock_bot

            # Add message to queue
            await notifier._enqueue("Test message")

            # Run send loop briefly
            notifier._running = True
            task = asyncio.create_task(notifier._send_loop())
            await asyncio.sleep(0.1)
            notifier._running = False
            await asyncio.sleep(0.1)
            task.cancel()

            # Verify send was called
            mock_bot.send_message.assert_called()

    @pytest.mark.asyncio
    async def test_alert_position_opened(self, notifier):
        with patch.object(notifier, "_enqueue") as mock_enqueue:
            await notifier.alert_position_opened(
                strategy="copy_trader",
                market_id="abc123",
                side="BUY",
                price=0.65,
                size=100.0,
                reasoning="High conviction",
                market_question="Will it rain?",
            )

            mock_enqueue.assert_called_once()
            text = mock_enqueue.call_args[0][0]
            assert "New Position Opened" in text
            assert "copy_trader" in text
            assert "BUY" in text
            assert "$0.6500" in text or "$0.65" in text
            assert "Will it rain?" in text

    @pytest.mark.asyncio
    async def test_alert_position_closed(self, notifier):
        with patch.object(notifier, "_enqueue") as mock_enqueue:
            await notifier.alert_position_closed(
                strategy="copy_trader",
                market_id="abc123",
                reason="take_profit",
                pnl=15.5,
                pnl_pct=15.5,
                hold_duration_str="2.5h",
                market_question="Will it rain?",
            )

            mock_enqueue.assert_called_once()
            text = mock_enqueue.call_args[0][0]
            assert "Position Closed" in text
            assert "take_profit" in text
            assert "+$15.50" in text
            assert "+15.5%" in text
            assert "2.5h" in text

    @pytest.mark.asyncio
    async def test_alert_position_closed_loss(self, notifier):
        with patch.object(notifier, "_enqueue") as mock_enqueue:
            await notifier.alert_position_closed(
                strategy="copy_trader",
                market_id="abc123",
                reason="stop_loss",
                pnl=-10.0,
                pnl_pct=-10.0,
            )

            mock_enqueue.assert_called_once()
            text = mock_enqueue.call_args[0][0]
            assert "$-10.00" in text
            assert "-10.0%" in text

    @pytest.mark.asyncio
    async def test_alert_daily_pnl(self, notifier):
        with patch.object(notifier, "_enqueue") as mock_enqueue:
            await notifier.alert_daily_pnl("Portfolio: $1000\nReturn: +5%")

            mock_enqueue.assert_called_once()
            text = mock_enqueue.call_args[0][0]
            assert "Daily P&L Summary" in text
            assert "Portfolio: $1000" in text

    @pytest.mark.asyncio
    async def test_alert_system(self, notifier):
        with patch.object(notifier, "_enqueue") as mock_enqueue:
            await notifier.alert_system(
                title="Connection Lost",
                message="WebSocket disconnected",
                level="error",
            )

            mock_enqueue.assert_called_once()
            text = mock_enqueue.call_args[0][0]
            assert "ERROR: Connection Lost" in text
            assert "WebSocket disconnected" in text

    @pytest.mark.asyncio
    async def test_alert_risk_warning(self, notifier):
        with patch.object(notifier, "_enqueue") as mock_enqueue:
            await notifier.alert_risk_warning(
                check_name="max_positions",
                detail="Maximum open positions exceeded",
            )

            mock_enqueue.assert_called_once()
            text = mock_enqueue.call_args[0][0]
            assert "Risk Warning" in text
            assert "max_positions" in text

    @pytest.mark.asyncio
    async def test_alert_kill_switch(self, notifier):
        with patch.object(notifier, "_enqueue") as mock_enqueue:
            await notifier.alert_kill_switch(activated_by="telegram")

            mock_enqueue.assert_called_once()
            text = mock_enqueue.call_args[0][0]
            assert "KILL SWITCH ACTIVATED" in text
            assert "telegram" in text


class TestTelegramCommandBot:
    """Test TelegramCommandBot (TG-04 through TG-07)."""

    @pytest.fixture
    def mock_settings(self):
        settings = MagicMock()
        settings.telegram_bot_token = MagicMock()
        settings.telegram_bot_token.get_secret_value.return_value = "test_token"
        settings.telegram_chat_id = "123456789"
        return settings

    @pytest.fixture
    def command_bot(self, mock_settings):
        return TelegramCommandBot(mock_settings)

    def test_not_enabled_when_missing_config(self):
        settings = MagicMock()
        settings.telegram_bot_token = MagicMock()
        settings.telegram_bot_token.get_secret_value.return_value = ""
        settings.telegram_chat_id = ""
        bot = TelegramCommandBot(settings)
        assert not bot.is_enabled

    def test_is_enabled_when_configured(self, command_bot):
        assert command_bot.is_enabled

    def test_set_handlers_stores_callbacks(self, command_bot):
        mock_status = AsyncMock(return_value="Status text")
        mock_pnl = MagicMock(return_value="PnL text")
        mock_kill = AsyncMock(return_value="Kill text")
        mock_pause = MagicMock(return_value="Pause text")
        mock_resume = MagicMock(return_value="Resume text")

        command_bot.set_handlers(
            get_status=mock_status,
            get_pnl=mock_pnl,
            do_kill=mock_kill,
            do_pause=mock_pause,
            do_resume=mock_resume,
        )

        assert command_bot._get_status is mock_status
        assert command_bot._get_pnl is mock_pnl
        assert command_bot._do_kill is mock_kill
        assert command_bot._do_pause is mock_pause
        assert command_bot._do_resume is mock_resume

    def test_is_authorized_allows_correct_chat(self, command_bot):
        mock_update = MagicMock()
        mock_update.effective_chat = MagicMock()
        mock_update.effective_chat.id = 123456789
        assert command_bot._is_authorized(mock_update)

    def test_is_authorized_rejects_wrong_chat(self, command_bot):
        mock_update = MagicMock()
        mock_update.effective_chat = MagicMock()
        mock_update.effective_chat.id = 999999999
        assert not command_bot._is_authorized(mock_update)

    @pytest.mark.asyncio
    async def test_cmd_status(self, command_bot):
        mock_status = AsyncMock(return_value="<b>Status</b>\nPortfolio: $1000")
        command_bot._get_status = mock_status

        mock_update = MagicMock()
        mock_update.effective_chat = MagicMock()
        mock_update.effective_chat.id = 123456789
        mock_message = AsyncMock()
        mock_update.message = mock_message

        await command_bot._cmd_status(mock_update, None)

        mock_status.assert_called_once()
        mock_message.reply_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_cmd_pnl(self, command_bot):
        mock_pnl = MagicMock(return_value="PnL Summary")
        command_bot._get_pnl = mock_pnl

        mock_update = MagicMock()
        mock_update.effective_chat = MagicMock()
        mock_update.effective_chat.id = 123456789
        mock_message = AsyncMock()
        mock_update.message = mock_message

        await command_bot._cmd_pnl(mock_update, None)

        mock_pnl.assert_called_once()
        mock_message.reply_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_cmd_kill(self, command_bot):
        mock_kill = AsyncMock(return_value="<b>KILL SWITCH ACTIVATED</b>")
        command_bot._do_kill = mock_kill

        mock_update = MagicMock()
        mock_update.effective_chat = MagicMock()
        mock_update.effective_chat.id = 123456789
        mock_message = AsyncMock()
        mock_update.message = mock_message

        mock_context = MagicMock()
        mock_context.args = ["confirm"]

        await command_bot._cmd_kill(mock_update, mock_context)

        mock_kill.assert_called_once()
        mock_message.reply_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_cmd_pause_with_strategy(self, command_bot):
        mock_pause = MagicMock(return_value="<b>Paused:</b> copy_trader")
        command_bot._do_pause = mock_pause

        mock_update = MagicMock()
        mock_update.effective_chat = MagicMock()
        mock_update.effective_chat.id = 123456789
        mock_message = AsyncMock()
        mock_update.message = mock_message

        mock_context = MagicMock()
        mock_context.args = ["copy_trader"]

        await command_bot._cmd_pause(mock_update, mock_context)

        mock_pause.assert_called_once_with("copy_trader")
        mock_message.reply_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_cmd_pause_all(self, command_bot):
        mock_pause = MagicMock(return_value="<b>Paused:</b> all")
        command_bot._do_pause = mock_pause

        mock_update = MagicMock()
        mock_update.effective_chat = MagicMock()
        mock_update.effective_chat.id = 123456789
        mock_message = AsyncMock()
        mock_update.message = mock_message

        mock_context = MagicMock()
        mock_context.args = []

        await command_bot._cmd_pause(mock_update, mock_context)

        mock_pause.assert_called_once_with(None)
        mock_message.reply_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_cmd_resume_with_strategy(self, command_bot):
        mock_resume = MagicMock(return_value="<b>Resumed:</b> copy_trader")
        command_bot._do_resume = mock_resume

        mock_update = MagicMock()
        mock_update.effective_chat = MagicMock()
        mock_update.effective_chat.id = 123456789
        mock_message = AsyncMock()
        mock_update.message = mock_message

        mock_context = MagicMock()
        mock_context.args = ["copy_trader"]

        await command_bot._cmd_resume(mock_update, mock_context)

        mock_resume.assert_called_once_with("copy_trader")
        mock_message.reply_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_cmd_help(self, command_bot):
        mock_update = MagicMock()
        mock_update.effective_chat = MagicMock()
        mock_update.effective_chat.id = 123456789
        mock_message = AsyncMock()
        mock_update.message = mock_message

        await command_bot._cmd_help(mock_update, None)

        mock_message.reply_text.assert_called_once()
        call_args = mock_message.reply_text.call_args[0][0]
        assert "/status" in call_args
        assert "/pnl" in call_args
        assert "/kill" in call_args
        assert "/pause" in call_args
        assert "/resume" in call_args

    @pytest.mark.asyncio
    async def test_unauthorized_chat_rejected(self, command_bot):
        mock_update = MagicMock()
        mock_update.effective_chat = MagicMock()
        mock_update.effective_chat.id = 999999999  # Wrong chat ID
        mock_message = AsyncMock()
        mock_update.message = mock_message

        await command_bot._cmd_status(mock_update, None)

        # Should not call reply_text for unauthorized chats
        mock_message.reply_text.assert_not_called()
