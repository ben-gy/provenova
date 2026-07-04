# End-to-end test

A single, repeatable mock E2E that installs the libraries and exercises **every
moving part with real data**, then asserts the results.

```bash
make e2e            # or: bash scripts/e2e/run.sh
```

## What it does

`run.sh` (orchestrator):
1. Creates `.venv` if missing and **installs every package editable**
   (`ql-core`, `ql-sdk[aer]`, `ql-crawler`, `server`).
2. Spins up a **fully isolated, throwaway environment** — a fresh temp SQLite
   DB, SDK home, and attestation signing key, on a random free port. It never
   touches your real `~/.provenova` (or legacy `~/.quantumledger`) or repo database.
3. `provision.py` bootstraps the DB, sets a known password on the (Enterprise,
   superadmin) bootstrap account, and seeds a walkable dataset via
   `demo_seed.seed_workspace` (runs, a reproduction, a published card, the
   public corpus, evaluated frameworks + a signed attestation).
4. Starts the FastAPI server and waits for `/api/v1/health`.
5. Runs `driver.py`, which asserts across every layer.
6. Tears everything down (kills the server, removes the temp dir) via a `trap`.

`driver.py` phases (exit non-zero on any failure):

| | Area | Checks |
|-|------|--------|
| A | Offline SDK / CLI | `ql init/demo/list/show/reproduce/connectors/config` |
| B/C | Health, auth, API key | health, admin login, `/me`, mint key, key auth |
| D/E | CLI connectivity + push | `ql login --verify`, `ql doctor`, `ql push`, read back over API |
| — | Provenance | server-exported `qlprov/run` re-verifies its own hash offline |
| F | Reproduce + cards | reproduce, publish, public card JSON, citation, badge SVG |
| G | Compliance | frameworks, enable, evaluate, status, gaps, attest, verify, revoke |
| H | Corpus / leaderboard | leaderboard populated from the crawled corpus |
| I | Web UI smoke | dashboard, docs (+ generated API ref), pricing, app pages, run detail |
| J | Multi-tenant security | anon → 401, cross-tenant → 404, `?workspace_id=` leaks nothing, free-tier gating → 402 |
| K | Interop error clarity | `ql doctor` at a dead endpoint fails clearly; `ql login` flags a bad token |

## Requirements

`python3.12` (or `python3`) and `curl` on PATH. First run installs Qiskit/Aer,
so it may take a few minutes; subsequent runs are fast.
