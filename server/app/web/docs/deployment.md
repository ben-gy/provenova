# Deployment & self-hosting

QuantumLedger can run anywhere from a single SQLite file to a full PostgreSQL-backed cluster. The server is
source-available (BUSL-1.1); see [Libraries & downloads](/docs/libraries).

## One-command self-host (Docker Compose)

```bash
cd deploy && docker compose up -d
```

This brings up four services (defined in `deploy/docker-compose.yml`):

| Service | Role |
|---------|------|
| `postgres` | PostgreSQL 16 database. |
| `api` | The FastAPI app (web UI + API) on port 8000. |
| `worker` | Async job processor (reproduce jobs, corpus ingestion, background tasks). |
| `proxy` | Caddy reverse proxy — TLS and caching headers. |

Persistent volumes hold the database (`pgdata`), attestation keys and other state (`qldata`), and TLS
certificates (`caddydata`).

## Configuration (environment variables)

| Variable | Purpose |
|----------|---------|
| `QL_DATABASE_URL` | Connection string. A PostgreSQL URL for hosted, or a SQLite URL for a tiny single-node deployment. |
| `QL_DEPLOYMENT` | Deployment flavor (`selfhost` / `hosted` / …) — drives feature gates and UI copy. |
| `QL_BASE_URL` | Public base URL (e.g. `https://quantumledger.example.com`). |
| `QL_SECRET_KEY` | Session-cookie secret. **Set a strong value in production.** |
| `QL_ATTESTATION_KEY_PATH` | Path to the Ed25519 private key used to sign attestations. |
| `QL_PUBLIC_CARDS` | `true`/`false`. Set `false` for a VPC/air-gapped install so cards and badges stay internal. |
| `QL_ADMIN_EMAIL` | Initial admin account. |

> **Warning:** Generate a strong, unique `QL_SECRET_KEY` in production — it signs session cookies. Never reuse the development default, and store the attestation key (`QL_ATTESTATION_KEY_PATH`) somewhere durable and backed up.

## Tiny / offline deployments

Point `QL_DATABASE_URL` at a SQLite file for a single-node install — the schema is identical to the
PostgreSQL one, so nothing else changes. Combine with `QL_PUBLIC_CARDS=false` for a fully internal,
air-gapped deployment (no public cards, badges, or corpus exposure).

## Running the app directly

```bash
pip install -e packages/ql-core -e packages/ql-sdk -e packages/ql-crawler -e server
PYTHONPATH=server uvicorn app.main:app --port 8000
```

The image builds from `deploy/Dockerfile` (`python:3.12-slim`), installs all packages, and runs
`uvicorn app.main:app`.

## Verifying attestations from outside

Consumers verify attestations against the public keys published at
`/.well-known/quantumledger-jwks.json` — no privileged access needed. See
[Compliance & attestations](/docs/compliance#attestations).

## Tests

```bash
python -m pytest   # determinism, immutability, dedup, drift, rule engine, attestation, crawler, e2e
```

That's the end of the guide — back to the [overview](/docs/overview), or browse the live
[API reference](/docs/api).
