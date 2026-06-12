FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=5011 \
    DB_URL=sqlite:////app/data/taxhub.db

WORKDIR /app

# Build tools + Playwright/Chromium runtime libraries.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
    libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
    libgbm1 libasound2 libpango-1.0-0 libcairo2 fonts-liberation && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    python -m playwright install chromium

COPY . .

# SQLite DB + captured raw documents live on a persistent volume.
RUN mkdir -p /app/data
VOLUME ["/app/data"]

EXPOSE 5011

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5011/health').read()"

# Web viewer. The scraper runs separately (cron / `docker compose run scrape`).
CMD ["python", "-m", "uvicorn", "taxapp:app", "--host", "0.0.0.0", "--port", "5011"]
