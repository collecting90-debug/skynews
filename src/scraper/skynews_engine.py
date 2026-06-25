"""
src/scraper/skynews_engine.py
──────────────────────────────
Scraping engine for Sky News Arabia Sport.
https://www.skynewsarabia.com/sport

Architecture (near-real-time):
  • httpx for the listing page  — fast, no browser overhead
  • Playwright ONLY for article detail pages that need JS
  • ETag / Last-Modified conditional GETs: 0 bytes when nothing changed
  • On first startup: seed ALL current articles as seen except the single
    latest one, so only one "catch-up" message is sent before live monitoring
"""

from __future__ import annotations

import asyncio
import re
from typing import Optional
from urllib.parse import urljoin

import httpx
from loguru import logger
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from src.core.config import Settings
from src.core.models import RawArticle
from src.scraper.browser import BrowserManager
from src.scraper.skynews_parser import (
    SKYNEWS_SPORT_URL,
    parse_article_detail,
    parse_article_list,
)

SKYNEWS_BASE = "https://www.skynewsarabia.com"

_CONTENT_READY_SELECTORS = [
    "article a[href*='/sport/']",
    ".story-card",
    ".article-card",
    ".content-card",
    "a[href*='/sport/']",
]

_CONTENT_WAIT_MS = 15_000

_HTTPX_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ar,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.skynewsarabia.com/",
}


