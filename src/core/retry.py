"""
src/core/retry.py
─────────────────
Reusable retry decorators and helpers built on top of tenacity.
Provides consistent exponential backoff across all I/O operations.
"""

from __future__ import annotations

import asyncio
from typing import Callable, TypeVar

from loguru import logger
from tenacity import (
    AsyncRetrying,
    RetryError,
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    wait_random_exponential,
)

import logging as _stdlib_logging

# tenacity uses stdlib logging; bridge to loguru
_log = _stdlib_logging.getLogger("tenacity")

T = TypeVar("T")


def build_retry(
    *,
    max_attempts: int = 5,
    min_wait: float = 2.0,
    max_wait: float = 60.0,
    exceptions: tuple = (Exception,),
):
    """
    Factory that returns a tenacity ``retry`` decorator with sane defaults.

    Usage::

        @build_retry(max_attempts=3, exceptions=(PlaywrightError,))
        async def load_page(url: str) -> str: ...
    """
    return retry(
        reraise=True,
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=1, min=min_wait, max=max_wait),
        retry=retry_if_exception_type(exceptions),
        before_sleep=before_sleep_log(_log, _stdlib_logging.WARNING),
    )


async def retry_async(
    fn: Callable,
    *args,
    max_attempts: int = 5,
    min_wait: float = 2.0,
    max_wait: float = 60.0,
    exceptions: tuple = (Exception,),
    **kwargs,
):
    """
    Inline async retry helper — useful when you cannot use a decorator.

    Example::

        result = await retry_async(
            fetch_article,
            url,
            max_attempts=3,
            exceptions=(TimeoutError,),
        )
    """
    async for attempt in AsyncRetrying(
        reraise=True,
        stop=stop_after_attempt(max_attempts),
        wait=wait_random_exponential(min=min_wait, max=max_wait),
        retry=retry_if_exception_type(exceptions),
    ):
        with attempt:
            return await fn(*args, **kwargs)


async def with_timeout(coro, timeout_seconds: float, operation: str = "operation"):
    """
    Await a coroutine with a hard timeout; logs and re-raises TimeoutError.
    """
    try:
        return await asyncio.wait_for(coro, timeout=timeout_seconds)
    except asyncio.TimeoutError:
        logger.error(f"Timeout after {timeout_seconds}s during: {operation}")
        raise
