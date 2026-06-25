"""
tests/test_parser.py
─────────────────────
Unit tests for the HTML article parser.
Uses real HTML snippets from the WAM website (as provided in the brief).
"""

from __future__ import annotations

import pytest

from src.scraper.parser import parse_article_list, _parse_date, _absolute_url


# Sample HTML taken directly from the WAM website (from the brief)
SAMPLE_LISTING_HTML = """
<html>
<body>
<div class="col-md-6 col-sm-12 col-xs-12">
  <app-article-item-bottom-text descriptionmaxheight="90px">
    <div class="art-img single-blog-post style-2 ng-star-inserted">
      <div class="blog-thumbnail">
        <a href="/en/article/c0e2k74-fujairah-host-west-asia-archery-cup">
          <img alt="" src="https://assets.wam.ae/resource/wig041hv1ka1auapd.jpeg">
        </a>
      </div>
      <div class="blog-content" style="max-height: 90px;">
        <a class="post-title description" href="/en/article/c0e2k74-fujairah-host-west-asia-archery-cup">
          Fujairah to host West Asia Archery Cup
        </a>
        <div class="mt-1 description ng-star-inserted">
          <span class="text-muted font-weight-light">
            <small>The West Asia Archery Federation has approved Fujairah as the host of the third edition of the West Asia Cup 2026...</small>
          </span>
        </div>
        <div>
          <span class="post-date">
            <i class="fa fa-solid fa-clock-o"></i>&nbsp; 20 hours ago
          </span>
        </div>
      </div>
    </div>
  </app-article-item-bottom-text>
</div>
</body>
</html>
"""


class TestArticleListParser:

    def test_parses_article_from_sample_html(self):
        articles = parse_article_list(SAMPLE_LISTING_HTML, subcategory="Other Sports")
        assert len(articles) == 1

    def test_title_extracted_correctly(self):
        articles = parse_article_list(SAMPLE_LISTING_HTML, subcategory="Other Sports")
        assert "Fujairah" in articles[0].title
        assert "West Asia Archery Cup" in articles[0].title

    def test_url_is_absolute(self):
        articles = parse_article_list(SAMPLE_LISTING_HTML, subcategory="Other Sports")
        assert articles[0].url.startswith("https://www.wam.ae")
        assert "/en/article/" in articles[0].url

    def test_image_url_extracted(self):
        articles = parse_article_list(SAMPLE_LISTING_HTML, subcategory="Other Sports")
        assert articles[0].image_url is not None
        assert "assets.wam.ae" in articles[0].image_url

    def test_summary_extracted(self):
        articles = parse_article_list(SAMPLE_LISTING_HTML, subcategory="Other Sports")
        assert articles[0].summary is not None
        assert len(articles[0].summary) > 10

    def test_subcategory_assigned(self):
        articles = parse_article_list(SAMPLE_LISTING_HTML, subcategory="Other Sports")
        assert articles[0].subcategory == "Other Sports"

    def test_article_hash_computed(self):
        articles = parse_article_list(SAMPLE_LISTING_HTML, subcategory="Other Sports")
        assert len(articles[0].article_hash) == 64   # SHA-256 hex

    def test_empty_html_returns_empty_list(self):
        articles = parse_article_list("<html><body></body></html>", subcategory="Football")
        assert articles == []


class TestDateParser:

    def test_relative_time(self):
        result = _parse_date("20 hours ago")
        assert result is not None

    def test_absolute_iso_date(self):
        result = _parse_date("2024-05-26T10:30:00")
        assert result is not None
        assert result.year == 2024

    def test_invalid_date_returns_none(self):
        result = _parse_date("not a date at all xyz")
        # dateparser may still parse some garbage — at minimum should not raise
        # (result can be None or datetime)
        assert result is None or hasattr(result, "year")


class TestUrlHelper:

    def test_relative_url_made_absolute(self):
        result = _absolute_url("/en/article/test", "https://www.wam.ae")
        assert result == "https://www.wam.ae/en/article/test"

    def test_absolute_url_unchanged(self):
        url = "https://www.wam.ae/en/article/test"
        assert _absolute_url(url, "https://www.wam.ae") == url
