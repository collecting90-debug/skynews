"""
src/database/cache.py
─────────────────────
Redis-backed deduplication cache.

In a multi-worker deployment each worker process has its own in-memory
seen-URL set.  Redis provides a shared, persistent layer so that:

  • An article scraped by worker A is not re-processed by worker B.
  • After a crash+restart the seen set is restored instantly without
    a full DB scan.

Falls back gracefully to a no-op if Redis is unavailable.
"""

from __future__ import annotations

from typing import Optional

import redis.asyncio as aioredis
from loguru import logger

from src.core.config import Settings

# TTL for seen-article keys: 30 days (articles older than this are irrelevant)
_KEY_TTL_SECONDS = 30 * 24 * 3600
_KEY_PREFIX = "wam:seen:"


class DeduplicationCache:
    """
    Async Redis cache for article deduplication.

    Usage::

        cache = DeduplicationCache(settings)
        await cache.connect()

        if await cache.is_seen("abc123"):
            return  # already processed

        await cache.mark_seen("abc123")
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._redis: Optional[aioredis.Redis] = None
        self._available: bool = False

    async def connect(self) -> None:
        """Connect to Redis. Does not raise — marks unavailable on failure."""
        try:
            self._redis = aioredis.from_url(
                self._settings.redis_url,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
            )
            await self._redis.ping()
            self._available = True
            logger.info("Redis deduplication cache connected", url=self._settings.redis_url)
        except Exception as exc:
            self._available = False
            logger.warning(
                f"Redis unavailable ({exc}) — falling back to in-process deduplication only"
            )

    async def disconnect(self) -> None:
        if self._redis:
            await self._redis.aclose()
        self._available = False

    async def is_seen(self, article_hash: str) -> bool:
        """Return True if this article hash has been seen before."""
        if not self._available or not self._redis:
            return False
        try:
            result = await self._redis.exists(f"{_KEY_PREFIX}{article_hash}")
            return bool(result)
        except Exception as exc:
            logger.warning(f"Redis is_seen failed: {exc}")
            return False

    async def mark_seen(self, article_hash: str) -> None:
        """Mark an article hash as seen (with TTL)."""
        if not self._available or not self._redis:
            return
        try:
            await self._redis.setex(
                f"{_KEY_PREFIX}{article_hash}",
                _KEY_TTL_SECONDS,
                "1",
            )
        except Exception as exc:
            logger.warning(f"Redis mark_seen failed: {exc}")

    async def bulk_mark_seen(self, hashes: set[str]) -> None:
        """Bulk-insert a set of hashes (used at startup for seeding)."""
        if not self._available or not self._redis or not hashes:
            return
        try:
            pipe = self._redis.pipeline()
            for h in hashes:
                pipe.setex(f"{_KEY_PREFIX}{h}", _KEY_TTL_SECONDS, "1")
            await pipe.execute()
            logger.info(f"Seeded {len(hashes)} hashes into Redis cache")
        except Exception as exc:
            logger.warning(f"Redis bulk_mark_seen failed: {exc}")

    @property
    def available(self) -> bool:
        return self._available
