"""
src/scraper/skynews_realtime_monitor.py
────────────────────────────────────────
Real-time Sky News Arabia Sport monitor using ETag / Last-Modified
conditional HTTP GETs for maximum efficiency.

Strategy:
  • Poll the sport section listing page every POLL_INTERVAL seconds
  • Use If-None-Match / If-Modified-Since — zero bytes transferred if nothing changed
  • When content changes, extract new article URLs and process them immediately
  • All article fetching is done with httpx (no browser needed for detail pages)

Latency budget (worst case):
  - Poll interval:     15s
  - HTTP conditional:  ~0.4s
  - Parse + classify:  ~0.5s
  - Telegram push:     ~0.5s
  Total worst-case:    ~16.5s
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from datetime import datetime
from typing import Awaitable, Callable, Optional
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from loguru import logger

from src.scraper.skynews_parser_v2 import SkyNewsParser, ParsedArticle

SKYNEWS_SPORT_URL = "https://www.skynewsarabia.com/sport"
SKYNEWS_BASE = "https://www.skynewsarabia.com"
POLL_INTERVAL = 15  # seconds

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ar,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.skynewsarabia.com/",
}

ArticleCallback = Callable[[ParsedArticle], Awaitable[None]]


class SkyNewsRealtimeMonitor:
    """
    Event-driven monitor for Sky News Arabia Sport.

    Usage::
        monitor = SkyNewsRealtimeMonitor(on_new_article=my_handler)
        await monitor.run_forever()
    """

    def __init__(self, on_new_article: ArticleCallback) -> None:
        self._callback = on_new_article
        self._seen_urls: set[str] = set()
        self._etag: Optional[str] = None
        self._last_modified: Optional[str] = None
        self._parser = SkyNewsParser()
        self._client = httpx.AsyncClient(
            headers=_HEADERS,
            timeout=15,
            follow_redirects=True,
            http2=True,
        )

    async def run_forever(self) -> None:
        """Main loop — runs until cancelled."""
        logger.info(
            f"SkyNewsRealtimeMonitor started — polling every {POLL_INTERVAL}s",
            source=SKYNEWS_SPORT_URL,
        )

        # Seed existing URLs so we don't re-process on startup
        await self._seed_seen_urls()

        while True:
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(f"Poll cycle error: {exc}", exc_info=True)

            await asyncio.sleep(POLL_INTERVAL)

    async def stop(self) -> None:
        await self._client.aclose()
        await self._parser.close()

    # ── Seeding ────────────────────────────────────────────────────────────────

    async def _seed_seen_urls(self) -> None:
        """Mark all currently visible articles as seen on startup."""
        try:
            urls = await self._fetch_current_urls(force=True)
            self._seen_urls.update(urls)
            logger.info(
                f"Seeded {len(urls)} existing article URLs — watching for new ones"
            )
        except Exception as exc:
            logger.warning(f"Could not seed URLs on startup: {exc}")

    # ── Poll cycle ─────────────────────────────────────────────────────────────

    async def _poll_once(self) -> None:
        """
        Conditional GET:
          304 Not Modified → nothing to do (0 bytes transferred)
          200 OK           → extract URLs → diff → process new articles
        """
        conditional_headers: dict[str, str] = {}
        if self._etag:
            conditional_headers["If-None-Match"] = self._etag
        if self._last_modified:
            conditional_headers["If-Modified-Since"] = self._last_modified

        try:
            response = await self._client.get(
                SKYNEWS_SPORT_URL,
                headers=conditional_headers,
            )
        except httpx.RequestError as exc:
            logger.warning(f"Network error during poll: {exc}")
            return

        if response.status_code == 304:
            logger.debug("304 Not Modified — no new articles")
            return

        # Update cache headers
        self._etag = response.headers.get("ETag")
        self._last_modified = response.headers.get("Last-Modified")

        if response.status_code != 200:
            logger.warning(f"Unexpected HTTP status: {response.status_code}")
            return

        current_urls = self._extract_urls_from_html(response.text)
        new_urls = [u for u in current_urls if u not in self._seen_urls]

        if not new_urls:
            logger.debug("Page changed but no new article URLs detected")
            return

        logger.info(f"Detected {len(new_urls)} new article(s)")

        sem = asyncio.Semaphore(3)
        tasks = [self._process_article(url, sem) for url in new_urls]
        await asyncio.gather(*tasks, return_exceptions=True)

    # ── URL extraction ─────────────────────────────────────────────────────────

    async def _fetch_current_urls(self, force: bool = False) -> list[str]:
        response = await self._client.get(SKYNEWS_SPORT_URL)
        response.raise_for_status()
        return self._extract_urls_from_html(response.text)

    def _extract_urls_from_html(self, html: str) -> list[str]:
        """Extract Sky News Arabia sport article URLs from the listing page."""
        soup = BeautifulSoup(html, "lxml")
        urls: list[str] = []

        for a in soup.find_all("a", href=True):
            href = str(a["href"])
            if _is_sport_article_href(href):
                full_url = (
                    href if href.startswith("http")
                    else urljoin(SKYNEWS_BASE, href)
                )
                if full_url not in urls:
                    urls.append(full_url)

        return urls

    # ── Article processing ─────────────────────────────────────────────────────

    async def _process_article(self, url: str, sem: asyncio.Semaphore) -> None:
        """Parse one article and fire the callback."""
        async with sem:
            # Mark seen immediately to prevent double-processing
            self._seen_urls.add(url)
            try:
                article = await self._parser.parse(url)
                logger.info(f"New article detected: {article.title[:70]}")
                await self._callback(article)
            except Exception as exc:
                logger.error(f"Failed to process {url}: {exc}")
                # Remove from seen so it's retried next cycle
                self._seen_urls.discard(url)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _is_sport_article_href(href: str) -> bool:
    """Return True for Sky News Arabia sport article URLs only."""
    if not href or "/sport/" not in href:
        return False
    stripped = href.rstrip("/")
    if stripped.endswith("/sport"):
        return False
    # Must have at least one digit in the slug (article numeric ID)
    slug_part = href.split("/sport/")[-1].split("/")[0].split("?")[0]
    return bool(re.search(r"\d", slug_part))
