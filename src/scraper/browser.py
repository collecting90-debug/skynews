"""
src/scraper/browser.py
──────────────────────
Manages a persistent Playwright browser session.

Key responsibilities:
  • Launch / relaunch Chromium with anti-detection settings
  • Provide an async context-manager interface
  • Recover transparently from browser crashes
  • Rotate user-agents
  • Support proxy configuration
"""

from __future__ import annotations

import random
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional

from loguru import logger
from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)

from src.core.config import Settings

# ── Rotating user-agent pool ──────────────────────────────────────────────────
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]


class BrowserManager:
    """
    Manages the lifecycle of a Playwright browser instance.

    Usage::

        async with BrowserManager(settings) as manager:
            page = await manager.new_page()
            ...
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None

    # ── Public API ────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Launch Playwright and Chromium."""
        self._playwright = await async_playwright().start()
        self._browser = await self._launch_browser()
        self._context = await self._create_context()
        logger.info("Browser session started", headless=self._settings.headless)

    async def stop(self) -> None:
        """Gracefully close everything."""
        try:
            if self._context:
                await self._context.close()
            if self._browser:
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
        except Exception as exc:
            logger.warning(f"Error during browser shutdown: {exc}")
        finally:
            self._context = None
            self._browser = None
            self._playwright = None
        logger.info("Browser session stopped")

    async def restart(self) -> None:
        """Restart the browser after a crash."""
        logger.warning("Restarting browser session …")
        await self.stop()
        await self.start()

    async def new_page(self) -> Page:
        """
        Open a new page in the shared browser context.
        Automatically restarts the browser if the context is dead.
        """
        if not self._context:
            await self.restart()
        try:
            page = await self._context.new_page()  # type: ignore[union-attr]
            await self._configure_page(page)
            return page
        except Exception as exc:
            logger.error(f"Failed to open new page: {exc}; restarting browser …")
            await self.restart()
            page = await self._context.new_page()  # type: ignore[union-attr]
            await self._configure_page(page)
            return page

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _launch_browser(self) -> Browser:
        assert self._playwright is not None

        launch_kwargs: dict = {
            "headless": self._settings.headless,
            "args": [
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--window-size=1920,1080",
                "--disable-web-security",
                "--disable-features=IsolateOrigins,site-per-process",
            ],
        }

        if self._settings.proxy_url:
            launch_kwargs["proxy"] = {"server": self._settings.proxy_url}

        return await self._playwright.chromium.launch(**launch_kwargs)

    async def _create_context(self) -> BrowserContext:
        assert self._browser is not None

        ua = random.choice(_USER_AGENTS)

        ctx = await self._browser.new_context(
            user_agent=ua,
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
            timezone_id="Asia/Dubai",
            java_script_enabled=True,
            accept_downloads=False,
            ignore_https_errors=True,
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )

        # Stealth: mask automation signals (bypass F5 bot protection)
        await ctx.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', {
                get: () => {
                    const arr = [
                        { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
                        { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
                        { name: 'Native Client', filename: 'internal-nacl-plugin', description: '' },
                    ];
                    arr.__proto__ = PluginArray.prototype;
                    return arr;
                }
            });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en', 'ar'] });
            window.chrome = { runtime: {}, loadTimes: function(){}, csi: function(){}, app: {} };
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                    Promise.resolve({ state: Notification.permission }) :
                    originalQuery(parameters)
            );
            delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;
            delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;
            delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol;
        """)

        return ctx

    async def _configure_page(self, page: Page) -> None:
        """Apply per-page performance and stealth settings."""
        # Block only heavy media — don't block fonts/css as F5 may use them for fingerprinting
        await page.route(
            "**/*.{mp4,webm,ogg,mp3,wav}",
            lambda route: route.abort(),
        )
        # Reasonable default timeout for all operations
        page.set_default_timeout(self._settings.page_load_timeout)

    # ── Context manager protocol ──────────────────────────────────────────────

    async def __aenter__(self) -> "BrowserManager":
        await self.start()
        return self

    async def __aexit__(self, *_) -> None:
        await self.stop()