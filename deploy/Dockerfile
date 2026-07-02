# QuantumLedger all-in-one image (API/web + worker share this image).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY packages/ql-core /app/packages/ql-core
COPY packages/ql-sdk /app/packages/ql-sdk
COPY packages/ql-crawler /app/packages/ql-crawler
COPY server /app/server
COPY frameworks /app/frameworks
COPY fixtures /app/fixtures

RUN pip install --upgrade pip \
    && pip install ./packages/ql-core ./packages/ql-sdk ./packages/ql-crawler ./server \
    && pip install "psycopg[binary]>=3.1"

# Operational scripts (key generation, demo seeding) — placed after the pip
# layer so editing them doesn't bust the dependency cache. Enables:
#   docker compose run  --rm api  python scripts/gen_attestation_key.py
#   docker compose exec       api python scripts/seed_demo.py
COPY scripts /app/scripts

ENV PYTHONPATH=/app/server
EXPOSE 8000

# Bind $PORT when the platform injects one (Cloud Run sets PORT=8080); default to
# 8000 elsewhere. docker-compose overrides this with its own multi-worker command.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
