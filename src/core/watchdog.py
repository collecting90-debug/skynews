"""
src/core/watchdog.py
─────────────────────
Process-level watchdog for 24/7 operation.

The watchdog wraps the pipeline in a supervisor loop:
  • Catches any unhandled exception that escapes the pipeline
  • Logs the crash with full traceback
  • Waits a back-off period
  • Restarts the pipeline from scratch

This guarantees the system continues running even after:
  • Playwright browser crashes
  • Network outages
  • Supabase API errors
  • Memory issues
  • Any other unhandled exception
"""

from __future__ import annotations

import asyncio
import signal
import sys
from typing import Optional

from loguru import logger

from src.core.config import Settings
from src.core.pipeline import ArticlePipeline

# How long to wait before restarting after a crash (seconds)
_RESTART_DELAYS = [10, 30, 60, 120, 300]   # progressive back-off


class Watchdog:
    """
    Supervisor that keeps ArticlePipeline running indefinitely.

    Usage::

        watchdog = Watchdog(settings)
        await watchdog.run()   # blocks until SIGTERM / SIGINT
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._pipeline: Optional[ArticlePipeline] = None
        self._restart_count: int = 0
        self._shutdown_event = asyncio.Event()

    async def run(self) -> None:
        """Main entry point — install signal handlers and start the supervisor."""
        self._install_signal_handlers()
        logger.info("Watchdog started — monitoring pipeline")

        while not self._shutdown_event.is_set():
            try:
                await self._run_pipeline()
            except asyncio.CancelledError:
                logger.info("Pipeline cancelled — shutting down")
                break
            except Exception as exc:
                self._restart_count += 1
                delay = self._get_restart_delay()
                logger.error(
                    f"Pipeline crashed (restart #{self._restart_count}): {exc}",
                    exc_info=True,
                )
                logger.info(f"Restarting in {delay}s …")
                try:
                    await asyncio.wait_for(
                        self._shutdown_event.wait(),
                        timeout=delay,
                    )
                except asyncio.TimeoutError:
                    pass   # delay elapsed normally
                else:
                    break  # shutdown was requested during delay

        logger.info("Watchdog stopped")

    async def shutdown(self) -> None:
        """Request graceful shutdown."""
        logger.info("Shutdown requested")
        self._shutdown_event.set()
        if self._pipeline:
            try:
                await self._pipeline.stop()
            except Exception as exc:
                logger.warning(f"Error during pipeline shutdown: {exc}")

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _run_pipeline(self) -> None:
        """Start and run the pipeline until it raises or shutdown is requested."""
        self._pipeline = ArticlePipeline(self._settings)
        await self._pipeline.start()
        self._restart_count = 0   # reset on successful start

        try:
            pipeline_task = asyncio.create_task(self._pipeline.run_forever())
            shutdown_task = asyncio.create_task(self._shutdown_event.wait())

            done, pending = await asyncio.wait(
                {pipeline_task, shutdown_task},
                return_when=asyncio.FIRST_COMPLETED,
            )

            for task in pending:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

            # Re-raise any pipeline exception
            for task in done:
                if task is pipeline_task and not pipeline_task.cancelled():
                    exc = pipeline_task.exception()
                    if exc:
                        raise exc

        finally:
            try:
                await self._pipeline.stop()
            except Exception as exc:
                logger.warning(f"Error stopping pipeline: {exc}")
            self._pipeline = None

    def _get_restart_delay(self) -> float:
        """Progressive back-off delay based on restart count."""
        idx = min(self._restart_count - 1, len(_RESTART_DELAYS) - 1)
        return float(_RESTART_DELAYS[idx])

    def _install_signal_handlers(self) -> None:
        """Install SIGTERM / SIGINT handlers for graceful shutdown."""
        loop = asyncio.get_event_loop()

        def _handle_signal(sig_name: str):
            logger.info(f"Received {sig_name} — initiating graceful shutdown")
            asyncio.create_task(self.shutdown())

        if sys.platform != "win32":
            loop.add_signal_handler(signal.SIGTERM, lambda: _handle_signal("SIGTERM"))
            loop.add_signal_handler(signal.SIGINT, lambda: _handle_signal("SIGINT"))
