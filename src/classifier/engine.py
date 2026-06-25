"""
src/classifier/engine.py
────────────────────────
Two-tier classification pipeline (spaCy removed for Python 3.12 compatibility):

  Tier 1 — Fast keyword/regex matching against the entity knowledge base.
            Handles ~85% of articles instantly with zero API cost.

  Tier 2 — OpenAI GPT-4o-mini fallback.  Called only when Tier 1
            returns low confidence (<0.6).

Classification logic (in priority order):
  • Any UAE entity present               → UAE News
  • Arab entity present, no UAE entity   → Arab News
  • Neither UAE nor Arab entity          → Global News
"""

from __future__ import annotations

import re
from typing import Optional

from loguru import logger

from src.core.config import Settings
from src.core.models import ClassificationResult, NewsClassification, RawArticle
from src.classifier.entities import ARAB_ENTITIES, GLOBAL_ENTITIES, UAE_ENTITIES

_openai_client = None


def _get_openai_client(settings: Settings):
    """Lazy-load the OpenAI client — only if USE_OPENAI=true and key is set."""
    global _openai_client
    if not settings.use_openai:
        return None
    if _openai_client is None and settings.openai_api_key:
        from openai import AsyncOpenAI
        _openai_client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _openai_client


class ArticleClassifier:
    """
    Multi-tier article classification engine.

    Usage::

        classifier = ArticleClassifier(settings)
        result = await classifier.classify(article)
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def classify(self, article: RawArticle) -> ClassificationResult:
        """
        Run the classification pipeline and return a ClassificationResult.
        """
        text = self._build_text(article)

        # ── Tier 1: Keyword matching ──────────────────────────────────────────
        result = self._keyword_classify(text)
        if result.confidence >= 0.85:
            logger.debug(
                f"Classified via keywords: {result.classification}",
                confidence=result.confidence,
                title=article.title[:60],
            )
            return result

        # ── Tier 2: OpenAI fallback ───────────────────────────────────────────
        openai_client = _get_openai_client(self._settings)
        if openai_client:
            try:
                openai_result = await self._openai_classify(text, article.title)
                if openai_result:
                    logger.debug(
                        f"Classified via OpenAI: {openai_result.classification}",
                        confidence=openai_result.confidence,
                    )
                    return openai_result
            except Exception as exc:
                logger.warning(f"OpenAI classification failed: {exc}")

        logger.warning(
            f"Low-confidence classification: {result.classification} ({result.confidence:.2f})",
            title=article.title[:60],
        )
        return result

    # ─────────────────────────────────────────────────────────────────────────
    # Tier 1 — Keyword matching
    # ─────────────────────────────────────────────────────────────────────────

    def _keyword_classify(self, text: str) -> ClassificationResult:
        normalised = text.lower()

        uae_hits = [e for e in UAE_ENTITIES if e in normalised]
        arab_hits = [e for e in ARAB_ENTITIES if e in normalised]
        global_hits = [e for e in GLOBAL_ENTITIES if e in normalised]

        classification, confidence = self._apply_rules(
            bool(uae_hits), bool(arab_hits), bool(global_hits),
            uae_count=len(uae_hits),
            arab_count=len(arab_hits),
        )

        return ClassificationResult(
            classification=classification,
            confidence=confidence,
            method="keyword",
            uae_entities=uae_hits[:10],
            arab_entities=arab_hits[:10],
            global_entities=global_hits[:10],
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Tier 2 — OpenAI fallback
    # ─────────────────────────────────────────────────────────────────────────

    async def _openai_classify(
        self, text: str, title: str
    ) -> Optional[ClassificationResult]:
        client = _get_openai_client(self._settings)
        if not client:
            return None

        prompt = f"""You are a news classification expert. Classify this sports news article into exactly ONE of these categories:
- UAE News: article involves any UAE entity (country, city, club, player, federation, venue)
- Arab News: article involves Arab countries/entities but NO UAE entities  
- Global News: article has NO UAE and NO Arab entities

Article Title: {title}

Article Text (first 1500 chars):
{text[:1500]}

Respond ONLY with valid JSON in this exact format:
{{"classification": "UAE News|Arab News|Global News", "confidence": 0.0-1.0, "uae_entities": [], "arab_entities": [], "global_entities": [], "reasoning": "brief reason"}}"""

        response = await client.chat.completions.create(
            model=self._settings.openai_model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=300,
            temperature=0.1,
        )

        import json
        raw = response.choices[0].message.content or ""
        raw = re.sub(r"```json|```", "", raw).strip()

        data = json.loads(raw)
        classification_str = data.get("classification", "Global News")

        classification_map = {
            "UAE News": NewsClassification.UAE,
            "Arab News": NewsClassification.ARAB,
            "Global News": NewsClassification.GLOBAL,
        }
        classification = classification_map.get(classification_str, NewsClassification.GLOBAL)

        return ClassificationResult(
            classification=classification,
            confidence=float(data.get("confidence", 0.75)),
            method="openai",
            uae_entities=data.get("uae_entities", []),
            arab_entities=data.get("arab_entities", []),
            global_entities=data.get("global_entities", []),
            reasoning=data.get("reasoning"),
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Shared rules
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _apply_rules(
        has_uae: bool,
        has_arab: bool,
        has_global: bool,
        uae_count: int = 0,
        arab_count: int = 0,
    ) -> tuple[NewsClassification, float]:
        if has_uae:
            confidence = min(0.70 + uae_count * 0.05, 0.98)
            return NewsClassification.UAE, confidence
        if has_arab:
            confidence = min(0.65 + arab_count * 0.05, 0.95)
            return NewsClassification.ARAB, confidence
        if has_global:
            return NewsClassification.GLOBAL, 0.80
        return NewsClassification.GLOBAL, 0.40

    @staticmethod
    def _build_text(article: RawArticle) -> str:
        parts = [article.title]
        if article.summary:
            parts.append(article.summary)
        if article.content:
            parts.append(article.content)
        return " ".join(parts)