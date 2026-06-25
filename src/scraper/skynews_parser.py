"""
src/scraper/skynews_parser.py  ← replace existing file with this
──────────────────────────────────────────────────────────────────
Fixes applied (v2-fix):

BUG 1 — Image = default_img.jpg
  • Added 'default_img' to _is_placeholder()
  • parse_article_detail() now ALWAYS overwrites image if it's a placeholder,
    not only when image_url is None.

BUG 2 — content / summary = null
  • Sky News Arabia is an Angular SPA.  The real article data (body, summary,
    image, date) lives inside the `data-sna-init` JSON attribute on <body>.
  • New function _extract_from_sna_init() reads that JSON first.
  • CSS selectors (div.article-body …) are kept as fallback only.

BUG 3 — publish_date = 00:00:00 UTC
  • JSON-LD on Sky News Arabia uses Eastern Arabic-Indic digits
    (٢٠٢٦-٠٥-٢٨T١٠:٥٥:١٣+0400).  datetime.fromisoformat() cannot parse them.
  • New helper _normalize_arabic_digits() converts them to ASCII digits first.
  • Primary date now comes from data-sna-init["date"] which is plain ISO.
"""

from __future__ import annotations

import html
import json
import re
from datetime import datetime
from typing import Optional
from urllib.parse import urljoin

import dateparser
from bs4 import BeautifulSoup, Tag
from loguru import logger

from src.core.models import RawArticle

SKYNEWS_BASE = "https://www.skynewsarabia.com"
SKYNEWS_SPORT_URL = "https://www.skynewsarabia.com/sport"

# Default image served when no real image is available
_DEFAULT_IMAGE_PATH = "18.2.1/img/default_img.jpg"

_NOISE_SELECTORS = [
    "ins", ".adsbygoogle", "[id^='div-gpt-ad']", ".ad-container",
    ".advertisement", "[class*='ad-']", "[class*='ads-']", ".dfp",
    "blockquote.twitter-tweet", ".instagram-media", ".fb-post",
    "div.externalHTML", ".social-embed", "[class*='social-share']",
    ".related-articles", ".related-stories", "[class*='related']",
    ".more-stories", ".see-also", ".read-also",
    "nav", "header", "footer", ".breadcrumb", ".pagination",
    ".share-buttons", ".sharing-tools", "[class*='share']",
    ".newsletter-signup", ".subscription-box",
    ".author-bio", ".author-box", ".reporter-card",
    ".article-tags", ".tags-section", ".story-tags",
    ".match-widget", ".sport-widget", ".score-widget",
    "script", "style", "noscript", "iframe",
]

# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def parse_article_list(
    html_text: str,
    subcategory: str = "رياضة",
    base_url: str = SKYNEWS_BASE,
) -> list[RawArticle]:
    """Parse the Sky News Arabia sport listing page → list of article stubs."""
    soup = BeautifulSoup(html_text, "lxml")
    articles: list[RawArticle] = []

    card_selectors = [
        "article.story-card a[href*='/sport/']",
        "article a[href*='/sport/']",
        ".story-card a[href*='/sport/']",
        ".article-card a[href*='/sport/']",
        ".content-card a[href*='/sport/']",
        "a.card-link[href*='/sport/']",
        "a[href*='/sport/'][href]",
    ]

    found_anchors: list[Tag] = []
    for sel in card_selectors:
        candidates = soup.select(sel)
        if candidates:
            found_anchors = candidates
            logger.debug(f"Listing selector matched: {sel!r} → {len(candidates)} items")
            break

    if not found_anchors:
        found_anchors = [
            a for a in soup.find_all("a", href=True)
            if _is_sport_article_url(str(a.get("href", "")))
        ]
        logger.debug(f"Ultra-fallback anchor scan: {len(found_anchors)} items")

    seen_urls: set[str] = set()
    for anchor in found_anchors:
        try:
            article = _parse_listing_anchor(anchor, subcategory, base_url)
            if article and article.url not in seen_urls:
                seen_urls.add(article.url)
                articles.append(article)
        except Exception as exc:
            logger.warning(f"Failed to parse listing card: {exc}")

    logger.debug(f"Parsed {len(articles)} article stubs from listing")
    return articles


