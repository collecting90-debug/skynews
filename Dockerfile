# ── Stage 1: Builder ──────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc libffi-dev libssl-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --prefix=/install --no-cache-dir -r requirements.txt

# ── Stage 2: Runtime ──────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
    libdbus-1-3 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 \
    libxrandr2 libgbm1 libasound2 libpango-1.0-0 libcairo2 \
    libx11-6 libx11-xcb1 libxcb1 libxext6 \
    fonts-unifont fonts-freefont-ttf curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local

# Create user
RUN useradd -m -u 1000 monitor

# Install Chromium as monitor user BEFORE copying app code
# This layer gets cached and won't re-download unless base image changes
USER monitor
ENV PLAYWRIGHT_BROWSERS_PATH=/home/monitor/.cache/ms-playwright
RUN python -m playwright install chromium

# Now copy app code (changes here won't invalidate Chromium cache)
USER root
WORKDIR /app
COPY src/ ./src/
COPY migrations/ ./migrations/
RUN mkdir -p /app/logs && chown -R monitor:monitor /app

USER monitor

HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

EXPOSE 8080
CMD ["python", "-m", "src.main"]