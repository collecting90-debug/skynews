"""
src/database/repository.py
──────────────────────────
Data access layer — all Supabase/PostgreSQL operations live here.

Pattern: Repository pattern — no SQL leaks into business logic layers.
The rest of the application only calls methods on ArticleRepository.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from loguru import logger
from supabase import AsyncClient, create_async_client

from src.core.config import Settings
from src.core.models import Article, NewsClassification, ScrapingStatus


TABLE = "articles"
DISPLAY_TABLE = "articles_display"


class ArticleRepository:
    """
    Async repository for article persistence via Supabase.

    Usage::

        repo = ArticleRepository(settings)
        await repo.connect()

        saved = await repo.save(article)
        exists = await repo.exists_by_hash(article.article_hash)
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: Optional[AsyncClient] = None

    # ── Connection lifecycle ──────────────────────────────────────────────────

    async def connect(self) -> None:
        """Initialise the Supabase async client."""
        self._client = await create_async_client(
            self._settings.supabase_url,
            self._settings.supabase_service_role_key,
        )
        logger.info("Supabase client connected")

    async def disconnect(self) -> None:
        """Close the Supabase client connection."""
        if self._client:
            # supabase-py v2 doesn't expose an explicit close — handled by GC
            self._client = None
        logger.info("Supabase client disconnected")

    def _db(self) -> AsyncClient:
        if not self._client:
            raise RuntimeError("ArticleRepository not connected — call connect() first")
        return self._client

    # ── Write operations ──────────────────────────────────────────────────────

    async def save(self, article: Article) -> Optional[dict]:
        """
        Insert a new article.
        Returns the inserted row or None if a duplicate was detected.
        Duplicate detection uses the article_hash (SHA-256 of URL).
        """
        if await self.exists_by_hash(article.article_hash):
            logger.debug(f"Duplicate skipped: {article.url}")
            return None

        row = self._to_row(article)
        try:
            result = (
                await self._db()
                .table(TABLE)
                .insert(row)
                .execute()
            )
            if result.data:
                logger.info(
                    f"Article saved: {article.title[:60]}",
                    classification=article.classification,
                    subcategory=article.subcategory,
                )
                return result.data[0]
            return None
        except Exception as exc:
            logger.error(f"Failed to save article {article.url}: {exc}")
            raise

    async def update_telegram_sent(self, article_hash: str) -> None:
        """Mark an article as sent to Telegram."""
        await (
            self._db()
            .table(TABLE)
            .update({
                "telegram_sent": True,
                "telegram_sent_at": datetime.utcnow().isoformat(),
                "status": ScrapingStatus.PUBLISHED.value,
            })
            .eq("article_hash", article_hash)
            .execute()
        )

    async def update_status(self, article_hash: str, status: ScrapingStatus) -> None:
        """Update the processing status of an article."""
        await (
            self._db()
            .table(TABLE)
            .update({"status": status.value})
            .eq("article_hash", article_hash)
            .execute()
        )

    # ── Read operations ───────────────────────────────────────────────────────

    async def exists_by_hash(self, article_hash: str) -> bool:
        """Check if an article already exists by its URL hash."""
        try:
            result = (
                await self._db()
                .table(TABLE)
                .select("id")
                .eq("article_hash", article_hash)
                .limit(1)
                .execute()
            )
            return bool(result.data)
        except Exception as exc:
            logger.warning(f"exists_by_hash check failed: {exc}")
            return False

    async def get_all_hashes(self) -> set[str]:
        """
        Return all stored article hashes.
        Used at startup to seed the scraper's seen-URL set.
        Paginates through all records to handle large datasets.
        """
        hashes: set[str] = set()
        page_size = 1000
        offset = 0

        while True:
            try:
                result = (
                    await self._db()
                    .table(TABLE)
                    .select("article_hash")
                    .range(offset, offset + page_size - 1)
                    .execute()
                )
                rows = result.data or []
                for row in rows:
                    hashes.add(row["article_hash"])
                if len(rows) < page_size:
                    break
                offset += page_size
            except Exception as exc:
                logger.error(f"Failed to fetch hashes (offset={offset}): {exc}")
                break

        logger.info(f"Loaded {len(hashes)} existing article hashes from DB")
        return hashes

    async def get_all_urls(self) -> set[str]:
        """Return all stored article URLs for deduplication seeding.
        Paginates through all records to handle large datasets.
        """
        urls: set[str] = set()
        page_size = 1000
        offset = 0

        while True:
            try:
                result = (
                    await self._db()
                    .table(TABLE)
                    .select("url")
                    .range(offset, offset + page_size - 1)
                    .execute()
                )
                rows = result.data or []
                for row in rows:
                    urls.add(row["url"])
                if len(rows) < page_size:
                    break
                offset += page_size
            except Exception as exc:
                logger.error(f"Failed to fetch URLs (offset={offset}): {exc}")
                break

        logger.info(f"Loaded {len(urls)} existing article URLs from DB")
        return urls

    async def get_recent(self, limit: int = 20) -> list[dict]:
        """Return the most recently scraped articles."""
        result = (
            await self._db()
            .table(TABLE)
            .select("*")
            .order("scraped_at", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data or []

    async def count_by_classification(self, classification: NewsClassification) -> int:
        """Return the total count of articles with the given classification."""
        try:
            result = (
                await self._db()
                .table(TABLE)
                .select("id", count="exact")
                .eq("classification", classification.value)
                .execute()
            )
            return result.count or 0
        except Exception as exc:
            logger.warning(f"count_by_classification failed: {exc}")
            return 0

    async def get_by_classification(
        self,
        classification: NewsClassification,
        limit: int = 50,
    ) -> list[dict]:
        result = (
            await self._db()
            .table(TABLE)
            .select("*")
            .eq("classification", classification.value)
            .order("scraped_at", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data or []

    # ── Display table ──────────────────────────────────────────────────────────

    async def save_display(self, article: Article, article_db_id: str) -> None:
        """
        Insert a slim record into articles_display for editorial use.
        Contains only: title, image_url, content, summary, publish_date, url.
        Silently skips if url already exists (UNIQUE constraint).
        """
        row = {
            "article_id":   article_db_id,
            "url":          article.url,
            "title":        article.title,
            "image_url":    article.image_url,
            "content":      article.content,
            "summary":      article.summary,
            "publish_date": article.publish_date.isoformat() if article.publish_date else None,
        }
        try:
            await (
                self._db()
                .table(DISPLAY_TABLE)
                .upsert(row, on_conflict="url", ignore_duplicates=True)
                .execute()
            )
        except Exception as exc:
            logger.warning(f"save_display failed for {article.url}: {exc}")

    # ── Mapping helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _to_row(article: Article) -> dict:
        """Convert an Article domain model to a Supabase-compatible dict."""
        return {
            "article_hash": article.article_hash,
            "url": article.url,
            "title": article.title,
            "content": article.content,
            "summary": article.summary,
            "image_url": article.image_url,
            "publish_date": article.publish_date.isoformat() if article.publish_date else None,
            "category": article.category,
            "subcategory": article.subcategory,
            "classification": article.classification.value,
            "classification_confidence": article.classification_confidence,
            "classification_method": article.classification_method,
            "detected_uae_entities": article.detected_uae_entities,
            "detected_arab_entities": article.detected_arab_entities,
            "detected_global_entities": article.detected_global_entities,
            "status": article.status.value,
            "scraped_at": article.scraped_at.isoformat(),
            "telegram_sent": article.telegram_sent,
            "telegram_sent_at": (
                article.telegram_sent_at.isoformat() if article.telegram_sent_at else None
            ),
        }
