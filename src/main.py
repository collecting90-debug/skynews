"""
src/main.py
────────────
Application entry point.

Initialises logging, optional Sentry, then hands off to the Watchdog
which manages the ArticlePipeline lifecycle indefinitely.

Run::

    python -m src.main
"""

from __future__ import annotations

import asyncio
import sys

from src.core.config import get_settings
from src.core.logging import setup_logging
from src.core.watchdog import Watchdog


async def main() -> None:
    settings = get_settings()

    # ── Logging ───────────────────────────────────────────────────────────────
    setup_logging(settings)

    from loguru import logger  # import after setup

    logger.info(
        "Sky News Arabia Sport Monitor starting",
        env=settings.env,
        poll_interval=settings.poll_interval_seconds,
        subcategories=[s["name"] for s in settings.subcategories],
    )

    # ── Sentry (optional) ─────────────────────────────────────────────────────
    if settings.sentry_dsn:
        import sentry_sdk
        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            environment=settings.env,
            traces_sample_rate=0.1,
        )
        logger.info("Sentry error tracking enabled")

    # ── Run ───────────────────────────────────────────────────────────────────
    watchdog = Watchdog(settings)
    try:
        await watchdog.run()
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received")
    finally:
        await watchdog.shutdown()
        logger.info("Shutdown complete")


if __name__ == "__main__":
    # Python 3.12+ asyncio.run with improved exception handling
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
