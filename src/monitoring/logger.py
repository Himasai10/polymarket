"""Structured JSON logging setup using structlog.

CORE-09: Every trade logged with strategy, market, reasoning, price, size, fees.
Provides consistent, queryable JSON logs across all modules.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

import structlog


def setup_logging(
    log_level: str = "INFO",
    json_output: bool = True,
    log_file: str | None = None,
    max_bytes: int = 10 * 1024 * 1024,  # 10 MB per file
    backup_count: int = 5,
) -> None:
    """Configure structlog for the entire application.

    Args:
        log_level: Minimum log level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        json_output: If True, output JSON lines. If False, pretty console output.
        log_file: Optional path to a log file. If set, logs are also written to file.
        max_bytes: Max size per log file before rotation (default 10 MB).
        backup_count: Number of rotated log files to keep (default 5).
    """
    # Shared processors for all output
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    if json_output:
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Configure the stdlib root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # Formatter that structlog will use
    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # Optional file handler with rotation (CORE-09 production)
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        file_json_formatter = structlog.stdlib.ProcessorFormatter(
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.JSONRenderer(),
            ],
        )
        file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            encoding="utf-8",
            maxBytes=max_bytes,
            backupCount=backup_count,
        )
        file_handler.setFormatter(file_json_formatter)
        root_logger.addHandler(file_handler)

    # Suppress noisy third-party loggers
    for noisy_logger in ("websockets", "httpx", "httpcore", "web3", "urllib3"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Get a configured structlog logger.

    Args:
        name: Logger name. If None, uses the caller's module name.

    Returns:
        A bound structlog logger instance.
    """
    return structlog.get_logger(name)


def log_trade(
    logger: structlog.stdlib.BoundLogger,
    *,
    event: str,
    strategy: str,
    market_id: str,
    token_id: str,
    side: str,
    price: float,
    size: float,
    order_type: str = "GTC",
    reasoning: str = "",
    fees: float = 0.0,
    order_id: str = "",
    fill_price: float | None = None,
    fill_size: float | None = None,
    **extra: object,
) -> None:
    """Log a trade event with all required fields (CORE-09).

    Ensures consistent trade logging across all strategies and modules.
    """
    logger.info(
        event,
        strategy=strategy,
        market_id=market_id,
        token_id=token_id,
        side=side,
        price=price,
        size=size,
        order_type=order_type,
        reasoning=reasoning,
        fees=fees,
        order_id=order_id,
        fill_price=fill_price,
        fill_size=fill_size,
        **extra,
    )


def log_risk_rejection(
    logger: structlog.stdlib.BoundLogger,
    *,
    strategy: str,
    market_id: str,
    reason: str,
    signal_price: float,
    signal_size: float,
) -> None:
    """Log when the risk manager rejects a signal."""
    logger.warning(
        "signal_rejected",
        strategy=strategy,
        market_id=market_id,
        reason=reason,
        signal_price=signal_price,
        signal_size=signal_size,
    )


def log_position_event(
    logger: structlog.stdlib.BoundLogger,
    *,
    event: str,
    strategy: str,
    market_id: str,
    token_id: str,
    entry_price: float,
    current_price: float,
    pnl_pct: float,
    reason: str = "",
    **extra: object,
) -> None:
    """Log position lifecycle events (open, close, TP, SL, trailing)."""
    logger.info(
        event,
        strategy=strategy,
        market_id=market_id,
        token_id=token_id,
        entry_price=entry_price,
        current_price=current_price,
        pnl_pct=round(pnl_pct, 4),
        reason=reason,
        **extra,
    )
