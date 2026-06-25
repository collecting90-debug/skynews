"""
src/scraper/filgoal_engine.py
──────────────────────────────
Scraping engine for FilGoal.com
"""

from __future__ import annotations

import asyncio
from typing import Optional

from loguru import logger
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from src.core.config import Settings
from src.core.models import RawArticle
from src.scraper.browser import BrowserManager
from src.scraper.filgoal_parser import parse_article_detail, parse_article_list

_CONTENT_READY_SELECTORS = [
    "li a[href^='/articles/']",
    "ul li a",
    "article",
]

_CONTENT_WAIT_MS = 30_000


class FilGoalScraper:

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._browser_manager = BrowserManager(settings)
        self._seen_urls: set[str] = set()

    async def start(self) -> None:
        await self._browser_manager.start()

    async def stop(self) -> None:
        await self._browser_manager.stop()

    async def __aenter__(self) -> "FilGoalScraper":
        await self.start()
        return self

    async def __aexit__(self, *_) -> None:
        await self.stop()

    async def poll_subcategory(self, subcategory: dict) -> list[RawArticle]:
        name = subcategory["name"]
        url  = subcategory["url"]

        logger.info(f"Polling FilGoal: {name}", url=url)

        listing_html = await self._safe_load_page(url, wait_for_content=True)
        if not listing_html:
            logger.error(f"Failed to load FilGoal listing page for {name}")
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

    async def seed_seen_urls(self, known_urls: set[str]) -> None:
        self._seen_urls.update(known_urls)
        logger.info(f"Seeded {len(known_urls)} known article URLs into seen set")

    # ── Page loading ──────────────────────────────────────────────────────────

    async def _safe_load_page(self, url: str, wait_for_content: bool = False) -> Optional[str]:
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

            backoff = min(self._settings.retry_backoff_base * (2 ** (attempt - 1)), 60.0)
            await asyncio.sleep(backoff)

        logger.error(f"All attempts failed for {url}: {last_exc}")
        return None

    async def _navigate_and_extract(self, page: Page, url: str, wait_for_content: bool) -> str:
        await page.goto(url, timeout=self._settings.page_load_timeout, wait_until="domcontentloaded")

        if wait_for_content:
            for selector in _CONTENT_READY_SELECTORS:
                try:
                    await page.wait_for_selector(selector, timeout=_CONTENT_WAIT_MS, state="attached")
                    await asyncio.sleep(1)
                    break
                except PlaywrightTimeoutError:
                    continue
        else:
            await asyncio.sleep(2)

        return await page.content()

    # ── Article batch fetching ────────────────────────────────────────────────

    async def _fetch_articles_batch(self, stubs: list[RawArticle], batch_size: int = 3) -> list[RawArticle]:
        results: list[RawArticle] = []

        for i in range(0, len(stubs), batch_size):
            batch = stubs[i: i + batch_size]
            tasks = [self._fetch_article_detail(stub) for stub in batch]
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)

            for stub, result in zip(batch, batch_results):
                if isinstance(result, Exception):
                    logger.warning(f"Failed to fetch detail for {stub.url}: {result}")
                    results.append(stub)
                else:
                    results.append(result)  # type: ignore

            await asyncio.sleep(2)

        return results

    async def _fetch_article_detail(self, stub: RawArticle) -> RawArticle:
        html = await self._safe_load_page(stub.url, wait_for_content=False)
        if not html:
            return stub

        # ── DEBUG: save HTML to inspect structure (remove after fixing parser) ──
        try:
            with open("article_debug.html", "w", encoding="utf-8") as f:
                f.write(html)
            logger.debug(f"Saved debug HTML for {stub.url}")
        except Exception:
            pass
        # ────────────────────────────────────────────────────────────────────────

        return parse_article_detail(html, stub)