"""Record + seal immutable runs, and reproduce-and-diff.

``record_run`` is the single path that writes a Run: it content-addresses the
circuit / compilation / calibration, executes deterministically, writes a Result,
computes the Merkle ``run_hash`` chained to the workspace head, and seals the run
(``pending -> completed``). ``reproduce_run`` re-runs a stored circuit under a
drifted (or substituted) device state and records a ``ReproductionEvent``.
"""

from __future__ import annotations

import datetime as _dt
import json

from sqlalchemy.orm import Session

from .. import content_address as ca
from .. import hashing
from ..models import (
    STATUS_COMPLETED,
    ReproductionEvent,
    Result,
    Run,
    Workspace,
)
from ..simulate import bridge, engine
from ..simulate.drift import drift_calibration
from . import diff_engine


def _parse_ts(value) -> _dt.datetime:
    try:
        return _dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return _dt.datetime.now(_dt.timezone.utc)


def record_run(
    session: Session,
    *,
    workspace: Workspace,
    qc,
    backend_spec: dict,
    calibration_payload: dict | None = None,
    shots: int = 1024,
    sim_shots: int | None = None,
    seed: int = 1337,
    seed_transpiler: int = 42,
    optimization_level: int = 1,
    exec_engine: str = "pure",
    account_id: str | None = None,
    project: str | None = None,
    logical_source: str | None = None,
    logical_fmt: str = "qlir/1.0",
    precomputed_counts: dict | None = None,
    capture_status: str = "complete",
) -> Run:
    n_qubits = qc.num_qubits
    if calibration_payload is None:
        calibration_payload = engine.default_simulator_calibration(n_qubits, backend_spec["name"])

    backend = ca.get_or_create_backend(
        session,
        vendor=backend_spec["vendor"],
        name=backend_spec["name"],
        kind=backend_spec.get("kind", "simulator"),
        n_qubits=n_qubits,
        basis_gates=backend_spec.get("basis_gates"),
        coupling_map=backend_spec.get("coupling_map"),
    )

    # ``sim_shots`` bounds the simulation/sampling loop independently of the
    # recorded ``shots``. Ingest passes a clamped sim_shots (the user's real shot
    # count is preserved on the Run) so a huge claimed shot count can't drive an
    # unbounded sampling loop — the counts are overridden by precomputed values.
    result = engine.execute(
        qc,
        calibration=calibration_payload,
        shots=shots if sim_shots is None else sim_shots,
        seed=seed,
        seed_transpiler=seed_transpiler,
        basis_gates=backend_spec.get("basis_gates"),
        coupling_map=backend_spec.get("coupling_map"),
        optimization_level=optimization_level,
        engine=exec_engine,
    )
    if precomputed_counts is not None:
        # Capture the user's actual result; keep the engine's transpilation/metrics.
        result.counts = dict(precomputed_counts)
        result.distribution = engine.counts_to_distribution(result.counts)

    logical_ir = bridge.ir_to_dict(bridge.qiskit_to_ir(qc))
    circuit = ca.get_or_create_circuit(
        session,
        fmt=logical_fmt,
        source=logical_source or json.dumps(logical_ir),
        n_qubits=n_qubits,
        n_clbits=n_qubits,
        metadata={"gates": len(logical_ir["gates"])},
    )
    compilation = ca.get_or_create_compilation(
        session,
        circuit=circuit,
        backend=backend,
        fmt=result.transpiled_fmt,
        transpiled_source=result.transpiled_source,
        optimization_level=optimization_level,
        transpiler_options={"seed_transpiler": seed_transpiler},
        metrics=result.metrics,
        toolchain={"engine": exec_engine},
    )
    calib = ca.get_or_create_calibration(
        session,
        backend=backend,
        payload=calibration_payload,
        captured_at=_parse_ts(calibration_payload.get("captured_at")),
        source=calibration_payload.get("provenance", {}).get("source", "simulated"),
    )

    now = _dt.datetime.now(_dt.timezone.utc)
    run = Run(
        workspace_id=workspace.id,
        account_id=account_id,
        circuit_id=circuit.id,
        compilation_id=compilation.id,
        backend_id=backend.id,
        calibration_snapshot_id=calib.id,
        shots=shots,
        seed_simulator=seed,
        seed_transpiler=seed_transpiler,
        execution_params={"optimization_level": optimization_level, "engine": exec_engine},
        project=project,
        capture_status=capture_status,
        started_at=now,
    )
    session.add(run)
    session.flush()

    counts_hash = hashing.counts_hash(result.counts, shots)
    retained = "counts" if workspace.retention_mode != "distribution_only" else "distribution"
    session.add(
        Result(
            run_id=run.id,
            result_index=0,
            counts=result.counts if retained == "counts" else None,
            counts_sha256=counts_hash,
            distribution=result.distribution,
            shots=shots,
            retained=retained,
        )
    )
    session.flush()

    run_hash, inputs_root, leaves = hashing.compute_run_hash(
        schema_version=run.provenance_schema_version,
        circuit_sha256=circuit.content_sha256,
        compilation_sha256=compilation.transpiled_sha256,
        backend_identity=backend.identity_hash,
        calibration_sha256=calib.content_sha256,
        shots=shots,
        seed_simulator=seed,
        seed_transpiler=seed_transpiler,
        execution_params=run.execution_params,
        result_counts_hashes=[counts_hash],
    )
    chain_hash = hashing.compute_chain_hash(workspace.chain_head, run_hash)
    run.run_hash = run_hash
    run.inputs_root = inputs_root
    run.merkle_leaves = leaves
    run.prev_chain_hash = workspace.chain_head
    run.chain_hash = chain_hash
    run.status = STATUS_COMPLETED
    run.finished_at = _dt.datetime.now(_dt.timezone.utc)
    workspace.chain_head = chain_hash
    session.flush()
    return run