class SkyNewsArabiaScraper:
    """
    Production scraper for Sky News Arabia Sport section.

    Startup behaviour
    -----------------
    On first run, the live listing page is fetched and ALL visible articles
    are marked as seen — except the single most-recent one.  That one article
    is processed and sent to Telegram as a "current state" signal.  After that,
    only genuinely new articles (published after the run started) are sent.

    This prevents the bot from flooding the channel with old articles every
    time the process restarts.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._browser_manager = BrowserManager(settings)
        self._seen_urls: set[str] = set()
        self._etag: dict[str, str] = {}
        self._last_modified: dict[str, str] = {}
        self._http_client: Optional[httpx.AsyncClient] = None
        self._startup_done: bool = False   # flips to True after first listing fetch

    async def start(self) -> None:
        self._http_client = httpx.AsyncClient(
            headers=_HTTPX_HEADERS,
            timeout=15,
            follow_redirects=True,
            http2=True,
        )
        await self._browser_manager.start()

    async def stop(self) -> None:
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None
        await self._browser_manager.stop()

    async def __aenter__(self) -> "SkyNewsArabiaScraper":
        await self.start()
        return self

    async def __aexit__(self, *_) -> None:
        await self.stop()

    # ── Public API ─────────────────────────────────────────────────────────────

    async def seed_seen_urls(self, known_urls: set[str]) -> None:
        """Seed URLs already in the DB so they are never re-processed."""
        self._seen_urls.update(known_urls)
        logger.info(f"Seeded {len(known_urls)} known article URLs from DB into seen set")

    async def seed_from_live_listing(self, subcategory: dict) -> Optional[RawArticle]:
        """
        Fetch the live listing page once at startup.

        Marks ALL currently visible articles as seen, EXCEPT the very first
        (most recent) one which is returned so the pipeline can process+send it.

        Returns the latest article stub, or None if the listing could not be fetched.
        """
        url = subcategory["url"]
        name = subcategory["name"]

        logger.info(f"Startup: fetching live listing to seed seen URLs — {name}")

        html = await self._httpx_load_listing(url, force=True)
        if not html:
            logger.warning("Startup: could not fetch live listing — will treat first poll normally")
            return None

        stubs = parse_article_list(html, subcategory=name)
        if not stubs:
            logger.warning("Startup: no articles found in live listing")
            return None

        # stubs[0] = most recent article on the page (first in DOM order)
        latest_stub = stubs[0]

        # Mark ALL articles as seen — including the latest.
        # The pipeline will handle sending the latest separately as a startup signal.
        # This prevents the polling loop from ever re-processing startup articles.
        for stub in stubs:
            self._seen_urls.add(stub.url)

        logger.info(
            f"Startup: seeded {len(stubs)} articles as seen. "
            f"Latest: {latest_stub.url}"
        )
        return latest_stub

    async def fetch_one_article(self, stub: RawArticle) -> RawArticle:
        """Fetch full detail for a single article stub."""
        return await self._fetch_article_detail(stub)

    async def poll_subcategory(self, subcategory: dict) -> list[RawArticle]:
        name = subcategory["name"]
        url = subcategory["url"]

        logger.info(f"Polling Sky News Arabia: {name}", url=url)

        if self._settings.use_httpx_for_listing:
            listing_html = await self._httpx_load_listing(url)
        else:
            listing_html = await self._safe_load_page(url, wait_for_content=True)

        # Empty string = 304 Not Modified — nothing to do
        if listing_html == "":
            return []

        if not listing_html:
            logger.error(f"Failed to load listing page for {name}")
            return []

        stubs = parse_article_list(listing_html, subcategory=name)
        new_stubs = [a for a in stubs if a.url not in self._seen_urls]
        logger.info(f"{len(new_stubs)} new articles in {name}")

        if not new_stubs:
            return []

        articles = await self._fetch_articles_batch(new_stubs, batch_size=3)

        for article in articles:
            self._seen_urls.add(article.url)

        return articles

    # ── Fast httpx listing fetch ───────────────────────────────────────────────

    async def _httpx_load_listing(self, url: str, force: bool = False) -> Optional[str]:
        """
        Fetch the listing page with httpx.
        Returns:
          str  — HTML content (may be long)
          ""   — 304 Not Modified (nothing changed, fast path)
          None — hard failure
        force=True skips conditional headers (used at startup).
        """
        assert self._http_client is not None

        conditional_headers: dict[str, str] = {}
        if not force:
            if url in self._etag:
                conditional_headers["If-None-Match"] = self._etag[url]
            if url in self._last_modified:
                conditional_headers["If-Modified-Since"] = self._last_modified[url]

        for attempt in range(1, 4):
            try:
                response = await self._http_client.get(url, headers=conditional_headers)

                if response.status_code == 304:
                    logger.debug(f"304 Not Modified — no new articles")
                    return ""

                if etag := response.headers.get("ETag"):
                    self._etag[url] = etag
                if lm := response.headers.get("Last-Modified"):
                    self._last_modified[url] = lm

                if response.status_code != 200:
                    logger.warning(f"HTTP {response.status_code} for {url}")
                    return None

                html = response.text
                article_links = len(re.findall(r'/sport/\d', html))
                if article_links < 3:
                    logger.warning(
                        f"httpx listing: only {article_links} article links — "
                        f"falling back to Playwright"
                    )
                    return await self._safe_load_page(url, wait_for_content=True)

                return html

            except httpx.RequestError as exc:
                logger.warning(f"httpx listing error (attempt {attempt}): {exc}")
                if attempt < 3:
                    await asyncio.sleep(2 * attempt)

        logger.warning("All httpx listing attempts failed — trying Playwright")
        return await self._safe_load_page(url, wait_for_content=True)

    # ── Playwright fallback ────────────────────────────────────────────────────

    async def _safe_load_page(
        self,
        url: str,
        wait_for_content: bool = False,
    ) -> Optional[str]:
        last_exc: Optional[Exception] = None

        for attempt in range(1, self._settings.max_retries + 1):
            page: Optional[Page] = None
            try:
                page = await self._browser_manager.new_page()
                html = await self._navigate_and_extract(page, url, wait_for_content)
                return html

            except PlaywrightTimeoutError as exc:
                last_exc = exc
                logger.warning(f"Timeout loading {url} (attempt {attempt})")

            except Exception as exc:
                last_exc = exc
                logger.warning(f"Error loading {url} (attempt {attempt}): {exc}")
                if "browser" in str(exc).lower() or "target" in str(exc).lower():
                    await self._browser_manager.restart()

            finally:
                if page and not page.is_closed():
                    try:
                        await page.close()
                    except Exception:
                        pass

            backoff = min(
                self._settings.retry_backoff_base * (2 ** (attempt - 1)), 60.0
            )
            await asyncio.sleep(backoff)

        logger.error(f"All attempts failed for {url}: {last_exc}")
        return None

    async def _navigate_and_extract(
        self,
        page: Page,
        url: str,
        wait_for_content: bool,
    ) -> str:
        await page.goto(
            url,
            timeout=self._settings.page_load_timeout,
            wait_until="domcontentloaded",
        )

        if wait_for_content:
            for selector in _CONTENT_READY_SELECTORS:
                try:
                    await page.wait_for_selector(
                        selector,
                        timeout=_CONTENT_WAIT_MS,
                        state="attached",
                    )
                    await asyncio.sleep(0.5)
                    break
                except PlaywrightTimeoutError:
                    continue
        else:
            await asyncio.sleep(0.5)

        return await page.content()

    # ── Article batch fetching ─────────────────────────────────────────────────

    async def _fetch_articles_batch(
        self,
        stubs: list[RawArticle],
        batch_size: int = 3,
    ) -> list[RawArticle]:
        results: list[RawArticle] = []

        for i in range(0, len(stubs), batch_size):
            batch = stubs[i : i + batch_size]
            tasks = [self._fetch_article_detail(stub) for stub in batch]
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)

            for stub, result in zip(batch, batch_results):
                if isinstance(result, Exception):
                    logger.warning(f"Failed to fetch detail for {stub.url}: {result}")
                    results.append(stub)
                else:
                    results.append(result)  # type: ignore[arg-type]

            if i + batch_size < len(stubs):
                await asyncio.sleep(0.5)

        return results

    async def _fetch_article_detail(self, stub: RawArticle) -> RawArticle:
        html = await self._httpx_fetch_detail(stub.url)
        if not html:
            html = await self._safe_load_page(stub.url, wait_for_content=False)
        if not html:
            return stub
        return parse_article_detail(html, stub)

    async def _httpx_fetch_detail(self, url: str) -> Optional[str]:
        assert self._http_client is not None
        try:
            response = await self._http_client.get(url)
            if response.status_code != 200:
                return None
            html = response.text
            if 'data-sna-init' in html or len(html) > 5000:
                return html
            return None
        except httpx.RequestError:
            return None
