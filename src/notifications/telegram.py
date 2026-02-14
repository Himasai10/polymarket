"""Telegram notification system: alerts, commands, and daily summaries.

Addresses: TG-01 through TG-08

Architecture:
- TelegramNotifier: async message sender with rate limiting and formatting.
  Used by OrderManager, PositionManager, HealthChecker to push alerts.
- TelegramBot: command handler (/status, /pnl, /kill, /pause, /resume).
  Runs as an async polling loop alongside the trading bot.

Both gracefully degrade if Telegram credentials are not configured.
"""

from __future__ import annotations

import asyncio
import html
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable, Coroutine

import structlog
from telegram import Bot, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

if TYPE_CHECKING:
    from ..core.config import Settings

logger = structlog.get_logger()

# Telegram rate limit: ~30 messages/sec globally, 1 msg/sec per chat
_MSG_INTERVAL = 1.1  # seconds between sends to a single chat


class TelegramNotifier:
    """Sends Telegram alerts for trade events, risk warnings, and system status.

    Queues messages internally and sends them with rate limiting so we
    never hit Telegram API limits. Gracefully no-ops if not configured.
    """

    def __init__(self, settings: Settings) -> None:
        self._bot_token = settings.telegram_bot_token.get_secret_value()
        self._chat_id = settings.telegram_chat_id
        self._enabled = bool(self._bot_token and self._chat_id)
        self._bot: Bot | None = None
        self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=200)
        self._running = False

        if not self._enabled:
            logger.info("telegram_disabled", reason="missing bot_token or chat_id")

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    async def initialize(self) -> None:
        """Create the Bot instance and verify connectivity."""
        if not self._enabled:
            return

        self._bot = Bot(token=self._bot_token)
        try:
            me = await self._bot.get_me()
            logger.info("telegram_connected", bot_username=me.username)
        except Exception as e:
            logger.error("telegram_connect_failed", error=str(e))
            self._enabled = False

    async def start(self) -> None:
        """Start the background send loop."""
        if not self._enabled:
            return
        self._running = True
        await self._send_loop()

    async def stop(self) -> None:
        """M-08: Drain ALL remaining messages (not just 10) and stop."""
        self._running = False
        if self._bot and self._enabled:
            while not self._queue.empty():
                try:
                    msg = self._queue.get_nowait()
                    await self._send(msg)
                except asyncio.QueueEmpty:
                    break
                except Exception:
                    break

    # ── Alert methods (TG-01, TG-02, TG-08) ──────────────────────

    async def alert_position_opened(
        self,
        strategy: str,
        market_id: str,
        side: str,
        price: float,
        size: float,
        reasoning: str = "",
        market_question: str = "",
    ) -> None:
        """TG-01: Alert on new position."""
        text = (
            f"<b>New Position Opened</b>\n"
            f"Strategy: <code>{_esc(strategy)}</code>\n"
            f"Market: {_esc(market_question or market_id[:24])}\n"
            f"Side: <b>{_esc(side)}</b>\n"
            f"Price: ${price:.4f}\n"
            f"Size: ${size:.2f}\n"
        )
        if reasoning:
            text += f"Reason: {_esc(reasoning[:200])}\n"
        await self._enqueue(text)

    async def alert_position_closed(
        self,
        strategy: str,
        market_id: str,
        reason: str,
        pnl: float,
        pnl_pct: float,
        hold_duration_str: str = "",
        market_question: str = "",
    ) -> None:
        """TG-02: Alert on position close."""
        emoji = "+" if pnl >= 0 else ""
        text = (
            f"<b>Position Closed</b>\n"
            f"Strategy: <code>{_esc(strategy)}</code>\n"
            f"Market: {_esc(market_question or market_id[:24])}\n"
            f"Exit reason: <b>{_esc(reason)}</b>\n"
            f"P&L: <b>{emoji}${pnl:.2f}</b> ({pnl_pct:+.1f}%)\n"
        )
        if hold_duration_str:
            text += f"Held: {_esc(hold_duration_str)}\n"
        await self._enqueue(text)

    async def alert_daily_pnl(self, summary: str) -> None:
        """TG-03: Daily P&L summary."""
        text = f"<b>Daily P&L Summary</b>\n<pre>{_esc(summary)}</pre>"
        await self._enqueue(text)

    async def alert_system(self, title: str, message: str, level: str = "warning") -> None:
        """TG-08: System alert (connection issues, risk warnings, errors)."""
        prefix = {"warning": "Warning", "error": "ERROR", "info": "Info"}.get(level, "Alert")
        text = f"<b>{_esc(prefix)}: {_esc(title)}</b>\n{_esc(message)}"
        await self._enqueue(text)

    async def alert_risk_warning(self, check_name: str, detail: str) -> None:
        """TG-08: Risk limit warning."""
        text = (
            f"<b>Risk Warning</b>\nCheck: <code>{_esc(check_name)}</code>\nDetail: {_esc(detail)}"
        )
        await self._enqueue(text)

    async def alert_kill_switch(self, activated_by: str = "system") -> None:
        """TG-08: Kill switch activated."""
        text = (
            f"<b>KILL SWITCH ACTIVATED</b>\n"
            f"By: {_esc(activated_by)}\n"
            f"All new trades halted. Open orders cancelled."
        )
        await self._enqueue(text)

    # ── Internal ──────────────────────────────────────────────────

    async def _enqueue(self, text: str) -> None:
        """Add a message to the send queue. Drops if full."""
        if not self._enabled:
            return
        try:
            self._queue.put_nowait(text)
        except asyncio.QueueFull:
            logger.warning("telegram_queue_full", dropped_msg_preview=text[:60])

    async def _send_loop(self) -> None:
        """Background loop: dequeue and send messages with rate limiting.

        M-10: Handles CancelledError explicitly for clean shutdown.
        """
        while self._running:
            try:
                msg = await asyncio.wait_for(self._queue.get(), timeout=5.0)
                await self._send(msg)
                await asyncio.sleep(_MSG_INTERVAL)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                # M-10: On cancellation, drain remaining messages before exiting
                logger.debug("telegram_send_loop_cancelled")
                break
            except Exception:
                logger.exception("telegram_send_loop_error")
                await asyncio.sleep(5.0)

    async def _send(self, text: str) -> None:
        """Send a single HTML message to the configured chat."""
        if not self._bot or not self._chat_id:
            return
        try:
            await self._bot.send_message(
                chat_id=self._chat_id,
                text=text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except Exception as e:
            logger.error("telegram_send_failed", error=str(e), msg_preview=text[:60])


class TelegramCommandBot:
    """Handles Telegram bot commands: /status, /pnl, /kill, /pause, /resume.

    Addresses: TG-04 through TG-07.

    Uses python-telegram-bot's Application for long-polling.
    Needs references to bot internals to answer commands — these are
    injected via set_handlers() after the TradingBot is constructed.
    """

    def __init__(self, settings: Settings) -> None:
        self._bot_token = settings.telegram_bot_token.get_secret_value()
        self._chat_id = settings.telegram_chat_id
        self._enabled = bool(self._bot_token and self._chat_id)
        self._app: Application | None = None

        # Callbacks injected by TradingBot.  Each returns a string to send back.
        self._get_status: Callable[[], Coroutine[Any, Any, str]] | None = None
        self._get_pnl: Callable[[], str] | None = None
        self._do_kill: Callable[[], Coroutine[Any, Any, str]] | None = None
        self._do_pause: Callable[[str | None], str] | None = None
        self._do_resume: Callable[[str | None], str] | None = None

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    def set_handlers(
        self,
        *,
        get_status: Callable[[], Coroutine[Any, Any, str]],
        get_pnl: Callable[[], str],
        do_kill: Callable[[], Coroutine[Any, Any, str]],
        do_pause: Callable[[str | None], str],
        do_resume: Callable[[str | None], str],
    ) -> None:
        """Inject command handler callbacks from the TradingBot."""
        self._get_status = get_status
        self._get_pnl = get_pnl
        self._do_kill = do_kill
        self._do_pause = do_pause
        self._do_resume = do_resume

    async def start(self) -> None:
        """Build the Application and start polling for commands."""
        if not self._enabled:
            logger.info("telegram_commands_disabled")
            return

        self._app = Application.builder().token(self._bot_token).build()

        # Register command handlers
        self._app.add_handler(CommandHandler("status", self._cmd_status))
        self._app.add_handler(CommandHandler("pnl", self._cmd_pnl))
        self._app.add_handler(CommandHandler("kill", self._cmd_kill))
        self._app.add_handler(CommandHandler("pause", self._cmd_pause))
        self._app.add_handler(CommandHandler("resume", self._cmd_resume))
        self._app.add_handler(CommandHandler("help", self._cmd_help))

        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(drop_pending_updates=True)
        logger.info("telegram_command_bot_started")

    async def stop(self) -> None:
        """Stop the polling loop and shut down."""
        if self._app:
            try:
                await self._app.updater.stop()
                await self._app.stop()
                await self._app.shutdown()
            except Exception:
                logger.exception("telegram_command_bot_stop_error")
            logger.info("telegram_command_bot_stopped")

    # ── Auth helper ───────────────────────────────────────────────

    def _is_authorized(self, update: Update) -> bool:
        """Only allow commands from the configured chat_id."""
        if not update.effective_chat:
            return False
        return str(update.effective_chat.id) == self._chat_id

    # ── Command handlers ──────────────────────────────────────────

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """TG-04: /status — portfolio value, open positions, active strategies."""
        if not self._is_authorized(update):
            return
        if not self._get_status:
            await update.message.reply_text("Status handler not configured.")  # type: ignore[union-attr]
            return
        try:
            text = await self._get_status()
            await update.message.reply_text(text, parse_mode=ParseMode.HTML)  # type: ignore[union-attr]
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")  # type: ignore[union-attr]

    async def _cmd_pnl(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """TG-05: /pnl — today's P&L with per-strategy breakdown."""
        if not self._is_authorized(update):
            return
        if not self._get_pnl:
            await update.message.reply_text("P&L handler not configured.")  # type: ignore[union-attr]
            return
        try:
            text = self._get_pnl()
            await update.message.reply_text(  # type: ignore[union-attr]
                f"<pre>{_esc(text)}</pre>",
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")  # type: ignore[union-attr]

    async def _cmd_kill(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """TG-06: /kill — execute kill switch.

        M-09: Requires confirmation — /kill confirm — to prevent accidental activation.
        """
        if not self._is_authorized(update):
            return
        if not self._do_kill:
            await update.message.reply_text("Kill handler not configured.")  # type: ignore[union-attr]
            return

        # M-09: Require explicit confirmation
        args = context.args
        if not args or args[0].lower() != "confirm":
            await update.message.reply_text(  # type: ignore[union-attr]
                "<b>Kill Switch</b>\n\n"
                "This will cancel ALL open orders and halt ALL trading.\n\n"
                "To confirm, send: <code>/kill confirm</code>",
                parse_mode=ParseMode.HTML,
            )
            return

        try:
            text = await self._do_kill()
            await update.message.reply_text(text, parse_mode=ParseMode.HTML)  # type: ignore[union-attr]
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")  # type: ignore[union-attr]

    async def _cmd_pause(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """TG-07: /pause [strategy] — pause individual or all strategies."""
        if not self._is_authorized(update):
            return
        if not self._do_pause:
            await update.message.reply_text("Pause handler not configured.")  # type: ignore[union-attr]
            return
        try:
            # Optional argument: strategy name
            args = context.args
            strategy_name = args[0] if args else None
            text = self._do_pause(strategy_name)
            await update.message.reply_text(text, parse_mode=ParseMode.HTML)  # type: ignore[union-attr]
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")  # type: ignore[union-attr]

    async def _cmd_resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """TG-07: /resume [strategy] — resume individual or all strategies."""
        if not self._is_authorized(update):
            return
        if not self._do_resume:
            await update.message.reply_text("Resume handler not configured.")  # type: ignore[union-attr]
            return
        try:
            args = context.args
            strategy_name = args[0] if args else None
            text = self._do_resume(strategy_name)
            await update.message.reply_text(text, parse_mode=ParseMode.HTML)  # type: ignore[union-attr]
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")  # type: ignore[union-attr]

    async def _cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show available commands."""
        if not self._is_authorized(update):
            return
        text = (
            "<b>Polymarket Bot Commands</b>\n\n"
            "/status — Portfolio value, open positions, strategies\n"
            "/pnl — Today's P&L with per-strategy breakdown\n"
            "/kill confirm — Emergency stop: cancel all orders, halt trading\n"
            "/pause [strategy] — Pause one or all strategies\n"
            "/resume [strategy] — Resume one or all strategies\n"
            "/help — Show this message"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)  # type: ignore[union-attr]


def _esc(text: str) -> str:
    """Escape text for Telegram HTML parse mode."""
    return html.escape(str(text))
