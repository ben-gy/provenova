"""Materialize a pushed run bundle into the hosted store.

The bundle carries the full payloads (circuit IR, calibration, transpiled form,
counts). We reconstruct the logical circuit and replay it through the shared
``runner.record_run`` with the captured counts, so the content-addressed hashes
and the portable ``run_hash`` are recomputed server-side and verified against the
client's claim. Ingestion is idempotent by ``run_hash``.
"""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from provenova_core.models import Run, Workspace
from provenova_core.reproduce import runner
from provenova_core.simulate import bridge
from provenova_core.simulate.safety import assert_safe_circuit, clamp_shots


def materialize_bundle(session: Session, workspace: Workspace, bundle: dict) -> dict:
    claimed = bundle["provenance"]["run_hash"]
    existing = session.scalar(
        select(Run).where(Run.workspace_id == workspace.id, Run.run_hash == claimed)
    )
    if existing is not None:
        return {"status": "exists", "run_id": existing.id, "run_hash": claimed}

    circ = bundle["circuit"]
    # SECURITY: the circuit source is attacker-controlled JSON that we are about
    # to reconstruct into a real QuantumCircuit and RE-SIMULATE (record_run always
    # calls the 2**n statevector engine). Validate the gate allowlist and size
    # caps BEFORE reconstruction, exactly like the growth path — otherwise a huge
    # n_qubits OOMs the worker and unknown gate names hit the bridge dispatch.
    safe_source = assert_safe_circuit(json.loads(circ["source"]))
    ir = bridge.dict_to_ir(safe_source)
    qc = bridge.qiskit_from_ir(ir)

    be = bundle["backend"]
    runinfo = bundle.get("run") or {}
    params = runinfo.get("execution_params") or {}
    # Preserve the user's real shot count on the record, but bound the value that
    # drives the re-simulation sampling loop (counts are overridden below anyway).
    try:
        real_shots = max(1, int(runinfo.get("shots") or bundle["result"].get("shots") or 1024))
    except (TypeError, ValueError):
        real_shots = 1024
    run = runner.record_run(
        session,
        workspace=workspace,
        qc=qc,
        backend_spec={
            "vendor": be["vendor"],
            "name": be["name"],
            "kind": be.get("kind", "simulator"),
            "basis_gates": be.get("basis_gates"),
            "coupling_map": be.get("coupling_map"),
        },
        calibration_payload=bundle["calibration"],
        shots=real_shots,
        sim_shots=clamp_shots(real_shots),
        seed=runinfo.get("seed_simulator") or 1337,
        seed_transpiler=runinfo.get("seed_transpiler") or 42,
        optimization_level=params.get("optimization_level", 1),
        exec_engine=params.get("engine", "pure"),
        precomputed_counts=bundle["result"].get("counts"),
        project=runinfo.get("project"),
        capture_status=runinfo.get("capture_status", "complete"),
    )
    session.commit()
    return {
        "status": "created",
        "run_id": run.id,
        "run_hash": run.run_hash,
        "hash_matched_client": run.run_hash == claimed,
    }