def parse_article_detail(html_text: str, stub: RawArticle) -> RawArticle:
    """
    Enrich a stub with full content from the article detail page.

    FIX: Always overwrites image/date/content from the detail page.
    Primary source: data-sna-init JSON embedded in <body>.
    Fallback: standard meta tags + CSS selectors.
    """
    soup = BeautifulSoup(html_text, "lxml")
    updates: dict = {}

    # ── 1. Try data-sna-init JSON (most reliable — has everything) ────────────
    sna_data = _extract_from_sna_init(soup)

    if sna_data:
        # Title
        title = _clean_text(sna_data.get("headline") or sna_data.get("title") or "")
        if title:
            updates["title"] = title

        # Summary
        summary = _clean_text(sna_data.get("summary") or "")
        if summary:
            updates["summary"] = summary

        # Full content (body is HTML — strip to text)
        body_html = sna_data.get("body") or ""
        if body_html:
            content = _html_to_text(body_html)
            if content:
                updates["content"] = content

        # Image
        image_url = _build_sna_image_url(sna_data.get("mediaAsset"))
        if image_url:
            updates["image_url"] = image_url

        # Date — data-sna-init["date"] is plain ISO (ASCII digits)
        date_str = sna_data.get("date") or sna_data.get("revision") or ""
        if date_str:
            parsed_date = _parse_iso_date(date_str)
            if parsed_date:
                updates["publish_date"] = parsed_date

    # ── 2. Fallback for any field still missing ───────────────────────────────
    if "title" not in updates:
        title = _extract_title(soup)
        if title:
            updates["title"] = title

    if "image_url" not in updates or _is_placeholder(updates.get("image_url", "")):
        image_url = _extract_image(soup)
        if image_url and not _is_placeholder(image_url):
            updates["image_url"] = image_url

    if "publish_date" not in updates:
        publish_date = _extract_date(soup)
        if publish_date:
            updates["publish_date"] = publish_date

    if "content" not in updates:
        content = _extract_full_content(soup)
        if content:
            updates["content"] = content

    # ── FIX: Always overwrite stub image if it's a placeholder ───────────────
    if "image_url" not in updates and _is_placeholder(stub.image_url or ""):
        image_url = _extract_image(soup)
        if image_url and not _is_placeholder(image_url):
            updates["image_url"] = image_url

    if updates:
        stub = stub.model_copy(update=updates)

    return stub


# ─────────────────────────────────────────────────────────────────────────────
# FIX: data-sna-init extraction (PRIMARY DATA SOURCE)
# ─────────────────────────────────────────────────────────────────────────────

def _extract_from_sna_init(soup: BeautifulSoup) -> Optional[dict]:
    """
    Sky News Arabia embeds all article data as JSON in the `data-sna-init`
    attribute on <body>.  Example (truncated):

        <body data-sna-init='{"initData": {"id":"1871933",
          "headline":"نيمار يثير القلق...",
          "summary":"...",
          "body":"<p>...</p>",
          "date":"2026-05-28T06:55:13Z",
          "mediaAsset":{"imageUrl":"/v1/2026/.../1-1871931.JPG", ...},
          ...}}'>

    Returns the inner "initData" dict, or None if not found.
    """
    body_tag = soup.find("body")
    if not body_tag:
        return None

    raw = body_tag.get("data-sna-init")
    if not raw:
        return None

    try:
        # Attribute value is HTML-entity-encoded JSON
        decoded = html.unescape(str(raw))
        outer = json.loads(decoded)
        # Data is nested under "initData" key
        return outer.get("initData") or outer
    except Exception as exc:
        logger.warning(f"data-sna-init parse error: {exc}")
        return None


def _build_sna_image_url(
    media_asset: Optional[dict],
    width: int = 1200,
    height: int = 630,
) -> Optional[str]:
    """
    mediaAsset.imageUrl looks like:
      /v1/2026/05/28/1871931/{width}/{height}/1-1871931.JPG

    Replace the template placeholders with concrete dimensions.
    """
    if not media_asset:
        return None
    raw_url = media_asset.get("imageUrl") or ""
    if not raw_url:
        return None

    # Replace {width} and {height} placeholders
    resolved = raw_url.replace("{width}", str(width)).replace("{height}", str(height))

    if resolved.startswith("http"):
        return resolved
    # Build full URL using skynewsarabia.com image CDN
    return f"https://www.skynewsarabia.com/images{resolved}"


def _html_to_text(body_html: str) -> str:
    """Strip HTML tags from article body and return clean paragraphs."""
    # Unescape if double-encoded
    body_html = html.unescape(body_html)
    soup = BeautifulSoup(body_html, "lxml")

    # Remove noise
    for sel in ["script", "style", "noscript", "sna"]:
        for el in soup.select(sel):
            el.decompose()

    paragraphs: list[str] = []
    for p in soup.find_all("p"):
        text = _clean_text(p.get_text(separator=" ", strip=True))
        if len(text) >= 20:
            paragraphs.append(text)

    return "\n\n".join(paragraphs)


