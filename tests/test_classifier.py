"""
tests/test_classifier.py
────────────────────────
Unit tests for the ArticleClassifier.

Tests cover all classification rules and edge cases.
"""

from __future__ import annotations

import pytest

from src.classifier.engine import ArticleClassifier
from src.core.models import NewsClassification, RawArticle
from src.core.config import get_settings


@pytest.fixture
def classifier():
    return ArticleClassifier(get_settings())


def make_article(title: str, content: str = "", subcategory: str = "Football") -> RawArticle:
    return RawArticle(
        title=title,
        url=f"https://www.wam.ae/en/article/test-{hash(title)}",
        content=content,
        subcategory=subcategory,
    )


# ─────────────────────────────────────────────────────────────────────────────
# UAE News classification
# ─────────────────────────────────────────────────────────────────────────────

class TestUAEClassification:

    @pytest.mark.asyncio
    async def test_uae_city_triggers_uae_news(self, classifier):
        article = make_article("Fujairah to host West Asia Archery Cup 2026")
        result = await classifier.classify(article)
        assert result.classification == NewsClassification.UAE

    @pytest.mark.asyncio
    async def test_uae_club_triggers_uae_news(self, classifier):
        article = make_article("Al Ain FC advances to AFC Champions League final")
        result = await classifier.classify(article)
        assert result.classification == NewsClassification.UAE

    @pytest.mark.asyncio
    async def test_uae_country_mention_triggers_uae_news(self, classifier):
        article = make_article(
            "UAE national team beats Saudi Arabia 2-0 in World Cup qualifier"
        )
        result = await classifier.classify(article)
        assert result.classification == NewsClassification.UAE

    @pytest.mark.asyncio
    async def test_uae_overrides_arab(self, classifier):
        article = make_article(
            "UAE beats Egypt in Gulf Cup semifinal — Dubai hosts the match"
        )
        result = await classifier.classify(article)
        assert result.classification == NewsClassification.UAE

    @pytest.mark.asyncio
    async def test_uae_overrides_global(self, classifier):
        article = make_article(
            "Abu Dhabi Grand Prix — Formula 1 world champion crowned at Yas Marina"
        )
        result = await classifier.classify(article)
        assert result.classification == NewsClassification.UAE


# ─────────────────────────────────────────────────────────────────────────────
# Arab News classification
# ─────────────────────────────────────────────────────────────────────────────

class TestArabClassification:

    @pytest.mark.asyncio
    async def test_arab_only_triggers_arab_news(self, classifier):
        article = make_article(
            "Al Ahly defeats Zamalek in Egyptian Premier League derby"
        )
        result = await classifier.classify(article)
        assert result.classification == NewsClassification.ARAB

    @pytest.mark.asyncio
    async def test_saudi_club_triggers_arab_news(self, classifier):
        article = make_article(
            "Al Hilal wins Saudi Pro League title for record 19th time"
        )
        result = await classifier.classify(article)
        assert result.classification == NewsClassification.ARAB

    @pytest.mark.asyncio
    async def test_arab_plus_global_triggers_arab_news(self, classifier):
        article = make_article(
            "Morocco qualifies for FIFA World Cup 2026 after beating France"
        )
        result = await classifier.classify(article)
        # Morocco is Arab — should be Arab news
        assert result.classification == NewsClassification.ARAB


# ─────────────────────────────────────────────────────────────────────────────
# Global News classification
# ─────────────────────────────────────────────────────────────────────────────

class TestGlobalClassification:

    @pytest.mark.asyncio
    async def test_no_arab_no_uae_triggers_global(self, classifier):
        article = make_article(
            "Real Madrid beats Manchester City 3-1 in UEFA Champions League final"
        )
        result = await classifier.classify(article)
        assert result.classification == NewsClassification.GLOBAL

    @pytest.mark.asyncio
    async def test_entirely_western_teams_is_global(self, classifier):
        article = make_article(
            "Wimbledon: Carlos Alcaraz defeats Novak Djokovic in five sets"
        )
        result = await classifier.classify(article)
        assert result.classification == NewsClassification.GLOBAL


# ─────────────────────────────────────────────────────────────────────────────
# Rule engine
# ─────────────────────────────────────────────────────────────────────────────

class TestRules:

    def test_uae_beats_everything(self):
        classification, confidence = ArticleClassifier._apply_rules(
            has_uae=True, has_arab=True, has_global=True
        )
        assert classification == NewsClassification.UAE
        assert confidence > 0.7

    def test_arab_beats_global(self):
        classification, confidence = ArticleClassifier._apply_rules(
            has_uae=False, has_arab=True, has_global=True
        )
        assert classification == NewsClassification.ARAB

    def test_global_only(self):
        classification, confidence = ArticleClassifier._apply_rules(
            has_uae=False, has_arab=False, has_global=True
        )
        assert classification == NewsClassification.GLOBAL

    def test_nothing_matched_defaults_global(self):
        classification, _ = ArticleClassifier._apply_rules(
            has_uae=False, has_arab=False, has_global=False
        )
        assert classification == NewsClassification.GLOBAL
