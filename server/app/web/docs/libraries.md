# Libraries & downloads

QuantumLedger ships as four Python packages. The client side is open source (Apache-2.0); the hosted server
is source-available (BUSL-1.1). You only need the pieces relevant to what you're doing.

## `quantumledger` — the SDK & CLI

**Install:** `pip install quantumledger` · extras: `[aer]`, `[qiskit_runtime]`, `[braket]`, `[azure]` ·
**License:** Apache-2.0 · **Package dir:** `packages/ql-sdk`

The open-source client. What it gives you:

- the **`@ql.capture`** decorator / context manager to record quantum jobs;
- an **offline local ledger** (`quantumledger.store.LocalLedger`, SQLite, no account);
- the **`ql` command-line tool** — see the [CLI reference](/docs/cli);
- **vendor connectors** (a plugin system) for Aer, IBM Qiskit Runtime, Braket, Azure, IonQ;
- **content-hash-idempotent push** to a hosted store (`ql push`).

```bash
pip install "quantumledger[aer]"
```

## `quantumledger-core` — the provenance engine

**Install:** `pip install quantumledger-core` · extra: `[postgres]` · **License:** Apache-2.0 ·
**Package dir:** `packages/ql-core`

The shared foundation used by both the SDK and the server:

- the SQLAlchemy **provenance data model** (Backend, CalibrationSnapshot, Circuit, Compilation, Run,
  Result, ReproductionEvent);
- **canonical JSON hashing** + **Merkle `run_hash`** and the DB-trigger **immutability** layer;
- the deterministic **simulator + drift engine**;
- **reproduce / scoring / diff** (Hellinger fidelity, TVD, Jensen–Shannon, the diff engine);
- the open **`qlprov/*` JSON schemas** (see [Open schemas](/docs/open-schemas)).

Runs on SQLite (offline / small self-host) or PostgreSQL (hosted) from one schema.

## `quantumledger-crawler` — the corpus crawler

**Install:** `pip install quantumledger-crawler` · **License:** Apache-2.0 · **Package dir:**
`packages/ql-crawler`

Collects public-QPU calibration data, normalizes it to `qlprov/calibration/1.0`, applies terms-of-service
redistribution policies, deduplicates, and ingests it into the public corpus. Ships a `FixtureSource`
(offline) and a `LiveSource` skeleton for real vendor APIs. See
[Corpus & leaderboard](/docs/corpus-and-leaderboard).

## `quantumledger-server` — the hosted platform

**Install:** `pip install quantumledger-server` (or use the docker-compose in `deploy/`) ·
**License:** BUSL-1.1 (source-available) · **Package dir:** `server`

The FastAPI application: ingestion + read API, the server-rendered web UI, Result Cards + badge service, the
reproduce engine, the compliance rule engine, Ed25519 attestations, accounts/entitlements/RBAC, and admin.
See [Deployment & self-hosting](/docs/deployment) and the [API reference](/docs/api).

## Which do I need?

| Goal | Install |
|------|---------|
| Record & reproduce locally | `quantumledger[aer]` (pulls in `quantumledger-core`) |
| Contribute to / crawl the corpus | `quantumledger-crawler` |
| Run your own server | `quantumledger-server` + core, or the `deploy/` docker-compose |
| Just verify a shared `qlprov` document | `quantumledger-core` |

Next: [CLI reference](/docs/cli).
