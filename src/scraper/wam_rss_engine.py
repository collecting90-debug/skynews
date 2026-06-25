"""
src/scraper/wam_rss_engine.py
──────────────────────────────
NOTE: This file has been migrated from WAM (wam.ae) to Sky News Arabia.
      The class name and file are kept for backwards compatibility with any
      existing imports. New code should use SkyNewsArabiaScraper directly.

Sky News Arabia does not publish an RSS feed for sports, so this module
now performs lightweight HTTP polling of the sport section JSON API
(if available) or falls back to parsing the HTML listing page.

Sky News Arabia API endpoint (when available):
  https://www.skynewsarabia.com/api/articles?section=sport&page=1

HTML fallback:
  https://www.skynewsarabia.com/sport
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime
from typing import Optional
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from loguru import logger

from src.core.config import Settings
from src.core.models import RawArticle

# Sky News Arabia sport section (migrated from WAM RSS)
SKYNEWS_SPORT_FEEDS = [
    {"name": "رياضة", "url": "https://www.skynewsarabia.com/sport"},
]

SKYNEWS_BASE = "https://www.skynewsarabia.com"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ar,en;q=0.9",
    "Referer": "https://www.skynewsarabia.com/",
}


class WAMRSSScraper:
    """
    Backwards-compatible class name — now polls Sky News Arabia sport section.
    Lightweight httpx-based scraper (no Playwright needed for listing page).
    For full article content, use SkyNewsArabiaScraper (Playwright-based).
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._seen_urls: set[str] = set()
        self._client: Optional[httpx.AsyncClient] = None

    async def start(self) -> None:
        self._client = httpx.AsyncClient(
            headers=_HEADERS,
            timeout=30.0,
            follow_redirects=True,
        )
        logger.info("Sky News Arabia HTTP scraper started")

    async def stop(self) -> None:
        if self._client:
            await self._client.aclose()
        logger.info("Sky News Arabia HTTP scraper stopped")

    async def __aenter__(self) -> "WAMRSSScraper":
        await self.start()
        return self

    async def __aexit__(self, *_) -> None:
        await self.stop()

    async def seed_seen_urls(self, known_urls: set[str]) -> None:
        self._seen_urls.update(known_urls)
        logger.info(f"Seeded {len(known_urls)} known article URLs into seen set")

    async def poll_subcategory(self, subcategory: dict) -> list[RawArticle]:
        name = subcategory["name"]
        url = subcategory["url"]

        logger.info(f"Polling Sky News Arabia (HTTP): {name}", url=url)

        try:
            assert self._client is not None
            resp = await self._client.get(url)
            resp.raise_for_status()
        except Exception as exc:
            logger.error(f"Failed to fetch Sky News Arabia listing {url}: {exc}")
            return []

        articles = self._parse_listing(resp.text, name)
        new_articles = [a for a in articles if a.url not in self._seen_urls]
        logger.info(f"{len(new_articles)} new articles in {name}")

        for a in new_articles:
            self._seen_urls.add(a.url)

        return new_articles

    def _parse_listing(self, html: str, subcategory: str) -> list[RawArticle]:
        """Parse Sky News Arabia sport listing page HTML."""
        from src.scraper.skynews_parser import parse_article_list
        return parse_article_list(html, subcategory=subcategory)
