"""
src/scraper/realtime_monitor.py
────────────────────────────────
True real-time FilGoal monitor using ETag/Last-Modified + asyncio.
Replaces the 5-minute polling loop with a 15-second smart diff loop.

Why not WebSocket/SSE?
  FilGoal has no public push API. Best achievable without reverse-engineering
  their internal APIs is ETag-based conditional GET — zero bytes transferred
  when nothing changed, near-instant detection when it does.

Latency budget:
  - Poll interval:     15s  (worst case detection delay)
  - HTTP fetch:        ~0.5s
  - Parse + classify:  ~0.5s
  - Telegram push:     ~0.5s
  Total worst-case:    ~16.5s  (vs 5 minutes currently)
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime
from typing import Callable, Awaitable, Optional

import httpx
from loguru import logger

from src.scraper.skynews_parser_v2 import SkyNewsParser as FilGoalParser, ParsedArticle

SKYNEWS_SPORT_URL = "https://www.skynewsarabia.com/sport"
FILGOAL_ARTICLES_URL = SKYNEWS_SPORT_URL  # alias kept for compat
POLL_INTERVAL = 15  # seconds — sweet spot between real-time and rate limiting

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ar,en;q=0.9",
}

ArticleCallback = Callable[[ParsedArticle], Awaitable[None]]


class RealTimeMonitor:
    """
    Event-driven monitor using HTTP conditional requests.

    Usage:
        monitor = RealTimeMonitor(on_new_article=my_handler)
        await monitor.run_forever()
    """

    def __init__(self, on_new_article: ArticleCallback):
        self._callback = on_new_article
        self._seen_urls: set[str] = set()
        self._etag: Optional[str] = None
        self._last_modified: Optional[str] = None
        self._parser = FilGoalParser()
        self._client = httpx.AsyncClient(
            headers=HEADERS,
            timeout=10,
            follow_redirects=True,
            http2=True,
        )

    async def run_forever(self):
        """Main loop — runs until cancelled."""
        logger.info(f"SkyNewsRealtimeMonitor started — polling every {POLL_INTERVAL}s")

        # Seed seen URLs on startup (don't re-process existing articles)
        await self._seed_seen_urls()

        while True:
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(f"Poll error: {exc}")

            await asyncio.sleep(POLL_INTERVAL)

    async def stop(self):
        await self._client.aclose()
        await self._parser.close()

    # ── Seeding ───────────────────────────────────────────────────────────────

    async def _seed_seen_urls(self):
        """On startup, mark existing articles as seen so we don't re-process them."""
        try:
            urls = await self._fetch_article_urls(force=True)
            self._seen_urls.update(urls)
            logger.info(f"Seeded {len(urls)} existing URLs — watching for new ones")
        except Exception as exc:
            logger.warning(f"Could not seed URLs on startup: {exc}")

    # ── Poll Cycle ────────────────────────────────────────────────────────────

    async def _poll_once(self):
        """
        Conditional GET on the articles listing page.
        If ETag/Last-Modified unchanged → server returns 304 Not Modified → 0 bytes.
        If changed → fetch URLs → diff → process new ones.
        """
        headers = {}
        if self._etag:
            headers["If-None-Match"] = self._etag
        if self._last_modified:
            headers["If-Modified-Since"] = self._last_modified

        try:
            response = await self._client.get(
                FILGOAL_ARTICLES_URL,
                headers=headers,
            )
        except httpx.RequestError as exc:
            logger.warning(f"Network error: {exc}")
            return

        # 304 = nothing changed
        if response.status_code == 304:
            logger.debug("304 Not Modified — no new articles")
            return

        # Update cache headers for next request
        self._etag = response.headers.get("ETag")
        self._last_modified = response.headers.get("Last-Modified")

        if response.status_code != 200:
            logger.warning(f"Unexpected status: {response.status_code}")
            return

        # Extract article URLs from listing page
        current_urls = self._extract_urls_from_html(response.text)
        new_urls = [u for u in current_urls if u not in self._seen_urls]

        if not new_urls:
            logger.debug("Page changed but no new article URLs found")
            return

        logger.info(f"Detected {len(new_urls)} new article(s)")

        # Process new articles concurrently (max 3 at a time)
        sem = asyncio.Semaphore(3)
        tasks = [self._process_article(url, sem) for url in new_urls]
        await asyncio.gather(*tasks, return_exceptions=True)

    # ── URL Extraction ────────────────────────────────────────────────────────

    async def _fetch_article_urls(self, force: bool = False) -> list[str]:
        response = await self._client.get(FILGOAL_ARTICLES_URL)
        response.raise_for_status()
        return self._extract_urls_from_html(response.text)

    def _extract_urls_from_html(self, html: str) -> list[str]:
        """Extract article URLs from the FilGoal articles listing page."""
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        urls = []

        for a in soup.find_all("a", href=True):
            href = str(a["href"])
            # FilGoal article URLs pattern: /articles/XXXXXX/...
            if "/sport/" in href and re.search(r"\d", href.split("/sport/")[-1].split("/")[0]):
                full_url = href if href.startswith("http") else f"https://www.skynewsarabia.com{href}"
                # Deduplicate within this batch
                if full_url not in urls:
                    urls.append(full_url)

        return urls

    # ── Article Processing ────────────────────────────────────────────────────

    async def _process_article(self, url: str, sem: asyncio.Semaphore):
        """Parse one article and call the callback."""
        async with sem:
            # Mark as seen immediately to prevent double-processing
            self._seen_urls.add(url)

            try:
                article = await self._parser.parse(url)
                logger.info(f"New article: {article.title[:60]}")
                await self._callback(article)
            except Exception as exc:
                logger.error(f"Failed to process {url}: {exc}")
                # Remove from seen so it gets retried next cycle
                self._seen_urls.discard(url)