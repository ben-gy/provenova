# Provenova

**The vendor-neutral system of record for quantum computing.**

> Naming: the product and site are **Provenova** (provenova.net); *quantumledger*
> is the repo codename that survives in a few stable identifiers — the `ql` CLI,
> the `QL_*` env vars, the `ql:card:*` PIDs and the frozen `qlprov/*` schema IDs.

Provenova binds every quantum run to the exact calibration and hardware state
that produced it — so results are **reproducible, comparable, shareable and
auditable** across QPUs, simulators and vendors. It is the "source of truth" that
enterprise software provides in every other data-intensive field, applied to the
noisy, drifting world of quantum.

This repository is a complete, runnable reference implementation of all six
product areas from the PRD — capture, provenance store, reproduce/analyse, trust
artifacts, compliance, and the public-corpus crawler.

---

## What's inside

```
packages/ql-core/      Shared core: SQLAlchemy provenance model, canonical hashing +
                       Merkle run-hashing, DB-trigger immutability, the deterministic
                       simulator + drift engine, reproduce/scoring/diff, open qlprov schemas.
packages/ql-sdk/       Open-source client `provenova` (Apache-2.0): the @ql.capture
                       decorator/context-manager, vendor connectors (plugin system),
                       offline local store, `ql` CLI, content-hash-idempotent push.
packages/ql-crawler/   Public-QPU calibration crawler + aggregate corpus + ToS gate.
server/                FastAPI app: ingestion + read API, server-rendered web UI,
                       Result Cards + badge service, compliance rule engine,
                       Ed25519 attestations, accounts/entitlements/RBAC, admin.
frameworks/            Compliance frameworks as data (FAIR, IEEE P7131, metrology, ...).
fixtures/              Representative vendor calibration payloads (IBM/IonQ/Braket).
deploy/                docker-compose (postgres + api + worker + Caddy) for self-host.
examples/  scripts/    Runnable demos + the end-to-end seed.
tests/                 pytest suites (attestation, crawler + corpus dedup, datasets, DOI
                       minting, rule engine, growth, plan tiering, full end-to-end server flow).
```

## Licensing

Open-core, one repo, two licenses — each directory carries its own `LICENSE` file:

- **Apache-2.0**: `packages/ql-core`, `packages/ql-sdk`, `packages/ql-crawler` (published on
  PyPI as [`provenova`](https://pypi.org/project/provenova/), `provenova-core`,
  `provenova-crawler`) and the `frameworks/` compliance data. The provenance format and every
  tool that reads or writes it is permanently open.
- **BUSL-1.1**: `server/` — source-available; free to run, **production self-hosting included**,
  no license key. The only reserved right is offering the server to third parties as a
  hosted, managed, or embedded service or product with substantially the server's
  functionality — paid or free. Each release converts to Apache-2.0 four years after it
  ships (this one: 2030-07-03). Details with examples:
  [provenova.net/docs/licensing](https://provenova.net/docs/licensing).

## Contributing

Contributions are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) for setup, PR guidelines
and the [Provenova CLA](CLA.md), which every contributor signs once (a bot walks you through
it on your first PR). The CLA grants the relicensing rights that keep the scheduled
BUSL→Apache conversion and commercial licensing legally possible.

## The four pillars (PRD §1)

1. **Vendor-neutral system of record** — one versioned, immutable, hash-chained record
   binding every run to the calibration state that produced it (`ql-core`).
2. **Open-core + freemium** — the client, connectors, provenance schema and reproduce
   engine are Apache-2.0; the hosted record is freemium (Free: 250 private records, fleet
   comparison, unlimited public Result Cards & badges, a read-only FAIR checklist);
   attestation issuance, continuous monitoring, deeper analytics and governance
   (Trust Center, SSO, data residency, SLA) are paid.
3. **Trust artifacts as a growth engine** — public, citable Result Cards and embeddable
   shields.io-style badges (Recorded → Reproduced → Benchmarked → Compliant → Audit-ready).
4. **"Vanta for quantum" compliance** — pick a standard; evidence is auto-collected from
   the runs already in the record; issue signed, revocable attestations.

---

## Quickstart (local, no account, < 5 minutes)

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e packages/ql-core -e "packages/ql-sdk[aer]"

python examples/bell.py         # one-line @ql.capture around your job
ql list                         # see the recorded run
ql show <run_id>                # circuit + calibration snapshot + result + provenance hash
ql reproduce <run_id> --days 90 --profile bad_day --html report.html
```

`ql reproduce` re-runs the stored circuit against a drifted device state and shows
exactly what changed (calibration drift, transpilation delta) with a Hellinger-fidelity
score and a verdict — the "aha" demo, fully offline and deterministic.

## Run the full platform

```bash
pip install -e packages/ql-core -e packages/ql-sdk -e packages/ql-crawler -e server
PYTHONPATH=server python scripts/seed_demo.py          # seed a walkable demo
PYTHONPATH=server uvicorn app.main:app --port 8000     # web app + API at :8000
```

Then browse: `/` (dashboard), `/leaderboard` (State of Quantum Hardware),
`/cards/<slug>` (public Result Card + badges), `/app/compliance`, `/trust/provenova`.

Push local runs to the hosted store:

```bash
ql login --token <api-key-from-seed> --endpoint http://localhost:8000
ql push
```

## Self-host (one command)

Free — production included — under the server's BUSL-1.1 license (see [Licensing](#licensing)):

```bash
cd deploy && docker compose up -d      # postgres + api + worker + Caddy
```

Set `QL_DATABASE_URL` to a SQLite URL for a tiny single-node deployment.

## Design guarantees

- **Immutable, hash-chained ledger.** `run_hash` is a portable Merkle content identity
  (stable across the offline and hosted stores — it is the ingest idempotency key);
  a per-workspace `chain_hash` links runs into a tamper-evident ledger. DB triggers
  reject any UPDATE/DELETE of sealed records.
- **Offline-verifiable provenance.** The exported `qlprov/run/1.0` document recomputes
  its own hash with no server (`provenova_core.verify_run_hash`).
- **Content-addressed dedup.** Calibration snapshots, circuits and compilations are
  stored once and referenced many times.
- **Compliance as a byproduct.** `EvidenceItem` points back into the core record; an
  attestation is an Ed25519 signature over a Merkle root of that evidence — mutate any
  referenced record and verification fails; revoke and it stops validating.
- **One schema, two backends.** SQLite (offline / self-host-small) and PostgreSQL (hosted).

## Tests

```bash
pip install pytest
python -m pytest        # attestation, crawler + corpus dedup, datasets, DOI/PID minting,
                        # rule engine, growth, plan tiering, sanitization, SEO pages,
                        # and the full end-to-end server flow
```

## Non-goals (PRD §4)

Not quantum hardware/control electronics, not a circuit IDE or scheduler, not an
error-mitigation product, not a general MLOps platform, and not an accredited auditor —
we produce the evidence and attestation. Quantum sensing is a future (O2) expansion.

*See [Licensing](#licensing) above; per-directory `LICENSE` files are authoritative.*
