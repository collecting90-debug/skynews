"""
src/core/config.py
──────────────────
Centralised configuration using Pydantic-Settings.
All values are loaded from environment variables / .env file.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application-wide settings — loaded once, shared everywhere."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Environment ──────────────────────────────────────────────────────────
    env: str = Field(default="production")
    log_level: str = Field(default="INFO")
    log_json: bool = Field(default=True)

    # ── Supabase ─────────────────────────────────────────────────────────────
    supabase_url: str
    supabase_service_role_key: str

    # ── Telegram ─────────────────────────────────────────────────────────────
    # Accept either old (filgoal) or new (skynews) naming for compatibility
    telegram_bot_token_skynews: Optional[str] = Field(default=None)
    telegram_chat_id_skynews: Optional[str] = Field(default=None)
    # Legacy names (kept for backwards-compat with existing .env files)
    telegram_bot_token_filgoal: Optional[str] = Field(default=None)
    telegram_chat_id_filgoal: Optional[str] = Field(default=None)

    @property
    def effective_telegram_bot_token(self) -> str:
        token = (
            self.telegram_bot_token_skynews
            or self.telegram_bot_token_filgoal
            or ""
        )
        return token

    @property
    def effective_telegram_chat_id(self) -> str:
        chat_id = (
            self.telegram_chat_id_skynews
            or self.telegram_chat_id_filgoal
            or ""
        )
        return chat_id

    # ── OpenAI ───────────────────────────────────────────────────────────────
    use_openai: bool = Field(default=False)
    openai_api_key: Optional[str] = Field(default=None)
    openai_model: str = Field(default="gpt-4o-mini")

    # ── Redis ─────────────────────────────────────────────────────────────────
    redis_url: str = Field(default="redis://redis:6379/0")

    # ── Scraper ───────────────────────────────────────────────────────────────
    # CHANGED: reduced from 30 → 20 seconds for near-real-time monitoring
    poll_interval_seconds: int = Field(default=20)
    page_load_timeout: int = Field(default=60_000)  # ms
    element_timeout: int = Field(default=30_000)    # ms
    max_retries: int = Field(default=5)
    retry_backoff_base: float = Field(default=5.0)

    # ── Listing-page fetch strategy ───────────────────────────────────────────
    # use_httpx_for_listing: True = fast httpx GET for the listing page (default)
    #                         False = Playwright (needed if site is fully JS-rendered
    #                         and httpx returns an empty article list)
    use_httpx_for_listing: bool = Field(default=True)

    # ── Browser ───────────────────────────────────────────────────────────────
    headless: bool = Field(default=True)
    proxy_url: Optional[str] = Field(default=None)

    # ── Sentry ────────────────────────────────────────────────────────────────
    sentry_dsn: Optional[str] = Field(default=None)

    # ── Sky News Arabia subcategories ─────────────────────────────────────────
    @property
    def subcategories(self) -> list[dict]:
        return [
            {
                "name": "رياضة",
                "url": "https://www.skynewsarabia.com/sport",
            },
        ]

    @field_validator("log_level")
    @classmethod
    def normalise_log_level(cls, v: str) -> str:
        return v.upper()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached singleton Settings instance."""
    return Settings()
