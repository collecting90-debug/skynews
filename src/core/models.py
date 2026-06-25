"""
src/core/models.py
──────────────────
Domain models — the single source of truth for article data shapes.
All layers (scraper, classifier, database, telegram) use these models.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator, computed_field


# ─────────────────────────────────────────────────────────────────────────────
# Enumerations
# ─────────────────────────────────────────────────────────────────────────────

class NewsClassification(str, Enum):
    UAE = "UAE News"
    ARAB = "Arab News"
    GLOBAL = "Global News"
    UNCLASSIFIED = "Unclassified"


class ScrapingStatus(str, Enum):
    PENDING = "pending"
    SCRAPED = "scraped"
    CLASSIFIED = "classified"
    PUBLISHED = "published"
    FAILED = "failed"


class SubcategorySlug(str, Enum):
    FOOTBALL = "football"
    EQUESTRIAN = "equestrian-camel-racing"
    OTHER = "other-sports"


# ─────────────────────────────────────────────────────────────────────────────
# Article — raw scraped data
# ─────────────────────────────────────────────────────────────────────────────

class RawArticle(BaseModel):
    """
    Data extracted directly from the WAM website.
    Not yet classified or stored.
    """

    title: str
    url: str
    image_url: Optional[str] = None
    content: Optional[str] = None          # full article body
    summary: Optional[str] = None          # short excerpt from listing page
    publish_date: Optional[datetime] = None
    category: str = "Sport"
    subcategory: str

    @computed_field  # type: ignore[misc]
    @property
    def article_hash(self) -> str:
        """
        SHA-256 fingerprint of the canonical URL.
        Used for deduplication across restarts.
        """
        canonical = self.url.strip().lower()
        return hashlib.sha256(canonical.encode()).hexdigest()

    @field_validator("url")
    @classmethod
    def ensure_absolute_url(cls, v: str) -> str:
        if v.startswith("/"):
            return f"https://www.skynewsarabia.com{v}"
        return v

    @field_validator("title")
    @classmethod
    def strip_title(cls, v: str) -> str:
        return v.strip()


# ─────────────────────────────────────────────────────────────────────────────
# Article — enriched, classified, ready for storage
# ─────────────────────────────────────────────────────────────────────────────

class Article(RawArticle):
    """
    Fully processed article — classified, ready to persist and distribute.
    """

    classification: NewsClassification = NewsClassification.UNCLASSIFIED
    classification_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    classification_method: str = "unset"   # "rule", "spacy", "openai"

    status: ScrapingStatus = ScrapingStatus.PENDING
    scraped_at: datetime = Field(default_factory=datetime.utcnow)
    telegram_sent: bool = False
    telegram_sent_at: Optional[datetime] = None

    # Detected named entities (for audit / debugging)
    detected_uae_entities: list[str] = Field(default_factory=list)
    detected_arab_entities: list[str] = Field(default_factory=list)
    detected_global_entities: list[str] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Classification result — returned by the classifier engine
# ─────────────────────────────────────────────────────────────────────────────

class ClassificationResult(BaseModel):
    classification: NewsClassification
    confidence: float = Field(ge=0.0, le=1.0)
    method: str
    uae_entities: list[str] = Field(default_factory=list)
    arab_entities: list[str] = Field(default_factory=list)
    global_entities: list[str] = Field(default_factory=list)
    reasoning: Optional[str] = None
