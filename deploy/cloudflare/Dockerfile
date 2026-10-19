# Provenova on Cloudflare Containers: the FastAPI app + Litestream.
#
# The database is SQLite on the container's ephemeral disk, continuously
# replicated to R2 by Litestream and restored from R2 on every cold start (see
# entrypoint.sh). Built by `wrangler deploy` from cloudflare/wrangler.jsonc with
# the repo root as build context. Cloudflare Containers run linux/amd64.
FROM --platform=linux/amd64 python:3.12-slim
LABEL org.opencontainers.image.source="https://github.com/ben-gy/provenova" \
      org.opencontainers.image.title="provenova"

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends build-essential ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

# Litestream, pinned by version and checksum (from the release's checksums.txt).
ARG LITESTREAM_VERSION=0.5.17
ARG LITESTREAM_SHA256=cfb371176d164437ae869f8351cfde49bd1804ae71c61923f75c9cba9c9c006d
RUN curl -fsSL -o /tmp/litestream.tgz \
      "https://github.com/benbjohnson/litestream/releases/download/v${LITESTREAM_VERSION}/litestream-${LITESTREAM_VERSION}-linux-x86_64.tar.gz" \
    && echo "${LITESTREAM_SHA256}  /tmp/litestream.tgz" | sha256sum -c - \
    && tar -xzf /tmp/litestream.tgz -C /usr/local/bin litestream \
    && rm /tmp/litestream.tgz \
    && litestream version

COPY packages/ql-core /app/packages/ql-core
COPY packages/ql-sdk /app/packages/ql-sdk
COPY packages/ql-crawler /app/packages/ql-crawler
COPY server /app/server
COPY frameworks /app/frameworks
COPY fixtures /app/fixtures
COPY deploy/requirements.lock /app/deploy/requirements.lock

# -c pins every third-party (transitive) version to the lockfile.
RUN pip install --upgrade pip \
    && pip install -c /app/deploy/requirements.lock \
       ./packages/ql-core ./packages/ql-sdk ./packages/ql-crawler ./server

COPY scripts /app/scripts
COPY deploy/cloudflare/litestream.yml /etc/litestream.yml
COPY deploy/cloudflare/entrypoint.sh /usr/local/bin/provenova-entrypoint
RUN chmod 0755 /usr/local/bin/provenova-entrypoint

ENV PYTHONPATH=/app/server \
    QL_DATA_DIR=/data \
    QL_DATABASE_URL=sqlite:////data/provenova.db
EXPOSE 8000

RUN useradd --create-home --uid 10001 app \
    && mkdir -p /data && chown -R app:app /app /data
USER app

CMD ["provenova-entrypoint"]
