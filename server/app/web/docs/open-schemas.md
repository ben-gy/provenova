# Open schemas (qlprov)

Provenova's provenance format is an **open, versioned standard** called `qlprov`, not a proprietary
blob. The schemas are JSON Schema (draft 2020-12) and ship inside `quantumledger-core` at
`quantumledger_core/schemas/qlprov/`. Because the format is open and self-verifying, a run you export can be
read, validated, and hash-checked by anyone — no Provenova server required.

## `qlprov/run/1.0`

The full record of a run: its circuit, backend identity, calibration snapshot, compilation metrics, result
distribution, and the hashes that bind them (`run_hash`, chain hashes). This is what `ql show <id>` renders
and what `GET /api/v1/runs/<id>` returns.

**Self-verifying.** An exported `qlprov/run/1.0` document **recomputes its own `run_hash`** offline:

```python
from quantumledger_core import verify_run_hash
assert verify_run_hash(document)   # True iff the leaf hashes still produce run_hash
```

The `run_hash` is the SHA-256 of a Merkle root over the seven labeled leaf hashes embedded in
`merkle.leaves` (schema, circuit, compilation, backend, calibration, execution, results) — any alteration
to those bound hashes changes the recomputed root and the check fails. The human-readable body fields can
be validated by cross-checking them against the leaves: `circuit.content_sha256`, `backend.identity_hash`,
and `calibration.content_sha256` equal their leaves directly, and the `results` leaf is the Merkle root of
each result's `counts_sha256`. The hash is also stable across the local and hosted stores, which is why it
doubles as the idempotency key for `ql push`.

## `qlprov/calibration/1.0`

The normalized calibration-snapshot format: per-qubit T1 / T2 / readout error, per-gate error rates, and
capture metadata. Every vendor's native calibration is normalized to this shape on capture and by the
crawler, which is what makes cross-vendor comparison (the [leaderboard](/docs/corpus-and-leaderboard))
possible.

## Why open schemas matter

- **Portability** — your provenance isn't locked into one vendor or one server.
- **Verifiability** — trust the math, not the platform: recompute the hash yourself.
- **Interoperability** — tools, auditors, and the public corpus all speak the same format.

Next: [Deployment & self-hosting](/docs/deployment).