# ─────────────────────────────────────────────────────────────────────────────
# FIX: Date parsing with Arabic-Indic digit support
# ─────────────────────────────────────────────────────────────────────────────

_ARABIC_INDIC_MAP = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


def _normalize_arabic_digits(text: str) -> str:
    """Convert Eastern Arabic-Indic digits (٠-٩) to ASCII digits (0-9)."""
    return text.translate(_ARABIC_INDIC_MAP)


def _parse_iso_date(date_str: str) -> Optional[datetime]:
    """
    Parse ISO date strings that may contain Arabic-Indic digits.
    Example: '٢٠٢٦-٠٥-٢٨T١٠:٥٥:١٣+0400' → datetime(2026, 5, 28, 10, 55, 13)
    """
    if not date_str:
        return None
    normalized = _normalize_arabic_digits(date_str)
    try:
        return datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Title extraction
# ─────────────────────────────────────────────────────────────────────────────

def _extract_title(soup: BeautifulSoup) -> str:
    for sel in ["article h1", "h1.article-title", "h1.story-title", "h1.post-title", "h1"]:
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
        text = re.sub(r"\s*[|\-–]\s*Sky News Arabia.*$", "", text, flags=re.IGNORECASE)
        return _clean_text(text)

    return ""


# ─────────────────────────────────────────────────────────────────────────────
# Content extraction (fallback)
# ─────────────────────────────────────────────────────────────────────────────

def _extract_full_content(soup: BeautifulSoup) -> str:
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

    body: Optional[Tag] = next((c for c in body_candidates if c is not None), None)

    if body is None:
        body = _find_content_rich_div(soup)

    if body is None:
        return ""

    for sel in _NOISE_SELECTORS:
        for el in body.select(sel):
            el.decompose()

    return _paragraphs_to_text(body)


def _find_content_rich_div(soup: BeautifulSoup) -> Optional[Tag]:
    best: Optional[Tag] = None
    best_score = 0
    for div in soup.find_all(["div", "section"]):
        ps = div.find_all("p")
        score = sum(len(p.get_text(strip=True)) for p in ps)
        if score > best_score:
            best_score = score
            best = div
    return best if best_score > 150 else None


def _paragraphs_to_text(body: Tag) -> str:
    paragraphs: list[str] = []
    for p in body.find_all("p"):
        if _is_inside_noise(p):
            continue
        cls = " ".join(p.get("class") or []).lower()
        if "caption" in cls or "credit" in cls:
            continue
        text = _clean_text(p.get_text(separator=" ", strip=True))
        if len(text) < 20:
            continue
        paragraphs.append(text)
    return "\n\n".join(paragraphs)


def _is_inside_noise(tag: Tag) -> bool:
    noise_class_fragments = {
        "related", "tags", "share", "comment", "widget",
        "ad", "ads", "advertisement", "author", "sidebar",
        "social", "newsletter", "subscription",
    }
    for parent in tag.parents:
        if not isinstance(parent, Tag):
            continue
        raw_cls = parent.get("class") or []
        cls_str = " ".join(str(c) for c in raw_cls).lower()
        if any(f in cls_str for f in noise_class_fragments):
            return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Image extraction (fallback)
# ─────────────────────────────────────────────────────────────────────────────

def _extract_image(soup: BeautifulSoup) -> Optional[str]:
    og = soup.find("meta", property="og:image")
    if og and og.get("content"):
        url = str(og["content"])
        if not _is_placeholder(url):
            return url

    tw = soup.find("meta", attrs={"name": "twitter:image"})
    if tw and tw.get("content"):
        url = str(tw["content"])
        if not _is_placeholder(url):
            return url

    for sel in [
        ".article-image img", ".story-image img", ".hero-image img",
        ".post-thumbnail img", "figure.main-image img", "figure img", "article img",
    ]:
        img = soup.select_one(sel)
        if img:
            src = str(img.get("data-src") or img.get("data-lazy-src") or img.get("src") or "")
            if src and not _is_placeholder(src):
                return _absolute_url(src, SKYNEWS_BASE)

    return None


# ─────────────────────────────────────────────────────────────────────────────
# Date extraction (fallback) — now with Arabic-Indic digit fix
# ─────────────────────────────────────────────────────────────────────────────

