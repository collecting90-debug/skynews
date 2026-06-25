#!/usr/bin/env python3
"""
article_debug.py
─────────────────
Debug tool: fetch and save the raw HTML of a Sky News Arabia sport article.

Usage:
    python article_debug.py
    python article_debug.py https://www.skynewsarabia.com/sport/XXXXX-slug

The saved HTML is written to article_debug.html
Use it to inspect selectors, content structure, and verify parser logic.

This script uses Playwright so it captures the fully-rendered page
(including any JavaScript-injected content).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# ── Default test article: use first available sport article or explicit URL ──
DEFAULT_URL = "https://www.skynewsarabia.com/sport"

OUTPUT_HTML = Path("article_debug.html")
OUTPUT_PARSED = Path("article_debug_parsed.txt")


async def main(url: str) -> None:
    print(f"[article_debug] Fetching article: {url}")

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("[ERROR] playwright not installed. Run: pip install playwright && playwright install chromium")
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

        print(f"[article_debug] Loading page...")
        await page.goto(url, timeout=60_000, wait_until="domcontentloaded")
        await asyncio.sleep(3)  # let dynamic content settle

        html = await page.content()
        await browser.close()

    OUTPUT_HTML.write_text(html, encoding="utf-8")
    print(f"[article_debug] Raw HTML saved → {OUTPUT_HTML} ({len(html):,} bytes)")

    # Also run it through the parser and save the result
    try:
        from src.scraper.skynews_parser_v2 import SkyNewsParser
        parser = SkyNewsParser()
        article = parser.parse_html(url, html)
        await parser.close()

        result_lines = [
            "=" * 70,
            "SKY NEWS ARABIA — ARTICLE PARSE RESULT",
            "=" * 70,
            f"URL:          {article.url}",
            f"Title:        {article.title}",
            f"Published:    {article.published_at}",
            f"Image URL:    {article.image_url}",
            f"Tags:         {', '.join(article.tags) if article.tags else '(none)'}",
            f"Hash:         {article.article_hash}",
            "",
            f"--- CONTENT ({len(article.full_content)} chars) ---",
            article.full_content[:3000] or "(no content extracted)",
            "",
            "--- METADATA ---",
            str(article.source_metadata),
        ]
        result_text = "\n".join(result_lines)
        OUTPUT_PARSED.write_text(result_text, encoding="utf-8")
        print(f"[article_debug] Parsed result saved → {OUTPUT_PARSED}")
        print()
        print(result_text[:800])

    except Exception as exc:
        print(f"[article_debug] Parser error: {exc}")
        print("              Raw HTML has been saved — inspect article_debug.html")


if __name__ == "__main__":
    target_url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    asyncio.run(main(target_url))
