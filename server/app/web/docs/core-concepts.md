# Core concepts

These are the ideas the rest of the product is built on. Understanding them makes every screen legible.

## The run — the atomic record

A **run** is a single execution of a circuit on a backend. It is stored immutably, bound to four things:

- the **circuit** (the quantum program, content-addressed),
- the **backend** (device identity: vendor, qubits, basis gates, coupling map),
- the **calibration snapshot** (the device's error rates and coherence times at run time),
- the **result** (the measured bitstring distribution).

Because all four are captured together, a run is *self-contained* — you never have to guess which device
state produced a result.

## Calibration snapshots

A **calibration snapshot** is a time-stamped capture of a device's state: per-qubit T1/T2 coherence times,
readout error, and per-gate error rates. Quantum hardware drifts continuously, so the *same circuit* on the
*same device* can give different results a day apart. Binding each run to its snapshot is what makes results
comparable and reproducible at all.

## Content-addressing & dedup

Circuits, compilations, and calibration snapshots are **content-addressed**: stored under the SHA-256 of
their canonical bytes. Two runs that share a circuit reference the *same* circuit row. This deduplicates
storage and means identity is intrinsic to the content, not an arbitrary database id.

Canonical hashing uses RFC 8785-style JSON normalization with controlled float precision, so the same
logical object always hashes the same way across machines and languages.

## Immutability & the hash chain

- **`run_hash`** is a **Merkle root** over a run's constituent leaves (circuit, backend, calibration,
  result). It is a portable content identity — recomputable offline, identical in the local and hosted
  stores, and used as the idempotency key when you `ql push`.
- **`chain_hash`** links each run to the previous one in a workspace, forming a **tamper-evident ledger**:
  altering an old run breaks every chain hash after it.
- **Database triggers reject any `UPDATE` or `DELETE`** of a sealed record. The ledger is append-only by
  construction, not just by convention.

## Offline verifiability

A run exported as a `qlprov/run/1.0` document **recomputes its own hash with no server**
(`quantumledger_core.verify_run_hash`). Anyone you share it with can confirm it hasn't been altered,
without trusting QuantumLedger. See [Open schemas](/docs/open-schemas).

## Why this matters downstream

Every other pillar leans on this foundation:

- **Reproduce** compares two runs whose provenance is fully known.
- **Result cards** cite an immutable `run_hash`.
- **Compliance evidence** is just a pointer into this record plus the target's content hash — so an
  **attestation** signs over hashes that break if any referenced record is mutated.

That's the design guarantee: *compliance and citation are byproducts of an honest, immutable record.*

Next: [Capturing runs](/docs/capturing-runs).
