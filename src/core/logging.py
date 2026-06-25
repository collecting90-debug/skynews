"""
src/core/logging.py
───────────────────
Structured logging configuration using loguru.
Outputs JSON in production, coloured text in development.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from src.core.config import Settings


def setup_logging(settings: "Settings") -> None:
    """
    Configure loguru for the entire application.
    Call once at startup before any other imports use the logger.
    """
    # Remove the default handler
    logger.remove()

    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)

    if settings.log_json:
        # ── Production: structured JSON ──────────────────────────────────────
        fmt = (
            '{{"time":"{time:YYYY-MM-DDTHH:mm:ss.SSSZ}",'
            '"level":"{level}",'
            '"name":"{name}",'
            '"message":"{message}",'
            '"env":"{extra[env]}"}}'
        )
        logger.configure(extra={"env": settings.env})

        # Console (stdout) — JSON
        logger.add(
            sys.stdout,
            format=fmt,
            level=settings.log_level,
            enqueue=True,       # thread-safe async logging
            backtrace=False,
            diagnose=False,
        )

        # Rotating file — JSON
        logger.add(
            log_dir / "wam_{time:YYYY-MM-DD}.log",
            format=fmt,
            level=settings.log_level,
            rotation="00:00",   # new file every midnight
            retention="30 days",
            compression="gz",
            enqueue=True,
            backtrace=True,
            diagnose=False,
        )

        # Error file — keeps only ERROR+ for quick triage
        logger.add(
            log_dir / "errors.log",
            format=fmt,
            level="ERROR",
            rotation="50 MB",
            retention="90 days",
            compression="gz",
            enqueue=True,
            backtrace=True,
            diagnose=True,
        )

    else:
        # ── Development: human-friendly coloured output ──────────────────────
        logger.add(
            sys.stderr,
            format=(
                "<green>{time:HH:mm:ss}</green> | "
                "<level>{level: <8}</level> | "
                "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> — "
                "<level>{message}</level>"
            ),
            level=settings.log_level,
            colorize=True,
            enqueue=True,
        )

    logger.info(
        "Logging initialised",
        level=settings.log_level,
        json_mode=settings.log_json,
        env=settings.env,
    )


def get_logger(name: str):
    """Return a contextual logger bound with the given name."""
    return logger.bind(name=name)
