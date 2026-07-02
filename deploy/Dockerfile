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

ENV PYTHONPATH=/app/server
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
