
"""
src/scraper/filgoal_parser.py
──────────────────────────────
HTML parsing layer for FilGoal.com articles.

Structure discovered from real HTML:
  div.article
    div.title        ← title + date + author
    div.details[0]   ← hero image + caption
    div.details[1]   ← FULL ARTICLE CONTENT (p tags + noise)
      p              ← real article paragraphs ✓
      div.match-item ← match widget (noise) ✗
      div.externalHTML > blockquote.twitter-tweet ← tweets (noise) ✗
      div.tags       ← article tags ✓
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Optional
from urllib.parse import urljoin

import dateparser
from bs4 import BeautifulSoup, Tag
from loguru import logger

from src.core.models import RawArticle

FILGOAL_BASE = "https://www.filgoal.com"

# Noise inside div.details[1] to remove before extracting paragraphs
_NOISE_SELECTORS = [
    "div.externalHTML",          # Embedded tweets
    "blockquote.twitter-tweet",  # Tweets
    "div.match-item",            # Match score widget
    "div.team",                  # Team widget
    "div.ntva_box",              # Related articles box
    "script",
    "style",
    "noscript",
    "iframe",
    "ins",
    ".adsbygoogle",
]


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def parse_article_list(
    html: str,
    subcategory: str,
    base_url: str = FILGOAL_BASE,
) -> list[RawArticle]:
    """Parse the FilGoal articles listing page."""
    soup = BeautifulSoup(html, "lxml")
    articles: list[RawArticle] = []

    cards = soup.select("li a[href^='/articles/']")

    if not cards:
        logger.warning(
            f"No article cards found for subcategory={subcategory}"
        )
        return []

    for card in cards:
        try:
            article = _parse_card(card, subcategory, base_url)
            if article:
                articles.append(article)

        except Exception as exc:
            logger.warning(f"Failed to parse article card: {exc}")

    logger.debug(f"Parsed {len(articles)} articles from listing")
    return articles


def parse_article_detail(html: str, raw: RawArticle) -> RawArticle:
    """
    Parse a full FilGoal article detail page.
    Extracts FULL clean content from div.details[1].
    """
    soup = BeautifulSoup(html, "lxml")

    updates: dict = {}

    # ── Image ────────────────────────────────────────────────────────────────
    if not raw.image_url:
        image_url = _extract_image(soup)
        if image_url:
            updates["image_url"] = image_url

    # ── Date ─────────────────────────────────────────────────────────────────
    if not raw.publish_date:
        publish_date = _extract_date(soup)
        if publish_date:
            updates["publish_date"] = publish_date

    # ── Full content ────────────────────────────────────────────────────────
    content = _extract_full_content(soup)
    if content:
        updates["content"] = content

    if updates:
        raw = raw.model_copy(update=updates)

    return raw


# ─────────────────────────────────────────────────────────────────────────────
# Content extraction
# ─────────────────────────────────────────────────────────────────────────────

def _extract_full_content(soup: BeautifulSoup) -> str:
    """
    FilGoal structure:
      div.article > div.details (×2)
        [0] = hero image + caption
        [1] = full article content

    Strategy:
      1. Select div.details containing most paragraphs
      2. Remove noise
      3. Extract all <p> tags
      4. Merge clean text
    """

    article_div = soup.select_one("div.article")

    if not article_div:
        return _fallback_content(soup)

    all_details = article_div.select("div.details")

    content_div: Optional[Tag] = None
    best_p_count = 0

    for d in all_details:
        p_count = len(d.find_all("p"))

        if p_count > best_p_count:
            best_p_count = p_count
            content_div = d

    if not content_div:
        return _fallback_content(soup)

    # Remove noise
    for selector in _NOISE_SELECTORS:
        for el in content_div.select(selector):
            el.decompose()

    paragraphs: list[str] = []

    for p in content_div.find_all("p"):
        cls = " ".join(p.get("class") or [])

        # Skip captions
        if "caption" in cls:
            continue

        text = _clean_text(
            p.get_text(separator=" ", strip=True)
        )

        if len(text) < 20:
            continue

        paragraphs.append(text)

    return "\n\n".join(paragraphs)


def _fallback_content(soup: BeautifulSoup) -> str:
    """Fallback: choose div with largest direct <p> content."""

    best: Optional[Tag] = None
    best_score = 0

    for div in soup.find_all("div"):
        ps = div.find_all("p", recursive=False)

        score = sum(
            len(p.get_text(strip=True))
            for p in ps
        )

        if score > best_score:
            best_score = score
            best = div

    if not best or best_score < 100:
        return ""

    for selector in _NOISE_SELECTORS:
        for el in best.select(selector):
            el.decompose()

    paragraphs: list[str] = []

    for p in best.find_all("p"):
        text = _clean_text(
            p.get_text(separator=" ", strip=True)
        )

        if len(text) >= 20:
            paragraphs.append(text)

    return "\n\n".join(paragraphs)


# ─────────────────────────────────────────────────────────────────────────────
# Extractors
# ─────────────────────────────────────────────────────────────────────────────

def _extract_image(soup: BeautifulSoup) -> Optional[str]:
    """Extract article hero image."""

    # Open Graph image
    og = soup.find("meta", property="og:image")

    if og:
        content = og.get("content")

        if content:
            return str(content)

    # Fallback image
    first_img = soup.select_one(
        "div.article div.details img"
    )

    if first_img:
        src = (
            first_img.get("data-src")
            or first_img.get("src")
            or ""
        )

        if src and "placeholder" not in str(src):
            return _absolute_url(str(src), FILGOAL_BASE)

    return None


def _extract_date(soup: BeautifulSoup) -> Optional[datetime]:
    """Extract publish date."""

    # JSON-LD
    for script in soup.find_all(
        "script",
        type="application/ld+json",
    ):
        try:
            data = json.loads(script.string or "")

            if isinstance(data, dict):
                date_str = (
                    data.get("datePublished")
                    or data.get("dateModified")
                )

                if date_str:
                    return datetime.fromisoformat(
                        str(date_str).replace("Z", "+00:00")
                    )

        except Exception:
            continue

    # OpenGraph
    og = soup.find(
        "meta",
        property="article:published_time",
    )

    if og:
        content = og.get("content")

        if content:
            try:
                return datetime.fromisoformat(
                    str(content).replace("Z", "+00:00")
                )

            except Exception:
                pass

    # FilGoal visible date
    title_div = soup.select_one("div.title")

    if title_div:
        for span in title_div.find_all(
            ["span", "p", "div"]
        ):
            text = span.get_text(strip=True)

            parsed = _parse_arabic_date(text)

            if parsed:
                return parsed

    # <time>
    time_el = soup.find("time")

    if time_el:
        dt = (
            time_el.get("datetime")
            or time_el.get_text(strip=True)
        )

        try:
            return datetime.fromisoformat(
                str(dt).replace("Z", "+00:00")
            )

        except Exception:
            return _parse_arabic_date(str(dt))

    return None


# ─────────────────────────────────────────────────────────────────────────────
# Listing card parser
# ─────────────────────────────────────────────────────────────────────────────

def _parse_card(
    card: Tag,
    subcategory: str,
    base_url: str,
) -> Optional[RawArticle]:

    href = str(card.get("href", ""))

    if not href:
        return None

    url = _absolute_url(href, base_url)

    title_tag = card.select_one("h6")

    if not title_tag:
        return None

    title = title_tag.get_text(strip=True)

    if not title:
        return None

    summary_tag = card.select_one("p")

    summary = (
        summary_tag.get_text(strip=True)[:500]
        if summary_tag
        else None
    )

    date_tag = card.select_one("span")

    publish_date = (
        _parse_arabic_date(
            date_tag.get_text(strip=True)
        )
        if date_tag
        else None
    )

    image_url: Optional[str] = None

    img = card.select_one("img")

    if img:
        src = (
            str(img.get("data-src"))
            or str(img.get("src"))
            or ""
        )

        if src and "placeholder" not in src:
            image_url = _absolute_url(src, base_url)

    return RawArticle(
        title=title,
        url=url,
        image_url=image_url,
        summary=summary,
        publish_date=publish_date,
        subcategory=subcategory,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Utilities
# ─────────────────────────────────────────────────────────────────────────────

def _parse_arabic_date(
    date_str: str,
) -> Optional[datetime]:

    if not date_str or len(date_str) < 5:
        return None

    try:
        return dateparser.parse(
            date_str,
            languages=["ar"],
            settings={
                "PREFER_DAY_OF_MONTH": "first",
                "TIMEZONE": "Africa/Cairo",
                "RETURN_AS_TIMEZONE_AWARE": False,
            },
        )

    except Exception:
        return None


def _clean_text(text: str) -> str:
    """Normalize whitespace and invisible chars."""

    text = re.sub(
        r"[\u200b\u200c\u200d\ufeff]",
        "",
        text,
    )

    text = re.sub(r"\s+", " ", text)

    return text.strip()


def _absolute_url(href: str, base: str) -> str:
    """Convert relative URL to absolute."""

    if href.startswith("http"):
        return href

    if href.startswith("//"):
        return "https:" + href

    return urljoin(base, href)

