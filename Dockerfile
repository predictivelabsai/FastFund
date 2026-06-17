FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=5021 \
    DB_URL=sqlite:////app/data/sfohub.db

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# SQLite DB lives on a persistent volume (when DATA_STORAGE=sqlite).
RUN mkdir -p /app/data
VOLUME ["/app/data"]

EXPOSE 5021

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5021/health').read()"

CMD ["python", "-m", "uvicorn", "web.app:app", "--host", "0.0.0.0", "--port", "5021"]
