"""
src/core/pipeline.py
─────────────────────
The central orchestration service.

ArticlePipeline wires together:
  Scraper → Classifier → Repository → Telegram

Startup behaviour
─────────────────
On start(), after seeding from the DB, the pipeline fetches the live listing
page ONCE and marks all currently visible articles as "seen" — except the
single most-recent one.  That one article is processed and sent to Telegram
immediately as a live "current state" signal.

After that, the normal polling loop runs every poll_interval_seconds and
sends only articles that are genuinely NEW (published after the run started).

This guarantees:
  • No flood of old articles on restart.
  • Exactly one "latest article" is sent on startup so the channel isn't silent.
  • Any new article published after startup is detected within ~20 seconds.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Optional

from loguru import logger

from src.classifier.engine import ArticleClassifier
from src.core.config import Settings
from src.core.models import Article, RawArticle, ScrapingStatus
from src.database.cache import DeduplicationCache
from src.database.repository import ArticleRepository
from src.scraper.skynews_engine import SkyNewsArabiaScraper
from src.telegram.sender import TelegramSender


class ArticlePipeline:
    """
    End-to-end article processing pipeline.

    Lifecycle::

        pipeline = ArticlePipeline(settings)
        await pipeline.start()
        await pipeline.run_forever()   # blocks until cancelled
        await pipeline.stop()
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._scraper = SkyNewsArabiaScraper(settings)
        self._classifier = ArticleClassifier(settings)
        self._repository = ArticleRepository(settings)
        self._cache = DeduplicationCache(settings)
        self._telegram = TelegramSender(settings)
        self._running = False

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """
        Initialise all subsystems, seed deduplication state, then send the
        single latest live article as a startup signal.
        """
        logger.info("Starting ArticlePipeline …")

        await self._repository.connect()
        await self._cache.connect()
        await self._telegram.start()
        await self._scraper.start()

        # 1. Seed from DB — articles already in storage are never re-sent
        existing_hashes = await self._repository.get_all_hashes()
        existing_urls = await self._repository.get_all_urls()
        await self._cache.bulk_mark_seen(existing_hashes)
        await self._scraper.seed_seen_urls(existing_urls)

        # 2. Seed from live listing — mark all visible articles as seen,
        #    ALWAYS send the latest one as a startup confirmation signal.
        for sub in self._settings.subcategories:
            latest_stub = await self._scraper.seed_from_live_listing(sub)
            if latest_stub:
                logger.info(
                    f"Startup: sending latest article as confirmation → {latest_stub.url}"
                )
                await self._send_startup_article(latest_stub)
            else:
                logger.info("Startup: no articles found — watching for new articles…")

        self._running = True
        logger.info("ArticlePipeline started ✓")

    async def stop(self) -> None:
        """Gracefully shut down all subsystems."""
        self._running = False
        await self._scraper.stop()
        await self._telegram.stop()
        await self._repository.disconnect()
        await self._cache.disconnect()
        logger.info("ArticlePipeline stopped")

    # ── Main loop ─────────────────────────────────────────────────────────────

    async def _send_startup_article(self, raw: RawArticle) -> None:
        """
        Always send the latest article on startup as a confirmation signal.
        Fetches full detail, saves to DB if not already there, and sends to Telegram.
        If already in DB, still sends to Telegram so the operator knows the bot is live.
        """
        try:
            # Try to fetch full article detail
            full_article = await self._scraper.fetch_one_article(raw)

            already_in_db = await self._repository.exists_by_hash(full_article.article_hash)

            if not already_in_db:
                # Brand new article — run full pipeline
                await self._process_article(full_article)
            else:
                # Already in DB — just send to Telegram as confirmation
                classification_result = await self._classifier.classify(full_article)
                article = Article(
                    **full_article.model_dump(),
                    classification=classification_result.classification,
                    classification_confidence=classification_result.confidence,
                    classification_method=classification_result.method,
                    detected_uae_entities=classification_result.uae_entities,
                    detected_arab_entities=classification_result.arab_entities,
                    detected_global_entities=classification_result.global_entities,
                    status=ScrapingStatus.CLASSIFIED,
                    scraped_at=datetime.utcnow(),
                )
                sent = await self._telegram.send(article)
                logger.info(
                    f"Startup confirmation sent ({'✓' if sent else '✗'}): {article.title[:60]}"
                )
        except Exception as exc:
            logger.error(f"Startup article send failed: {exc}")

    async def run_forever(self) -> None:
        """
        Continuously poll all subcategories.

        Uses deadline-aware sleeping: measures cycle duration and sleeps only
        the remaining time, keeping the effective interval stable at
        poll_interval_seconds regardless of how long each cycle takes.
        """
        interval = self._settings.poll_interval_seconds
        logger.info(
            f"Monitoring {len(self._settings.subcategories)} subcategories "
            f"with {interval}s interval"
        )

        while self._running:
            cycle_start = time.monotonic()

            for sub in self._settings.subcategories:
                if not self._running:
                    break
                try:
                    await self._poll_subcategory(sub)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.error(f"Unhandled error polling {sub['name']}: {exc}")

            elapsed = time.monotonic() - cycle_start
            remaining = max(0.0, interval - elapsed)

            if remaining > 0:
                logger.debug(
                    f"Poll cycle done in {elapsed:.1f}s — sleeping {remaining:.1f}s"
                )
                await asyncio.sleep(remaining)
            else:
                logger.debug(
                    f"Poll cycle took {elapsed:.1f}s (>{interval}s) — "
                    f"next poll immediate"
                )
                await asyncio.sleep(0)

    async def run_once(self) -> list[Article]:
        """Run exactly one poll cycle. Useful for testing / admin API."""
        all_articles: list[Article] = []
        for sub in self._settings.subcategories:
            articles = await self._poll_subcategory(sub)
            all_articles.extend(articles)
        return all_articles

    # ── Per-subcategory poll ──────────────────────────────────────────────────

    async def _poll_subcategory(self, sub: dict) -> list[Article]:
        raw_articles = await self._scraper.poll_subcategory(sub)
        if not raw_articles:
            return []

        processed: list[Article] = []
        for raw in raw_articles:
            article = await self._process_article(raw)
            if article:
                processed.append(article)

        return processed

    # ── Per-article processing ────────────────────────────────────────────────

    async def _process_article(self, raw: RawArticle) -> Optional[Article]:
        """
        Full pipeline for a single article:
        deduplicate → classify → save → notify.
        Returns the saved Article or None if skipped/failed.
        """
        # 1. Redis deduplication (fast path)
        if await self._cache.is_seen(raw.article_hash):
            logger.debug(f"Already in cache, skipping: {raw.url}")
            return None

        # 2. DB deduplication (authoritative)
        if await self._repository.exists_by_hash(raw.article_hash):
            await self._cache.mark_seen(raw.article_hash)
            logger.debug(f"Already in DB, skipping: {raw.url}")
            return None

        # 3. Classify
        classification_result = await self._classifier.classify(raw)

        # 4. Build enriched Article
        article = Article(
            **raw.model_dump(),
            classification=classification_result.classification,
            classification_confidence=classification_result.confidence,
            classification_method=classification_result.method,
            detected_uae_entities=classification_result.uae_entities,
            detected_arab_entities=classification_result.arab_entities,
            detected_global_entities=classification_result.global_entities,
            status=ScrapingStatus.CLASSIFIED,
            scraped_at=datetime.utcnow(),
        )

        # 5. Persist to Supabase
        saved_row = await self._repository.save(article)
        if not saved_row:
            return None

        await self._cache.mark_seen(article.article_hash)

        # 6. Send to Telegram
        sent = await self._telegram.send(article)
        if sent:
            await self._repository.update_telegram_sent(article.article_hash)
            article = article.model_copy(
                update={
                    "telegram_sent": True,
                    "telegram_sent_at": datetime.utcnow(),
                    "status": ScrapingStatus.PUBLISHED,
                }
            )

        logger.info(
            "Article pipeline complete",
            title=article.title[:60],
            classification=article.classification.value,
            telegram_sent=sent,
        )

        return article