def _extract_date(soup: BeautifulSoup) -> Optional[datetime]:
    # 1. JSON-LD — fix Arabic-Indic digits before parsing
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            if isinstance(data, list):
                data = data[0] if data else {}
            date_str = data.get("datePublished") or data.get("dateModified")
            if date_str:
                parsed = _parse_iso_date(str(date_str))  # handles Arabic digits
                if parsed:
                    return parsed
        except Exception:
            continue

    # 2. Open Graph article:published_time
    og = soup.find("meta", property="article:published_time")
    if og and og.get("content"):
        parsed = _parse_iso_date(str(og["content"]))
        if parsed:
            return parsed

    # 3. <time> element
    time_el = soup.find("time")
    if time_el:
        dt_attr = time_el.get("datetime") or time_el.get_text(strip=True)
        parsed = _parse_iso_date(str(dt_attr))
        if parsed:
            return parsed
        return _parse_arabic_date(str(dt_attr))

    # 4. Visible date elements
    for sel in [
        ".article-date", ".story-date", ".publish-date",
        ".date", "span.date", "div.date", "[class*='date']",
    ]:
        el = soup.select_one(sel)
        if el:
            parsed = _parse_arabic_date(el.get_text(strip=True))
            if parsed:
                return parsed

    return None


def _extract_tags(soup: BeautifulSoup) -> list[str]:
    tags: list[str] = []
    for sel in [".article-tags a", ".story-tags a", ".tags a", "ul.tags li a", "[class*='tags'] a"]:
        for a in soup.select(sel):
            text = _clean_text(a.get_text())
            if text and text not in tags:
                tags.append(text)

    if not tags:
        meta = soup.find("meta", attrs={"name": "keywords"})
        if meta and meta.get("content"):
            tags = [k.strip() for k in str(meta["content"]).split(",") if k.strip()]

    return tags[:10]


# ─────────────────────────────────────────────────────────────────────────────
# Listing card parser
# ─────────────────────────────────────────────────────────────────────────────

def _parse_listing_anchor(
    anchor: Tag,
    subcategory: str,
    base_url: str,
) -> Optional[RawArticle]:
    href = str(anchor.get("href", ""))
    if not href or not _is_sport_article_url(href):
        return None

    url = _absolute_url(href, base_url)

    title_el = (
        anchor.select_one("h1, h2, h3, h4, h5, h6")
        or anchor.select_one(".story-title, .article-title, .card-title, .title")
    )
    title = _clean_text(title_el.get_text() if title_el else anchor.get_text())
    if not title or len(title) < 5:
        return None

    summary_el = anchor.select_one("p, .summary, .excerpt, .description")
    summary = _clean_text(summary_el.get_text())[:500] if summary_el else None

    date_el = anchor.select_one("time, .date, span.date, [class*='date']")
    publish_date: Optional[datetime] = None
    if date_el:
        dt_attr = date_el.get("datetime") or date_el.get_text(strip=True)
        publish_date = _parse_iso_date(str(dt_attr)) or _parse_arabic_date(str(dt_attr))

    image_url: Optional[str] = None
    img = anchor.select_one("img")
    if not img and anchor.parent:
        img = anchor.parent.select_one("img")

    if img:
        src = str(img.get("data-src") or img.get("data-lazy-src") or img.get("src") or "")
        if src and not _is_placeholder(src):
            image_url = _absolute_url(src, base_url)

    return RawArticle(
        title=title,
        url=url,
        image_url=image_url,
        summary=summary,
        publish_date=publish_date,
        subcategory=subcategory,
        category="Sport",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Utilities
# ─────────────────────────────────────────────────────────────────────────────

def _is_sport_article_url(href: str) -> bool:
    if not href or "/sport/" not in href:
        return False
    stripped = href.rstrip("/")
    if stripped.endswith("/sport"):
        return False
    parts = href.split("/sport/")
    if len(parts) < 2:
        return False
    slug = parts[-1].split("/")[0].split("?")[0].split("#")[0]
    return bool(re.search(r"\d", slug))


def _is_placeholder(src: str) -> bool:
    """
    FIX: Added 'default_img' to catch the Sky News Arabia default image
    served when no real image is available.
    """
    if not src:
        return True
    placeholder_patterns = [
        "placeholder", "blank.gif", "spacer", "1x1", "pixel",
        "data:image", "loading", "lazy-placeholder",
        "default_img",      # ← FIX: Sky News Arabia default fallback image
    ]
    src_lower = src.lower()
    return any(p in src_lower for p in placeholder_patterns)


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


def _absolute_url(href: str, base: str) -> str:
    if href.startswith("http"):
        return href
    if href.startswith("//"):
        return "https:" + href
    return urljoin(base, href)