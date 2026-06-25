"""
src/scraper/skynews_parser_v2.py
──────────────────────────────────
Production-grade Sky News Arabia article fetcher and parser.
Uses httpx (no browser) — maximum speed, ~1-2s per article.

Sky News Arabia article URL pattern:
  https://www.skynewsarabia.com/sport/{numeric-id}-{arabic-slug}

This module provides:
  • SkyNewsParser  — async fetch + parse of a single article URL
  • ParsedArticle  — dataclass returned by SkyNewsParser.parse()
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from urllib.parse import urljoin

import dateparser
import httpx
from bs4 import BeautifulSoup, Tag
from loguru import logger

SKYNEWS_BASE = "https://www.skynewsarabia.com"

# ── Noise selectors removed before content extraction ─────────────────────────
_NOISE_SELECTORS = [
    "ins", ".adsbygoogle", "[id^='div-gpt-ad']", ".ad-container",
    ".advertisement", "[class*='ad-']", "[class*='ads-']", ".dfp",
    ".related-articles", ".related-stories", "[class*='related']",
    ".more-stories", ".see-also", ".read-also",
    "nav", "header", "footer", ".breadcrumb", ".pagination",
    ".share-buttons", ".sharing-tools", "[class*='share']",
    ".newsletter-signup", ".subscription-box",
    ".author-bio", ".author-box", ".reporter-card",
    ".article-tags", ".tags-section", ".story-tags",
    ".match-widget", ".sport-widget", ".score-widget",
    "blockquote.twitter-tweet", ".instagram-media", ".fb-post",
    "div.externalHTML", ".social-embed",
    "script", "style", "noscript", "iframe",
]

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ar,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.skynewsarabia.com/sport",
}


@dataclass
class ParsedArticle:
    url: str
    title: str
    full_content: str
    image_url: Optional[str]
    tags: list[str]
    published_at: Optional[datetime]
    article_hash: str
    source_metadata: dict = field(default_factory=dict)

    @property
    def content_preview(self) -> str:
        """First 300 chars — useful for Telegram caption."""
        return self.full_content[:300].strip() + (
            "…" if len(self.full_content) > 300 else ""
        )


class SkyNewsParser:
    """
    Fetches and parses a single Sky News Arabia article page.
    No Playwright — pure httpx + BeautifulSoup for maximum speed.
    """

    def __init__(self, timeout: int = 15) -> None:
        self._client = httpx.AsyncClient(
            headers=_HEADERS,
            timeout=timeout,
            follow_redirects=True,
            http2=True,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "SkyNewsParser":
        return self

    async def __aexit__(self, *_) -> None:
        await self.close()

    # ── Public API ─────────────────────────────────────────────────────────────

    async def parse(self, url: str) -> ParsedArticle:
        html = await self._fetch(url)
        return self.parse_html(url, html)

    def parse_html(self, url: str, html: str) -> ParsedArticle:
        """Parse pre-fetched HTML (useful for testing and debug tools)."""
        soup = BeautifulSoup(html, "lxml")

        # Extract metadata BEFORE stripping noise (tags/image live outside body)
        title = self._extract_title(soup)
        image_url = self._extract_image(soup)
        tags = self._extract_tags(soup)
        published_at = self._extract_date(soup)
        source_metadata = self._extract_metadata(soup)

        # Strip noise elements in place
        self._strip_noise(soup)

        # Extract clean body text
        full_content = self._extract_content(soup)

        return ParsedArticle(
            url=url,
            title=title,
            full_content=full_content,
            image_url=image_url,
            tags=tags,
            published_at=published_at,
            article_hash=hashlib.sha256(url.encode()).hexdigest(),
            source_metadata=source_metadata,
        )

    # ── Fetching ───────────────────────────────────────────────────────────────

    async def _fetch(self, url: str) -> str:
        response = await self._client.get(url)
        response.raise_for_status()
        return response.text

    # ── Noise removal ──────────────────────────────────────────────────────────

    def _strip_noise(self, soup: BeautifulSoup) -> None:
        for selector in _NOISE_SELECTORS:
            for el in soup.select(selector):
                el.decompose()
        # Remove empty block elements
        for tag in soup.find_all(["div", "section", "aside"]):
            if tag.get_text(strip=True) == "":
                tag.decompose()

    # ── Content extraction ─────────────────────────────────────────────────────

    def _extract_content(self, soup: BeautifulSoup) -> str:
        # Priority selector list for Sky News Arabia article body
        body_candidates = [
            soup.select_one("div.article-body"),
            soup.select_one("div.story-body"),
            soup.select_one("div.news-body"),
            soup.select_one("div.article-content"),
            soup.select_one("div.story-content"),
            soup.select_one("[class*='article-body']"),
            soup.select_one("[class*='story-body']"),
            soup.select_one("[class*='article-content']"),
            soup.select_one("[class*='story-content']"),
            soup.select_one("article"),
        ]

        body: Optional[Tag] = next(
            (c for c in body_candidates if c is not None), None
        )

        if body is None:
            body = self._find_content_rich_div(soup)

        if body is None:
            return ""

        return self._paragraphs_to_text(body)

    def _find_content_rich_div(self, soup: BeautifulSoup) -> Optional[Tag]:
        best: Optional[Tag] = None
        best_score = 0
        for div in soup.find_all(["div", "section"]):
            ps = div.find_all("p")
            score = sum(len(p.get_text(strip=True)) for p in ps)
            if score > best_score:
                best_score = score
                best = div
        return best if best_score > 150 else None

    def _paragraphs_to_text(self, body: Tag) -> str:
        paragraphs: list[str] = []
        for p in body.find_all("p"):
            if self._is_inside_noise(p):
                continue
            cls = " ".join(p.get("class") or []).lower()
            if "caption" in cls or "credit" in cls:
                continue
            text = _clean_text(p.get_text(separator=" ", strip=True))
            if len(text) < 20:
                continue
            paragraphs.append(text)
        return "\n\n".join(paragraphs)

    def _is_inside_noise(self, tag: Tag) -> bool:
        noise_fragments = {
            "related", "tags", "share", "comment", "widget",
            "ad", "ads", "advertisement", "author", "sidebar",
            "social", "newsletter", "subscription",
        }
        for parent in tag.parents:
            if not isinstance(parent, Tag):
                continue
            raw_cls = parent.get("class") or []
            cls_str = " ".join(str(c) for c in raw_cls).lower()
            if any(f in cls_str for f in noise_fragments):
                return True
        return False

    # ── Field extractors ───────────────────────────────────────────────────────

    def _extract_title(self, soup: BeautifulSoup) -> str:
        for sel in [
            "article h1",
            "h1.article-title",
            "h1.story-title",
            "h1.post-title",
            "h1",
        ]:
            el = soup.select_one(sel)
            if el:
                return _clean_text(el.get_text())

        og = soup.find("meta", property="og:title")
        if og and og.get("content"):
            return _clean_text(str(og["content"]))

        title_tag = soup.find("title")
        if title_tag:
            text = title_tag.get_text()
            text = re.sub(r"\s*[|\-–]\s*سكاي نيوز عربية.*$", "", text)
            text = re.sub(
                r"\s*[|\-–]\s*Sky News Arabia.*$", "", text, flags=re.IGNORECASE
            )
            return _clean_text(text)

        return ""

    def _extract_image(self, soup: BeautifulSoup) -> Optional[str]:
        # 1. Open Graph (most reliable)
        og = soup.find("meta", property="og:image")
        if og and og.get("content"):
            return str(og["content"])

        # 2. Twitter card image
        tw = soup.find("meta", attrs={"name": "twitter:image"})
        if tw and tw.get("content"):
            return str(tw["content"])

        # 3. Article hero image selectors
        for sel in [
            ".article-image img",
            ".story-image img",
            ".hero-image img",
            ".post-thumbnail img",
            "figure.main-image img",
            "figure img",
            "article img",
        ]:
            img = soup.select_one(sel)
            if img:
                src = str(
                    img.get("data-src")
                    or img.get("data-lazy-src")
                    or img.get("src")
                    or ""
                )
                if src and not _is_placeholder(src):
                    return urljoin(SKYNEWS_BASE, src)

        return None

    def _extract_tags(self, soup: BeautifulSoup) -> list[str]:
        tags: list[str] = []
        for sel in [
            ".article-tags a",
            ".story-tags a",
            ".tags a",
            "ul.tags li a",
            "[class*='tags'] a",
        ]:
            for a in soup.select(sel):
                text = _clean_text(a.get_text())
                if text and text not in tags:
                    tags.append(text)

        if not tags:
            meta = soup.find("meta", attrs={"name": "keywords"})
            if meta and meta.get("content"):
                tags = [
                    k.strip()
                    for k in str(meta["content"]).split(",")
                    if k.strip()
                ]

        return tags[:10]

    def _extract_date(self, soup: BeautifulSoup) -> Optional[datetime]:
        # 1. JSON-LD
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                if isinstance(data, list):
                    data = data[0] if data else {}
                date_str = data.get("datePublished") or data.get("dateModified")
                if date_str:
                    return datetime.fromisoformat(
                        str(date_str).replace("Z", "+00:00")
                    )
            except Exception:
                continue

        # 2. Open Graph article:published_time
        og = soup.find("meta", property="article:published_time")
        if og and og.get("content"):
            try:
                return datetime.fromisoformat(
                    str(og["content"]).replace("Z", "+00:00")
                )
            except Exception:
                pass

        # 3. <time> element
        time_el = soup.find("time")
        if time_el:
            dt = time_el.get("datetime") or time_el.get_text(strip=True)
            try:
                return datetime.fromisoformat(str(dt).replace("Z", "+00:00"))
            except Exception:
                return _parse_arabic_date(str(dt))

        # 4. Visible date elements
        for sel in [
            ".article-date", ".story-date", ".publish-date",
            "span.date", "div.date", "[class*='date']",
        ]:
            el = soup.select_one(sel)
            if el:
                parsed = _parse_arabic_date(el.get_text(strip=True))
                if parsed:
                    return parsed

        return None

    def _extract_metadata(self, soup: BeautifulSoup) -> dict:
        meta: dict = {}
        for prop in ["og:title", "og:description", "og:url", "og:site_name"]:
            el = soup.find("meta", property=prop)
            if el and el.get("content"):
                key = prop.split(":")[-1]
                meta[key] = str(el["content"])
        # Article section / author
        section_el = soup.find("meta", property="article:section")
        if section_el and section_el.get("content"):
            meta["section"] = str(section_el["content"])
        author_el = soup.find("meta", property="article:author")
        if author_el and author_el.get("content"):
            meta["author"] = str(author_el["content"])
        return meta


# ── Utilities ──────────────────────────────────────────────────────────────────

def _parse_arabic_date(date_str: str) -> Optional[datetime]:
    if not date_str or len(date_str) < 5:
        return None
    try:
        return dateparser.parse(
            date_str,
            languages=["ar"],
            settings={
                "PREFER_DAY_OF_MONTH": "first",
                "TIMEZONE": "Asia/Riyadh",
                "RETURN_AS_TIMEZONE_AWARE": False,
            },
        )
    except Exception:
        return None


def _clean_text(text: str) -> str:
    text = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _is_placeholder(src: str) -> bool:
    bad = ["placeholder", "blank.gif", "spacer", "1x1", "pixel",
           "data:image", "loading", "lazy-placeholder"]
    src_lower = src.lower()
    return any(p in src_lower for p in bad)
