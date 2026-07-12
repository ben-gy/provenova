# Deployment & self-hosting

Provenova can run anywhere from a single SQLite file to a full PostgreSQL-backed cluster.
Self-hosting is free — production included, no license key — under the server's BUSL-1.1 license,
which reserves only offering the server to third parties as a hosted, managed, or embedded
service or product (paid or free); see [Licensing](/docs/licensing).

Two things to know before you deploy:

- **Plans bind on the hosted service, not yours.** On a self-hosted instance you are the
  superadmin and administer your own plans and grants — entitlements exist to organize your
  workspaces, not to meter you.
- **Your instance signs with its own key.** Attestations from a self-hosted install verify against
  *your* key at `/.well-known/provenova-jwks.json` on your domain — third parties have no prior
  reason to trust it. The Lab tier's **verified keys** registers your public key in Provenova's
  trust directory so your attestations verify against provenova.net; see the
  [pricing FAQ](/docs/pricing-faq#can-i-self-host).

## Self-host (Docker Compose)

```bash
cd deploy
cp .env.example .env   # then edit — required values are documented inline (QL_DOMAIN, QL_SECRET_KEY, QL_ATTESTATION_KEY_B64, POSTGRES_PASSWORD)
docker compose up -d --build
```

This brings up four services (defined in `deploy/docker-compose.yml`):

| Service | Role |
|---------|------|
| `postgres` | PostgreSQL 16 database. |
| `api` | The FastAPI app (web UI + API) on port 8000. |
| `worker` | Background worker — periodic corpus crawling and continuous compliance monitoring. |
| `proxy` | Caddy reverse proxy — automatic HTTPS (TLS) and gzip compression. |

Persistent volumes hold the database (`pgdata`) and Caddy's TLS certificates and state (`caddy_data`,
`caddy_config`). The attestation signing key lives in `.env` as `QL_ATTESTATION_KEY_B64`, not on a
volume — back that file up.

## Configuration (environment variables)

| Variable | Purpose |
|----------|---------|
| `QL_DATABASE_URL` | Connection string. A PostgreSQL URL for hosted, or a SQLite URL for a tiny single-node deployment. |
| `QL_DEPLOYMENT` | Deployment flavor: `selfhost` or `hosted` (default `hosted`). Shown in the page footer and reported by `/api/v1/health`. |
| `QL_BASE_URL` | Public base URL (e.g. `https://provenova.example.com`). |
| `QL_SECRET_KEY` | Session-cookie secret. **Set a strong value in production.** |
| `QL_ATTESTATION_KEY_B64` | Base64 of the PKCS8-PEM Ed25519 attestation signing key (generate with `python scripts/gen_attestation_key.py`). This is what the compose stack uses; it must stay stable across redeploys — regenerating it invalidates all previously issued attestations. |
| `QL_ATTESTATION_KEY_PATH` | Fallback: path to a PEM key file, used only when `QL_ATTESTATION_KEY_B64` is unset (auto-generated on first boot; not durable on ephemeral filesystems). |
| `QL_PUBLIC_CARDS` | `true`/`false`. Reserved — accepted but not currently enforced; public cards and badges are served regardless of this value. |
| `QL_ADMIN_EMAIL` | Initial admin account. |

> **Warning:** Generate a strong, unique `QL_SECRET_KEY` in production — it signs session cookies. Never reuse the development default, and store the attestation key (`QL_ATTESTATION_KEY_B64`) somewhere durable and backed up.

## Tiny / offline deployments

Point `QL_DATABASE_URL` at a SQLite file for a single-node install — the schema is identical to the
PostgreSQL one, so nothing else changes.

## Running the app directly

```bash
pip install -e packages/ql-core -e packages/ql-sdk -e packages/ql-crawler -e server
PYTHONPATH=server uvicorn app.main:app --port 8000
```

The image builds from `deploy/Dockerfile` (`python:3.12-slim`), installs all packages, and runs
`uvicorn app.main:app`.

## Verifying attestations from outside

Consumers verify attestations against the public keys published at
`/.well-known/provenova-jwks.json` — no privileged access needed. See
[Compliance & attestations](/docs/compliance#attestations).

## Tests

```bash
python -m pytest   # e2e (hash determinism via ingest round-trip, ingest dedup, drift verdicts), rule engine, attestation, crawler, tiering, DOI
```

That's the end of the guide — back to the [overview](/docs/overview), or browse the live
[API reference](/docs/api).
