# Deployment Guide — WAM News Monitor

---

## 1. Prerequisites

| Requirement | Minimum Version |
|---|---|
| Docker | 24.0+ |
| Docker Compose | v2.20+ |
| Supabase project | ✓ (existing) |
| Telegram Bot | ✓ (create via @BotFather) |
| OpenAI API key | Optional (improves classification) |

---

## 2. First-time Setup

### 2.1 Clone and configure

```bash
git clone <your-repo>
cd wam-news-monitor
cp .env.example .env
```

Edit `.env` and fill in:
- `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY`
- `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`
- `OPENAI_API_KEY` (optional)

### 2.2 Run the database migration

Open your Supabase project → SQL Editor → paste and run:

```
migrations/001_initial_schema.sql
```

### 2.3 Build and start

```bash
docker compose up -d --build
```

First startup takes 5–10 minutes (downloads Chromium + spaCy model).

### 2.4 Verify it's working

```bash
# Watch logs
docker compose logs -f monitor

# Check admin API
curl http://localhost:8080/health

# View recent articles
curl http://localhost:8080/articles/recent
```

---

## 3. Local Development

```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
python -m spacy download en_core_web_lg
playwright install chromium

# Copy and fill in .env
cp .env.example .env

# Run the monitor
python -m src.main

# Run tests
pytest tests/ -v
```

---

## 4. Environment Variable Reference

| Variable | Required | Default | Description |
|---|---|---|---|
| `SUPABASE_URL` | ✓ | — | Your Supabase project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | ✓ | — | Service role key (bypasses RLS) |
| `TELEGRAM_BOT_TOKEN` | ✓ | — | Bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | ✓ | — | Target chat/channel ID |
| `OPENAI_API_KEY` | ✗ | — | Enables GPT-4o-mini classifier fallback |
| `REDIS_URL` | ✗ | `redis://redis:6379/0` | Redis for cross-worker dedup |
| `POLL_INTERVAL_SECONDS` | ✗ | `30` | How often to check each subcategory |
| `PAGE_LOAD_TIMEOUT` | ✗ | `60000` | Max ms to wait for page load |
| `MAX_RETRIES` | ✗ | `5` | Retry attempts per URL |
| `HEADLESS` | ✗ | `true` | Run browser headless |
| `PROXY_URL` | ✗ | — | HTTP proxy (e.g. `http://user:pass@host:port`) |
| `LOG_LEVEL` | ✗ | `INFO` | Logging verbosity |
| `SENTRY_DSN` | ✗ | — | Sentry error tracking DSN |

---

## 5. Monitoring & Operations

### View logs

```bash
# Live logs
docker compose logs -f monitor

# Last 100 lines
docker compose logs --tail=100 monitor

# Error logs only
tail -f logs/errors.log
```

### Restart after config change

```bash
docker compose restart monitor
```

### Full restart (rebuild)

```bash
docker compose down
docker compose up -d --build
```

### Scale workers (future)

```bash
docker compose up -d --scale monitor=3
```
(Requires Redis for cross-worker dedup — already configured.)

---

## 6. Security Hardening

1. **Never commit `.env`** — it contains secret keys
2. **Rotate Supabase service role key** every 90 days
3. **Use a dedicated Telegram bot** — don't reuse bots
4. **Restrict admin API** — put Nginx + basic auth in front of port 8080
5. **Use a read-only proxy account** if adding proxy rotation
6. **Enable Sentry** for production error alerting
7. **Review Supabase RLS policies** before exposing data via anon key

### Nginx reverse proxy for admin API

```nginx
server {
    listen 443 ssl;
    server_name monitor.yourdomain.com;

    auth_basic "WAM Monitor Admin";
    auth_basic_user_file /etc/nginx/.htpasswd;

    location / {
        proxy_pass http://localhost:8080;
        proxy_set_header Host $host;
    }
}
```

---

## 7. Performance Tuning

| Parameter | Recommendation |
|---|---|
| `POLL_INTERVAL_SECONDS` | 30–60s is optimal; <20s risks rate limiting |
| `PAGE_LOAD_TIMEOUT` | 60–90s for WAM (very slow site) |
| `MAX_RETRIES` | 5 is good; increase to 7 for flaky networks |
| Redis `maxmemory` | 256 MB is plenty for URL dedup |
| spaCy model | `en_core_web_lg` gives best NER accuracy |
| Docker memory limit | 2 GB minimum (Chromium + spaCy are heavy) |

---

## 8. Scaling to Additional Categories

To monitor additional WAM categories, add entries to `subcategories` in `src/core/config.py`:

```python
@property
def subcategories(self) -> list[dict]:
    return [
        # existing sports subcategories ...
        {
            "name": "Economy",
            "slug": "economy",
            "url": f"{self.wam_base_url}/en/category/economy",
        },
    ]
```

No other changes required — the architecture is category-agnostic.

---

## 9. Troubleshooting

| Symptom | Likely Cause | Fix |
|---|---|---|
| `No articles found` | Angular hasn't rendered | Increase `PAGE_LOAD_TIMEOUT` |
| `Browser crash loop` | Insufficient memory | Increase Docker memory limit to 3 GB |
| `Supabase 401` | Wrong service role key | Check `.env` |
| `Telegram flood` | Too many restarts | Check for crash loop in logs |
| `Redis connection refused` | Redis container not ready | `docker compose restart monitor` |
| `spaCy model not found` | Model not downloaded | Rebuild Docker image |
