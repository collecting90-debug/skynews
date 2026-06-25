"""
src/telegram/sender.py
───────────────────────
Telegram notification module — professional news card format.
No emojis. Clean HTML. ESPN/BBC Sport style.
"""

from __future__ import annotations

import asyncio
import textwrap
from datetime import datetime
from typing import Optional

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from loguru import logger

from src.core.config import Settings
from src.core.models import Article, NewsClassification

_PHOTO_CAPTION_LIMIT = 950
_TEXT_MESSAGE_LIMIT = 3800

_CATEGORY_MAP = {
    NewsClassification.UAE:          "أخبار الإمارات",
    NewsClassification.ARAB:         "أخبار عربية",
    NewsClassification.GLOBAL:       "أخبار عالمية",
    NewsClassification.UNCLASSIFIED: "أخبار كرة القدم",
}


class TelegramSender:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._bot: Optional[Bot] = None
        self._last_sent: float = 0.0
        self._min_interval: float = 2.5

    async def start(self) -> None:
        self._bot = Bot(
            token=self._settings.effective_telegram_bot_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        logger.info("Telegram bot initialised")

    async def stop(self) -> None:
        if self._bot:
            await self._bot.session.close()
        self._bot = None

    async def send(self, article: Article) -> bool:
        if not self._bot:
            logger.error("TelegramSender not started")
            return False

        await self._rate_limit()

        try:
            if article.image_url:
                return await self._send_photo(article)
            else:
                return await self._send_text(article)
        except Exception as exc:
            logger.error(f"Telegram send failed for {article.url}: {exc}")
            return False

    # ── Send methods ──────────────────────────────────────────────────────────

    async def _send_photo(self, article: Article) -> bool:
        caption = self._build_caption(article, limit=_PHOTO_CAPTION_LIMIT)
        keyboard = self._build_keyboard(article)
        try:
            await self._bot.send_photo(  # type: ignore[union-attr]
                chat_id=self._settings.effective_telegram_chat_id,
                photo=article.image_url,
                caption=caption,
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML,
            )
            logger.info(f"Telegram photo sent: {article.title[:60]}")
            return True
        except Exception as exc:
            logger.warning(f"Photo send failed ({exc}), falling back to text")
            return await self._send_text(article)

    async def _send_text(self, article: Article) -> bool:
        text = self._build_caption(article, limit=_TEXT_MESSAGE_LIMIT)
        keyboard = self._build_keyboard(article)
        await self._bot.send_message(  # type: ignore[union-attr]
            chat_id=self._settings.effective_telegram_chat_id,
            text=text,
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=False,
        )
        logger.info(f"Telegram text sent: {article.title[:60]}")
        return True

    # ── Message builder ───────────────────────────────────────────────────────

    def _build_caption(self, article: Article, limit: int = 950) -> str:
        """
        Professional news card format:

        ───────────────────────────
        CATEGORY

        Title

        Content preview

        Tags

        Published At  |  Source
        ───────────────────────────
        """
        category = _CATEGORY_MAP.get(article.classification, "أخبار كرة القدم")
        date_str  = self._format_date(article.publish_date)
        summary   = self._get_summary(article, max_chars=400)

        lines: list[str] = []

        # Category line
        lines.append(f"<b>{self._escape(category)}</b>")
        lines.append("")

        # Title
        lines.append(f"<b>{self._escape(article.title)}</b>")
        lines.append("")

        # Content
        if summary:
            lines.append(self._escape(summary))
            lines.append("")

        # Published date
        if date_str:
            lines.append(f"<i>{date_str}</i>")
            lines.append("")

        # Source link
        lines.append(f'<a href="{article.url}">اقرأ المقال كاملاً</a>')

        caption = "\n".join(lines)

        # Truncate safely if over limit
        if len(caption) > limit:
            caption = caption[: limit - 60].rsplit("\n", 1)[0]
            caption += f'\n\n<a href="{article.url}">اقرأ المقال كاملاً</a>'

        return caption

    def _build_keyboard(self, article: Article) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(
                    text="اقرأ المقال كاملاً",
                    url=article.url,
                )
            ]]
        )

    # ── Utilities ─────────────────────────────────────────────────────────────

    async def _rate_limit(self) -> None:
        import time
        elapsed = time.monotonic() - self._last_sent
        if elapsed < self._min_interval:
            await asyncio.sleep(self._min_interval - elapsed)
        self._last_sent = time.monotonic()

    @staticmethod
    def _format_date(dt: Optional[datetime]) -> str:
        if not dt:
            return ""
        return dt.strftime("%d %b %Y, %H:%M UTC")

    @staticmethod
    def _get_summary(article: Article, max_chars: int = 400) -> str:
        text = article.summary or article.content or ""
        if not text:
            return ""
        paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
        first = paragraphs[0] if paragraphs else text
        return textwrap.shorten(first, width=max_chars, placeholder="…")

    @staticmethod
    def _escape(text: str) -> str:
        return (
            text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
        )