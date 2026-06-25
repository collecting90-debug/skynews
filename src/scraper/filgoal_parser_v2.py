SKYNEWS_BASE = "https://www.skynewsarabia.com"
"""
src/scraper/filgoal_parser_v2.py
─────────────────────────────────
Production-grade FilGoal article extractor.
Uses httpx (no browser) for maximum speed.
Target latency: <2s per article.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup, Tag

SKYNEWS_BASE = "https://www.filgoal.com"

# ── Noise selectors to remove before parsing ──────────────────────────────────
NOISE_SELECTORS = [
    # Ads
    "ins", ".adsbygoogle", "[id^='div-gpt-ad']", ".ad-container",
    ".advertisement", ".ads", "[class*='ad-']", "[class*='ads-']",
    # Related articles
    ".related-articles", ".related-posts", ".related_articles",
    "[class*='related']", ".see-also",
    # Navigation / UI chrome
    "nav", "header", "footer", ".breadcrumb", ".pagination",
    ".social-share", ".share-buttons", "[class*='share']",
    # Tags section (we extract it separately, then remove)
    ".article-tags", ".tags-section",
    # Metadata blocks (teams, competition widgets)
    ".match-widget", ".team-widget", ".competition-widget",
    ".match-info", "[class*='widget']",
    # Comments / subscription
    ".comments-section", ".newsletter-signup", ".subscription",
    # Author bio boxes
    ".author-bio", ".author-box",
    # Scripts / styles
    "script", "style", "noscript",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ar,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


@dataclass
class ParsedArticle:
    url: str
    title: str
    full_content: str           # Clean merged paragraphs
    image_url: Optional[str]
    tags: list[str]
    published_at: Optional[datetime]
    article_hash: str           # SHA-256 of URL for deduplication
    source_metadata: dict = field(default_factory=dict)

    @property
    def content_preview(self) -> str:
        """First 300 chars for Telegram caption."""
        return self.full_content[:300].strip() + ("…" if len(self.full_content) > 300 else "")


class FilGoalParser:  # kept as alias — use SkyNewsParser for new code
    pass


class SkyNewsParser:
    """
    Fetches and parses a FilGoal article page.
    No Playwright — pure httpx + BeautifulSoup for maximum speed.
    """

    def __init__(self, timeout: int = 10):
        self._client = httpx.AsyncClient(
            headers=HEADERS,
            timeout=timeout,
            follow_redirects=True,
            http2=True,          # HTTP/2 for faster fetching
        )

    async def close(self):
        await self._client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.close()

    # ── Public API ────────────────────────────────────────────────────────────

    async def parse(self, url: str) -> ParsedArticle:
        html = await self._fetch(url)
        soup = BeautifulSoup(html, "lxml")

        # Extract before removing noise (tags, image exist outside article body)
        title = self._extract_title(soup)
        image_url = self._extract_image(soup)
        tags = self._extract_tags(soup)
        published_at = self._extract_date(soup)
        source_metadata = self._extract_metadata(soup)

        # Remove all noise elements
        self._strip_noise(soup)

        # Extract clean content
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

    # ── Fetching ─────────────────────────────────────────────────────────────

    async def _fetch(self, url: str) -> str:
        response = await self._client.get(url)
        response.raise_for_status()
        return response.text

    # ── Noise Removal ─────────────────────────────────────────────────────────

    def _strip_noise(self, soup: BeautifulSoup) -> None:
        """Remove all non-article elements in-place."""
        for selector in NOISE_SELECTORS:
            for el in soup.select(selector):
                el.decompose()

        # Also remove empty block elements
        for tag in soup.find_all(["div", "section", "aside"]):
            if tag.get_text(strip=True) == "":
                tag.decompose()

    # ── Content Extraction ────────────────────────────────────────────────────

    def _extract_content(self, soup: BeautifulSoup) -> str:
        """
        Find the article body and extract ALL <p> tags in order.
        Merges fragmented paragraphs into one coherent article.
        """
        # Try known article body selectors (most specific first)
        body_candidates = [
            soup.select_one(".article-body"),
            soup.select_one(".article-content"),
            soup.select_one(".post-content"),
            soup.select_one("[class*='article-body']"),
            soup.select_one("[class*='article-content']"),
            soup.select_one("article"),
        ]

        body = next((c for c in body_candidates if c is not None), None)

        if body is None:
            # Fallback: find the div with the most <p> tags
            body = self._find_content_rich_div(soup)

        if body is None:
            return ""

        return self._paragraphs_to_text(body)

    def _find_content_rich_div(self, soup: BeautifulSoup) -> Optional[Tag]:
        """Find the div containing the most meaningful paragraph text."""
        best: Optional[Tag] = None
        best_score = 0

        for div in soup.find_all(["div", "section"]):
            paragraphs = div.find_all("p", recursive=False)
            if not paragraphs:
                paragraphs = div.find_all("p")

            text_len = sum(len(p.get_text(strip=True)) for p in paragraphs)
            if text_len > best_score:
                best_score = text_len
                best = div

        return best if best_score > 100 else None

    def _paragraphs_to_text(self, body: Tag) -> str:
        """
        Extract all <p> tags from body in DOM order.
        Cleans whitespace and merges into readable text.
        """
        paragraphs = []

        for p in body.find_all("p"):
            # Skip <p> tags inside noise containers that survived stripping
            if self._is_inside_noise(p):
                continue

            text = p.get_text(separator=" ", strip=True)
            text = self._clean_text(text)

            # Skip very short or empty paragraphs (likely UI artifacts)
            if len(text) < 15:
                continue

            paragraphs.append(text)

        return "\n\n".join(paragraphs)

    def _is_inside_noise(self, tag: Tag) -> bool:
        """Check if a tag is nested inside a known noise container."""
        noise_classes = {
            "related", "tags", "share", "comment", "widget",
            "ad", "ads", "advertisement", "author", "sidebar",
        }
        for parent in tag.parents:
            if not isinstance(parent, Tag):
                continue
            raw_class = parent.get("class") or []
            classes = set(" ".join(str(c) for c in raw_class).lower().split())
            if classes & noise_classes:
                return True
        return False

    # ── Field Extractors ─────────────────────────────────────────────────────

    def _extract_title(self, soup: BeautifulSoup) -> str:
        # Try structured selectors first
        for sel in ["h1.article-title", "h1.post-title", "h1", ".article-title"]:
            el = soup.select_one(sel)
            if el:
                return self._clean_text(el.get_text())

        # Fallback to <title> tag (strip site name suffix)
        title_tag = soup.find("title")
        if title_tag:
            title = title_tag.get_text()
            # Remove "| FilGoal" or "- FilGoal" suffix
            title = re.sub(r"\s*[\|–\-]\s*فيلجول.*$", "", title)
            title = re.sub(r"\s*[\|–\-]\s*FilGoal.*$", "", title, flags=re.IGNORECASE)
            return self._clean_text(title)

        return ""

    def _extract_image(self, soup: BeautifulSoup) -> Optional[str]:
        # 1. Open Graph image (most reliable)
        og = soup.find("meta", property="og:image")
        if og and og.get("content"):
            return str(og["content"])

        # 2. Article hero image
        for sel in [
            ".article-image img", ".hero-image img",
            ".post-thumbnail img", "figure img",
            ".article-body img",
        ]:
            img = soup.select_one(sel)
            if img and img.get("src"):
                return urljoin(SKYNEWS_BASE, str(img["src"]))

        return None

    def _extract_tags(self, soup: BeautifulSoup) -> list[str]:
        """
        Extract from:
          <div class="tags"><a href="/tags/...">الدوري السعودي</a></div>
        """
        tags = []

        # Primary: tags div
        tags_container = soup.select_one(".tags, .article-tags, [class*='tags']")
        if tags_container:
            for a in tags_container.find_all("a", href=True):
                if "/tags/" in a["href"] or "/tag/" in a["href"]:
                    text = self._clean_text(a.get_text())
                    if text:
                        tags.append(text)

        # Fallback: meta keywords
        if not tags:
            keywords_meta = soup.find("meta", attrs={"name": "keywords"})
            if keywords_meta and keywords_meta.get("content"):
                tags = [k.strip() for k in str(keywords_meta["content"]).split(",") if k.strip()]

        return tags

    def _extract_date(self, soup: BeautifulSoup) -> Optional[datetime]:
        # 1. JSON-LD structured data (most reliable)
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                import json
                data = json.loads(script.string or "")
                date_str = data.get("datePublished") or data.get("dateModified")
                if date_str:
                    return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            except Exception:
                continue

        # 2. Open Graph
        og_time = soup.find("meta", property="article:published_time")
        if og_time and og_time.get("content"):
            try:
                return datetime.fromisoformat(str(og_time["content"]).replace("Z", "+00:00"))
            except Exception:
                pass

        # 3. <time> element
        time_el = soup.find("time")
        if time_el:
            dt = time_el.get("datetime") or time_el.get_text()
            try:
                return datetime.fromisoformat(str(dt).replace("Z", "+00:00"))
            except Exception:
                pass

        return None

    def _extract_metadata(self, soup: BeautifulSoup) -> dict:
        """Extract teams, competition, match info if present."""
        meta = {}

        # Open Graph basics
        for prop in ["og:title", "og:description", "og:url"]:
            el = soup.find("meta", property=prop)
            if el and el.get("content"):
                key = prop.split(":")[1]
                meta[key] = el["content"]

        return meta

    # ── Utilities ─────────────────────────────────────────────────────────────

    @staticmethod
    def _clean_text(text: str) -> str:
        """Normalize whitespace and strip zero-width chars."""
        text = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", text)  # Zero-width chars
        text = re.sub(r"\s+", " ", text)
        return text.strip()