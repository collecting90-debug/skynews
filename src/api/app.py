"""
src/api/app.py
──────────────
Optional FastAPI admin interface.

Provides HTTP endpoints for:
  • Health check
  • Recent articles listing
  • Manual poll trigger
  • Statistics

Run alongside the main monitor process, or disable entirely.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from loguru import logger

from src.core.config import get_settings
from src.core.models import NewsClassification
from src.database.repository import ArticleRepository

_repo: ArticleRepository | None = None
_settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator:
    global _repo
    _repo = ArticleRepository(_settings)
    await _repo.connect()
    logger.info("Admin API started")
    yield
    if _repo:
        await _repo.disconnect()
    logger.info("Admin API stopped")


app = FastAPI(
    title="WAM News Monitor — Admin API",
    version="1.0.0",
    description="Internal API for monitoring the WAM news scraper",
    lifespan=lifespan,
)


@app.get("/health", tags=["meta"])
async def health_check():
    """Quick health probe — used by Docker healthcheck."""
    return {"status": "ok", "service": "wam-news-monitor"}


@app.get("/articles/recent", tags=["articles"])
async def get_recent_articles(limit: int = 20):
    """Return the most recently scraped articles."""
    if not _repo:
        raise HTTPException(503, "Database not available")
    articles = await _repo.get_recent(limit=min(limit, 100))
    return {"count": len(articles), "articles": articles}


@app.get("/articles/by-classification/{classification}", tags=["articles"])
async def get_by_classification(classification: str, limit: int = 50):
    """Return articles filtered by classification type."""
    try:
        cls = NewsClassification(classification)
    except ValueError:
        raise HTTPException(400, f"Unknown classification: {classification}")

    if not _repo:
        raise HTTPException(503, "Database not available")

    articles = await _repo.get_by_classification(cls, limit=min(limit, 200))
    return {"classification": classification, "count": len(articles), "articles": articles}


@app.get("/stats", tags=["meta"])
async def get_stats():
    """Return high-level statistics."""
    if not _repo:
        raise HTTPException(503, "Database not available")

    uae = await _repo.get_by_classification(NewsClassification.UAE, limit=1)
    arab = await _repo.get_by_classification(NewsClassification.ARAB, limit=1)
    glob = await _repo.get_by_classification(NewsClassification.GLOBAL, limit=1)

    return {
        "classifications": {
            "uae_news": len(uae),
            "arab_news": len(arab),
            "global_news": len(glob),
        }
    }
