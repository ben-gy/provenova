"""Build the exportable, offline-verifiable ``qlprov/run/1.0`` document."""

from __future__ import annotations

from .models import Run

MERKLE_ALGO = "sha256-merkle/1.0"


def build_run_doc(run: Run) -> dict:
    """Assemble the portable provenance record for a sealed Run.

    Anyone with this document can recompute ``inputs_root`` and ``run_hash`` and
    verify integrity offline via :func:`provenova_core.verify_run_hash`.
    """
    circuit = run.circuit
    comp = run.compilation
    backend = run.backend
    calib = run.calibration
    return {
        "schema": run.provenance_schema_version,
        "run_id": run.id,
        "run_hash": run.run_hash,
        "chain_hash": run.chain_hash,
        "prev_chain_hash": run.prev_chain_hash,
        "workspace": {"id": run.workspace_id},
        "created_by": run.account_id,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "circuit": {
            "content_sha256": circuit.content_sha256,
            "format": circuit.fmt,
            "n_qubits": circuit.n_qubits,
            "n_clbits": circuit.n_clbits,
        },
        "compilation": None
        if comp is None
        else {
            "transpiled_sha256": comp.transpiled_sha256,
            "optimization_level": comp.optimization_level,
            "transpiler_options": comp.transpiler_options,
            "metrics": comp.metrics,
            "toolchain": comp.toolchain,
        },
        "backend": {
            "identity_hash": backend.identity_hash,
            "vendor": backend.vendor,
            "name": backend.name,
            "kind": backend.kind,
            "n_qubits": backend.n_qubits,
        },
        "calibration": {
            "snapshot_id": calib.id,
            "content_sha256": calib.content_sha256,
            "captured_at": calib.captured_at.isoformat() if calib.captured_at else None,
        },
        "execution": {
            "shots": run.shots,
            "seed_simulator": run.seed_simulator,
            "seed_transpiler": run.seed_transpiler,
            "params": run.execution_params or {},
        },
        "results": [
            {
                "result_index": r.result_index,
                "counts_sha256": r.counts_sha256,
                "distribution": r.distribution,
                "shots": r.shots,
                "retained": r.retained,
            }
            for r in run.results
        ],
        "merkle": {
            "leaves": run.merkle_leaves or {},
            "inputs_root": run.inputs_root,
            "algo": MERKLE_ALGO,
        },
    }
