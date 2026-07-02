"""Canonical JSON hashing + Merkle run-hashing.

Everything hashed in QuantumLedger passes through :func:`canonical_bytes` so that
SQLite and PostgreSQL — and any external verifier — produce byte-identical input
to SHA-256. This is what makes the ``run_hash`` reproducible and the exported
``qlprov/run/1.0`` document verifiable offline.

Design (see the PRD §10 and the provenance-core design):

* RFC 8785-style canonical JSON: sorted keys, no insignificant whitespace,
  UTF-8, floats normalized to a fixed precision so probabilities/calibration
  values serialize identically on every backend.
* Leaf content hashes for circuits, compilations, calibration snapshots, results.
* ``run_hash`` is a Merkle root over *labeled* leaves, chained to the previous
  run's hash (per-workspace append-only ledger) — altering an old run breaks
  every subsequent hash.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

# Number of significant figures floats are rounded to before hashing. Chosen so
# that shot probabilities and calibration parameters are stable across backends
# and Python float repr differences, while preserving meaningful precision.
FLOAT_SIGFIGS = 12

MERKLE_ALGO = "sha256-merkle/1.0"
GENESIS = "GENESIS"


def _normalize(obj: Any) -> Any:
    """Recursively normalize a value for canonical serialization."""
    if isinstance(obj, float):
        if obj != obj:  # NaN
            return "NaN"
        if obj in (float("inf"), float("-inf")):
            return "Infinity" if obj > 0 else "-Infinity"
        # Round to FLOAT_SIGFIGS significant figures, then collapse -0.0 -> 0.0.
        if obj == 0:
            return 0.0
        from math import floor, log10

        digits = FLOAT_SIGFIGS - 1 - floor(log10(abs(obj)))
        rounded = round(obj, digits)
        return rounded + 0.0
    if isinstance(obj, dict):
        return {str(k): _normalize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_normalize(v) for v in obj]
    return obj


def canonical_bytes(obj: Any) -> bytes:
    """Serialize ``obj`` to canonical JSON bytes (sorted keys, no whitespace)."""
    return json.dumps(
        _normalize(obj),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def sha256_hex(obj: Any) -> str:
    """SHA-256 hex digest of the canonical JSON encoding of ``obj``."""
    return hashlib.sha256(canonical_bytes(obj)).hexdigest()


def sha256_bytes(data: bytes) -> str:
    """SHA-256 hex digest of raw bytes (for blob content addressing)."""
    return hashlib.sha256(data).hexdigest()


def merkle_root(leaves: Iterable[str]) -> str:
    """Standard binary Merkle root over a list of hex-string leaves.

    Empty -> hash of the empty marker. Odd levels duplicate the last node.
    """
    level = list(leaves)
    if not level:
        return hashlib.sha256(b"EMPTY").hexdigest()
    while len(level) > 1:
        if len(level) % 2 == 1:
            level.append(level[-1])
        nxt = []
        for i in range(0, len(level), 2):
            combined = (level[i] + level[i + 1]).encode("utf-8")
            nxt.append(hashlib.sha256(combined).hexdigest())
        level = nxt
    return level[0]


# ---------------------------------------------------------------------------
# Leaf content hashes
# ---------------------------------------------------------------------------

def circuit_hash(fmt: str, source: str) -> str:
    return sha256_hex({"format": fmt, "source": source})


def compilation_hash(fmt: str, transpiled_source: str) -> str:
    return sha256_hex({"format": fmt, "source": transpiled_source})


def calibration_hash(payload: dict) -> str:
    return sha256_hex(payload)


def counts_hash(counts: dict[str, int] | None, shots: int) -> str:
    return sha256_hex({"counts": counts or {}, "shots": shots})


def backend_identity_hash(
    vendor: str,
    name: str,
    n_qubits: int | None,
    basis_gates: list[str] | None,
    coupling_map: list[list[int]] | None,
) -> str:
    return sha256_hex(
        {
            "vendor": vendor,
            "name": name,
            "n_qubits": n_qubits,
            "basis_gates": sorted(basis_gates or []),
            "coupling_map": coupling_map,
        }
    )


# ---------------------------------------------------------------------------
# Run hash — Merkle-style binding + hash chain
# ---------------------------------------------------------------------------

def compute_run_hash(
    *,
    schema_version: str,
    circuit_sha256: str,
    compilation_sha256: str | None,
    backend_identity: str,
    calibration_sha256: str,
    shots: int,
    seed_simulator: int | None,
    seed_transpiler: int | None,
    execution_params: dict | None,
    result_counts_hashes: list[str],
) -> tuple[str, str, dict]:
    """Compute the portable, Merkle-bound run hash.

    Returns ``(run_hash, inputs_root, leaves)`` where ``leaves`` is the labeled
    leaf map embedded in the exported provenance document so any single input
    can be shown to have diverged. ``run_hash`` is a pure content identity — it
    does NOT depend on ledger position, so a run keeps the same hash when it is
    pushed from a local store to the hosted store (the ingest idempotency key).
    """
    execution_leaf = sha256_hex(
        {
            "shots": shots,
            "seed_simulator": seed_simulator,
            "seed_transpiler": seed_transpiler,
            "params": execution_params or {},
        }
    )
    results_leaf = merkle_root(result_counts_hashes)
    leaves = {
        "schema": schema_version,
        "circuit": circuit_sha256,
        "compilation": compilation_sha256 or "none",
        "backend": backend_identity,
        "calibration": calibration_sha256,
        "execution": execution_leaf,
        "results": results_leaf,
    }
    # Each labeled leaf is itself hashed so a verifier can point at exactly which
    # leaf diverged (calibration vs transpilation vs results ...).
    labeled = [sha256_hex({k: leaves[k]}) for k in sorted(leaves)]
    inputs_root = merkle_root(labeled)
    run_hash = sha256_hex({"inputs_root": inputs_root})
    return run_hash, inputs_root, leaves


def compute_chain_hash(prev_chain_hash: str | None, run_hash: str) -> str:
    """Per-workspace append-only ledger link: H(prev_chain_hash || run_hash).

    Altering any historical run changes its ``run_hash`` and breaks every
    subsequent ``chain_hash`` — the tamper-evidence guarantee (verify_chain).
    """
    return sha256_hex({"prev": prev_chain_hash or GENESIS, "run": run_hash})
