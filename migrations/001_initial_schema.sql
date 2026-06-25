-- ─────────────────────────────────────────────────────────────────────────────
-- Sky News Arabia Sport Monitor — Initial Database Schema
-- Migration: 001_initial_schema.sql
-- Run this in your Supabase SQL editor or via psql.
-- ─────────────────────────────────────────────────────────────────────────────

-- Enable necessary PostgreSQL extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";   -- for fast ILIKE / full-text searches

-- ─────────────────────────────────────────────────────────────────────────────
-- ENUM types
-- ─────────────────────────────────────────────────────────────────────────────

DO $$ BEGIN
    CREATE TYPE news_classification AS ENUM (
        'UAE News',
        'Arab News',
        'Global News',
        'Unclassified'
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE scraping_status AS ENUM (
        'pending',
        'scraped',
        'classified',
        'published',
        'failed'
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- ─────────────────────────────────────────────────────────────────────────────
-- ARTICLES table
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS articles (
    -- ── Identity ──────────────────────────────────────────────────────────────
    id                          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    article_hash                TEXT NOT NULL UNIQUE,      -- SHA-256 of URL
    url                         TEXT NOT NULL UNIQUE,

    -- ── Content ───────────────────────────────────────────────────────────────
    title                       TEXT NOT NULL,
    content                     TEXT,
    summary                     TEXT,
    image_url                   TEXT,

    -- ── Categorisation ────────────────────────────────────────────────────────
    category                    TEXT NOT NULL DEFAULT 'Sport',
    subcategory                 TEXT NOT NULL,
    publish_date                TIMESTAMPTZ,

    -- ── NLP Classification ────────────────────────────────────────────────────
    classification              news_classification NOT NULL DEFAULT 'Unclassified',
    classification_confidence   FLOAT CHECK (classification_confidence BETWEEN 0 AND 1),
    classification_method       TEXT,                      -- 'keyword', 'spacy', 'openai'
    detected_uae_entities       TEXT[] DEFAULT '{}',
    detected_arab_entities      TEXT[] DEFAULT '{}',
    detected_global_entities    TEXT[] DEFAULT '{}',

    -- ── Pipeline status ───────────────────────────────────────────────────────
    status                      scraping_status NOT NULL DEFAULT 'pending',
    scraped_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- ── Telegram delivery ─────────────────────────────────────────────────────
    telegram_sent               BOOLEAN NOT NULL DEFAULT FALSE,
    telegram_sent_at            TIMESTAMPTZ,

    -- ── Audit ─────────────────────────────────────────────────────────────────
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Trigger: auto-update updated_at ──────────────────────────────────────────

CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS articles_updated_at ON articles;
CREATE TRIGGER articles_updated_at
    BEFORE UPDATE ON articles
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ─────────────────────────────────────────────────────────────────────────────
-- INDEXES — tuned for the query patterns used by the application
-- ─────────────────────────────────────────────────────────────────────────────

-- Deduplication (most frequent query — called on every article)
CREATE UNIQUE INDEX IF NOT EXISTS idx_articles_hash
    ON articles (article_hash);

CREATE UNIQUE INDEX IF NOT EXISTS idx_articles_url
    ON articles (url);

-- Classification filter (admin API + analytics)
CREATE INDEX IF NOT EXISTS idx_articles_classification
    ON articles (classification);

-- Subcategory filter
CREATE INDEX IF NOT EXISTS idx_articles_subcategory
    ON articles (subcategory);

-- Recent articles (default sort for listing)
CREATE INDEX IF NOT EXISTS idx_articles_scraped_at
    ON articles (scraped_at DESC);

-- Publish date sort
CREATE INDEX IF NOT EXISTS idx_articles_publish_date
    ON articles (publish_date DESC NULLS LAST);

-- Telegram delivery status (for retry jobs)
CREATE INDEX IF NOT EXISTS idx_articles_telegram_sent
    ON articles (telegram_sent) WHERE telegram_sent = FALSE;

-- Pipeline status
CREATE INDEX IF NOT EXISTS idx_articles_status
    ON articles (status);

-- Full-text search on title (for future search feature)
CREATE INDEX IF NOT EXISTS idx_articles_title_trgm
    ON articles USING GIN (title gin_trgm_ops);

-- ─────────────────────────────────────────────────────────────────────────────
-- ROW LEVEL SECURITY — disable for service-role key usage
-- ─────────────────────────────────────────────────────────────────────────────

ALTER TABLE articles ENABLE ROW LEVEL SECURITY;

-- Service role bypasses RLS automatically in Supabase.
-- Add policies here if you ever expose this table via anon/user JWT tokens.

-- ─────────────────────────────────────────────────────────────────────────────
-- VIEWS — convenience queries
-- ─────────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE VIEW v_articles_summary AS
SELECT
    id,
    title,
    subcategory,
    classification,
    classification_confidence,
    publish_date,
    scraped_at,
    telegram_sent,
    url
FROM articles
ORDER BY scraped_at DESC;

CREATE OR REPLACE VIEW v_classification_stats AS
SELECT
    classification,
    COUNT(*)                                    AS total,
    AVG(classification_confidence)              AS avg_confidence,
    COUNT(*) FILTER (WHERE telegram_sent)       AS sent_to_telegram,
    MAX(scraped_at)                             AS last_scraped
FROM articles
GROUP BY classification;

-- ─────────────────────────────────────────────────────────────────────────────
-- COMMENTS
-- ─────────────────────────────────────────────────────────────────────────────

COMMENT ON TABLE articles IS 'WAM sports news articles scraped and classified by the monitoring system.';
COMMENT ON COLUMN articles.article_hash IS 'SHA-256 of the canonical article URL — primary deduplication key.';
COMMENT ON COLUMN articles.classification IS 'NLP-derived classification: UAE News > Arab News > Global News.';
COMMENT ON COLUMN articles.classification_method IS 'Which NLP tier produced this classification: keyword, spacy, or openai.';
