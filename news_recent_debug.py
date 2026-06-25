#!/usr/bin/env python3
"""
news_recent_debug.py
──────────────────────
Debug tool: fetch and inspect the Sky News Arabia Sport listing page.

Usage:
    python news_recent_debug.py

Saves:
  • news_recent_debug.html         — full raw HTML of the sport section
  • news_recent_debug_articles.txt — list of detected article URLs + titles

This is the page used by the refresh/polling logic to detect new articles.
Use the saved HTML to verify and calibrate selectors used in:
  • src/scraper/skynews_parser.py   (parse_article_list)
  • src/scraper/skynews_realtime_monitor.py (_extract_urls_from_html)
"""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path
from urllib.parse import urljoin

OUTPUT_HTML = Path("news_recent_debug.html")
OUTPUT_ARTICLES = Path("news_recent_debug_articles.txt")

SKYNEWS_SPORT_URL = "https://www.skynewsarabia.com/sport"
SKYNEWS_BASE = "https://www.skynewsarabia.com"


def _is_sport_article_href(href: str) -> bool:
    """Return True for Sky News Arabia sport article URLs."""
    if not href or "/sport/" not in href:
        return False
    stripped = href.rstrip("/")
    if stripped.endswith("/sport"):
        return False
    slug = href.split("/sport/")[-1].split("/")[0].split("?")[0]
    return bool(re.search(r"\d", slug))


async def main() -> None:
    print(f"[news_recent_debug] Fetching: {SKYNEWS_SPORT_URL}")

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("[ERROR] playwright not installed.")
        sys.exit(1)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
            ],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            locale="ar",
            timezone_id="Asia/Riyadh",
        )
        page = await context.new_page()

        print("[news_recent_debug] Loading page (waiting for articles)...")
        await page.goto(SKYNEWS_SPORT_URL, timeout=60_000, wait_until="domcontentloaded")

        # Wait for article cards to appear
        selectors_to_try = [
            "article a[href*='/sport/']",
            ".story-card",
            ".article-card",
            "a[href*='/sport/']",
        ]
        for sel in selectors_to_try:
            try:
                await page.wait_for_selector(sel, timeout=15_000, state="attached")
                print(f"[news_recent_debug] Content selector matched: {sel!r}")
                break
            except Exception:
                continue

        await asyncio.sleep(2)
        html = await page.content()
        await browser.close()

    OUTPUT_HTML.write_text(html, encoding="utf-8")
    print(f"[news_recent_debug] Raw HTML saved → {OUTPUT_HTML} ({len(html):,} bytes)")

    # Extract article URLs from the HTML
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")

        articles_found: list[dict] = []
        seen_urls: set[str] = set()

        for a in soup.find_all("a", href=True):
            href = str(a["href"])
            if not _is_sport_article_href(href):
                continue
            full_url = href if href.startswith("http") else urljoin(SKYNEWS_BASE, href)
            if full_url in seen_urls:
                continue
            seen_urls.add(full_url)

            # Try to get title from the anchor's text or a child element
            title_el = a.select_one("h1, h2, h3, h4, h5, h6, .title, .story-title")
            title = (title_el or a).get_text(strip=True)[:120]

            # Try to get date
            date_el = a.select_one("time, .date, span.date")
            date_text = date_el.get_text(strip=True) if date_el else ""

            articles_found.append({
                "url": full_url,
                "title": title,
                "date": date_text,
            })

        lines = [
            "=" * 70,
            f"SKY NEWS ARABIA SPORT — LISTING PAGE ANALYSIS",
            f"URL: {SKYNEWS_SPORT_URL}",
            f"Total HTML size: {len(html):,} bytes",
            f"Articles detected: {len(articles_found)}",
            "=" * 70,
            "",
        ]
        for i, art in enumerate(articles_found, 1):
            lines.append(f"{i:3d}. {art['title']}")
            lines.append(f"      URL:  {art['url']}")
            if art["date"]:
                lines.append(f"      Date: {art['date']}")
            lines.append("")

        # Also analyse card structure for selector debugging
        lines += [
            "=" * 70,
            "SELECTOR ANALYSIS (for calibrating parsers)",
            "=" * 70,
        ]
        for sel in [
            "article", ".story-card", ".article-card", ".content-card",
            "a[href*='/sport/']", "h1", "h2", "h3", "time",
            ".date", "[class*='date']",
        ]:
            count = len(soup.select(sel))
            lines.append(f"  {sel:<40} → {count} matches")

        result = "\n".join(lines)
        OUTPUT_ARTICLES.write_text(result, encoding="utf-8")
        print(f"[news_recent_debug] Article list saved → {OUTPUT_ARTICLES}")
        print()
        print(result[:1200])

    except Exception as exc:
        print(f"[news_recent_debug] Parse error: {exc}")
        print("  Raw HTML saved — inspect news_recent_debug.html manually.")


if __name__ == "__main__":
    asyncio.run(main())