def reproduce_run(
    session: Session,
    original_run: Run,
    *,
    workspace: Workspace,
    days: float = 30.0,
    profile: str = "typical",
    drift_seed: int = 7,
    target_backend_spec: dict | None = None,
    exec_engine: str = "pure",
    account_id: str | None = None,
) -> tuple[Run, ReproductionEvent]:
    circuit = original_run.circuit
    logical_ir = bridge.dict_to_ir(json.loads(circuit.source))
    qc = bridge.qiskit_from_ir(logical_ir)

    orig_backend = original_run.backend
    kind = "reproduce"
    if target_backend_spec is not None and target_backend_spec.get("name") != orig_backend.name:
        kind = "cross_vendor"
        backend_spec = target_backend_spec
        base_cal = engine.default_simulator_calibration(qc.num_qubits, target_backend_spec["name"])
        new_cal = drift_calibration(base_cal, days=days, seed=drift_seed, profile=profile)
    else:
        backend_spec = {
            "vendor": orig_backend.vendor,
            "name": orig_backend.name,
            "kind": orig_backend.kind,
            "basis_gates": orig_backend.basis_gates,
            "coupling_map": orig_backend.coupling_map,
        }
        new_cal = drift_calibration(
            original_run.calibration.payload, days=days, seed=drift_seed, profile=profile
        )

    new_run = record_run(
        session,
        workspace=workspace,
        qc=qc,
        backend_spec=backend_spec,
        calibration_payload=new_cal,
        shots=original_run.shots,
        seed=original_run.seed_simulator or 1337,
        seed_transpiler=original_run.seed_transpiler or 42,
        exec_engine=exec_engine,
        account_id=account_id,
        project=original_run.project,
    )

    orig_res = original_run.results[0]
    new_res = new_run.results[0]
    diff = diff_engine.build_diff(
        dist_a=orig_res.distribution,
        dist_b=new_res.distribution,
        counts_a=orig_res.counts,
        counts_b=new_res.counts,
        cal_a=original_run.calibration.payload,
        cal_b=new_cal,
        metrics_a=original_run.compilation.metrics if original_run.compilation else {},
        metrics_b=new_run.compilation.metrics if new_run.compilation else {},
        backend_a=orig_backend.name,
        backend_b=backend_spec["name"],
    )
    event = ReproductionEvent(
        original_run_id=original_run.id,
        reproduced_run_id=new_run.id,
        kind=kind,
        submitted_by=account_id,
        status="verified",
        diff=diff,
        reproducibility_score=diff["scores"]["hellinger_fidelity"],
        verdict=diff["verdict"],
        calibration_drift=diff["calibration_drift"],
        transpilation_delta=diff["transpilation_delta"],
    )
    session.add(event)
    session.flush()
    return new_run, event
