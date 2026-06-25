"""
src/telegram/formatter.py
──────────────────────────
Professional Telegram message formatter — ESPN/BBC Sport style.
No emojis. Clean HTML formatting only.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

from src.scraper.skynews_parser_v2 import ParsedArticle


class TelegramFormatter:
    """
    Formats a ParsedArticle into a polished Telegram HTML message.

    Output style: professional news card — title, category, content,
    published time, tags, and source link.
    """

    MAX_CONTENT_LENGTH = 800   # Telegram caption limit awareness

    @classmethod
    def format(cls, article: ParsedArticle) -> "FormattedMessage":
        category = cls._detect_category(article)
        content_preview = cls._truncate(article.full_content, cls.MAX_CONTENT_LENGTH)
        published = cls._format_date(article.published_at)
        tags_line = cls._format_tags(article.tags)

        # Build the message — clean HTML, no emojis
        lines = [
            f"<b>{cls._escape(article.title)}</b>",
            "",
            f"<i>{category}</i>",
            "",
            cls._escape(content_preview),
        ]

        if tags_line:
            lines += ["", f"<b>الوسوم:</b> {tags_line}"]

        if published:
            lines += ["", f"<b>نُشر في:</b> {published}"]

        lines += [
            "",
            f'<a href="{article.url}">اقرأ المقال كاملاً</a>',
        ]

        return FormattedMessage(
            text="\n".join(lines),
            image_url=article.image_url,
            parse_mode="HTML",
        )

    @classmethod
    def format_news_card(cls, article: ParsedArticle) -> str:
        """
        Structured news card format (for website/app use).
        Matches the required output format from the brief.
        """
        category = cls._detect_category(article)
        published = cls._format_date(article.published_at)

        return (
            f"Headline: {article.title}\n\n"
            f"Category: {category}\n\n"
            f"Content:\n{cls._rewrite_content(article.full_content)}\n\n"
            f"Published At: {published or 'Not provided'}\n\n"
            f"Source Link: {article.url}\n\n"
            f"Image: {article.image_url or 'Not provided'}\n\n"
            f"Tags: {', '.join(article.tags) or 'Not provided'}"
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    @classmethod
    def _detect_category(cls, article: ParsedArticle) -> str:
        """Infer category from tags and title."""
        tags_lower = " ".join(article.tags).lower()
        title_lower = article.title.lower()
        combined = tags_lower + " " + title_lower

        if any(w in combined for w in ["عالمي", "دوري أبطال", "فيفا", "يويفا"]):
            return "Global Football News"
        if any(w in combined for w in ["سعودي", "النصر", "الهلال", "الاتحاد"]):
            return "Saudi Football News"
        if any(w in combined for w in ["مصري", "الأهلي", "الزمالك"]):
            return "Egyptian Football News"
        if any(w in combined for w in ["منتخب", "كأس العالم", "أمم"]):
            return "International Football News"
        return "Football News"

    @classmethod
    def _rewrite_content(cls, content: str) -> str:
        """
        Light professional rewrite — preserves facts,
        improves flow for news card format.
        """
        # Split into paragraphs and clean each
        paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
        # Rejoin with proper spacing
        return "\n\n".join(paragraphs)

    @classmethod
    def _format_tags(cls, tags: list[str]) -> str:
        if not tags:
            return ""
        # Format as hashtag-style inline links
        return " · ".join(f"#{t.replace(' ', '_')}" for t in tags[:6])

    @classmethod
    def _format_date(cls, dt: Optional[datetime]) -> Optional[str]:
        if not dt:
            return None
        # Ensure UTC display
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.strftime("%d %B %Y, %H:%M UTC")

    @classmethod
    def _truncate(cls, text: str, max_len: int) -> str:
        if len(text) <= max_len:
            return text
        # Cut at word boundary
        truncated = text[:max_len].rsplit(" ", 1)[0]
        return truncated + "…"

    @classmethod
    def _escape(cls, text: str) -> str:
        """Escape HTML special chars for Telegram HTML parse mode."""
        return (
            text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )


class FormattedMessage:
    def __init__(self, text: str, image_url: Optional[str], parse_mode: str):
        self.text = text
        self.image_url = image_url
        self.parse_mode = parse_mode

    def __repr__(self):
        return f"FormattedMessage(len={len(self.text)}, has_image={bool(self.image_url)})"