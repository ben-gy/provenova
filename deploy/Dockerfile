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
COPY deploy/requirements.lock /app/deploy/requirements.lock

# -c pins every third-party (transitive) version to the lockfile so the image is
# reproducible and a compromised/yanked upstream can't silently change what ships.
RUN pip install --upgrade pip \
    && pip install -c /app/deploy/requirements.lock \
       ./packages/ql-core ./packages/ql-sdk ./packages/ql-crawler ./server \
       "psycopg[binary]>=3.1"

# Operational scripts (key generation, demo seeding) — placed after the pip
# layer so editing them doesn't bust the dependency cache. Enables:
#   docker compose run  --rm api  python scripts/gen_attestation_key.py
#   docker compose exec       api python scripts/seed_demo.py
COPY scripts /app/scripts

ENV PYTHONPATH=/app/server
EXPOSE 8000

# Run as an unprivileged user (defence-in-depth: a runtime bug is no longer
# root-in-container). ./data must be writable for the attestation key / sqlite
# when those aren't supplied via env + Postgres.
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /app/data && chown -R app:app /app
USER app

# Bind $PORT when the platform injects one (Cloud Run sets PORT=8080); default to
# 8000 elsewhere. docker-compose overrides this with its own multi-worker command.
# --proxy-headers/--forwarded-allow-ips: trust the single Fly/Caddy edge so
# request.client.host is the real caller (correct rate-limit keying).
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips=*"]
