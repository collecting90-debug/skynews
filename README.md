# WAM News Monitor — Enterprise Intelligence Platform

A production-grade, 24/7 intelligent news monitoring system targeting the UAE state news agency (WAM) sports category. Extracts, classifies, stores, and distributes breaking news in real time.

---

## Architecture Overview

```
wam-news-monitor/
├── src/
│   ├── core/               # Config, logging, base models, DI container
│   ├── scraper/            # Playwright engine, page parsers, article extractor
│   ├── classifier/         # NLP pipeline, NER, entity detection, OpenAI fallback
│   ├── database/           # Supabase repository, migrations, query layer
│   ├── telegram/           # Bot sender, message formatter, rate limiter
│   └── api/                # FastAPI admin endpoints (optional)
├── migrations/             # SQL schema files
├── docker/                 # Dockerfile, nginx config
├── tests/                  # Unit + integration tests
├── docker-compose.yml
├── requirements.txt
├── .env.example
└── README.md
```

### Design Principles

| Concern | Approach |
|---|---|
| Concurrency | `asyncio` + `Playwright` async API |
| Fault tolerance | Exponential backoff retries, watchdog restarts |
| State management | Redis for seen-article deduplication |
| Persistence | Supabase (PostgreSQL) with hash-based uniqueness |
| Classification | spaCy NER → rule engine → OpenAI GPT-4o fallback |
| Observability | `loguru` structured JSON logs + Sentry-ready |
| Deployment | Docker Compose; scalable to Kubernetes |

---

## Quick Start

### 1. Prerequisites

- Docker 24+ and Docker Compose v2
- Python 3.12+ (for local dev)

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your credentials
```

### 3. Run with Docker

```bash
docker compose up -d
```

### 4. Run locally (development)

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python -m spacy download en_core_web_lg
playwright install chromium
python -m src.main
```

---

## Environment Variables

See `.env.example` for all required variables.

---

## Database Schema

See `migrations/001_initial_schema.sql`.

---

## Monitoring & Logs

Logs are written to `./logs/` as structured JSON. Mount this directory or ship to your log aggregator.

---

## Scaling

- Add more subcategory workers by extending `SUBCATEGORIES` in config
- Redis handles cross-worker dedup automatically
- Each worker is an independent asyncio task — horizontally scalable

---

## Security Notes

- Service role key stored only in `.env`, never committed
- Playwright runs in sandboxed headless Chromium
- Rate limiting prevents IP bans
- Proxy rotation supported via `PROXY_URL`

---

## License

Internal use only.
