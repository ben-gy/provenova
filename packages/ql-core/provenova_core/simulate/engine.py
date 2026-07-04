"""Execution facade: transpile with Qiskit, execute deterministically.

Default execution is the pure-Python engine (deterministic, dependency-light,
fully under our control and used by the test-suite). Qiskit-Aer is available via
``engine="aer"`` for higher-fidelity noise. Either path is seeded and stable.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import bridge, pure_python


@dataclass
class ExecResult:
    counts: dict[str, int]
    distribution: dict[str, float]
    transpiled_ir: dict
    transpiled_source: str
    transpiled_fmt: str
    metrics: dict
    basis_gates: list[str]
    coupling_map: list | None


def _qasm3(qc) -> tuple[str, str]:
    try:
        from qiskit import qasm3

        return qasm3.dumps(qc), "qasm3"
    except Exception:  # pragma: no cover
        import json

        return json.dumps(bridge.ir_to_dict(bridge.qiskit_to_ir(qc))), "qlir/1.0"


def counts_to_distribution(counts: dict[str, int]) -> dict[str, float]:
    total = sum(counts.values()) or 1
    return {k: v / total for k, v in counts.items()}


def default_simulator_calibration(n_qubits: int, name: str = "aer") -> dict:
    """A near-ideal calibration snapshot for a simulator backend.

    Even simulators get a calibration snapshot (the core differentiator: every
    run is bound to a device-state record); a pristine simulator's errors are ~0.
    """
    import datetime as _dt

    return {
        "schema": "qlprov/calibration/1.0",
        "backend": {"vendor": "local_sim", "name": name, "n_qubits": n_qubits, "kind": "simulator"},
        "captured_at": _dt.datetime(2026, 1, 1, tzinfo=_dt.timezone.utc).isoformat(),
        "qubits": [
            {"index": i, "T1_us": 200.0, "T2_us": 180.0, "readout_error": 0.001,
             "prob_meas0_prep1": 0.001, "prob_meas1_prep0": 0.001}
            for i in range(n_qubits)
        ],
        "gates": [{"gate": "cx", "qubits": [i, (i + 1) % max(n_qubits, 2)], "error": 0.001, "length_ns": 300}
                  for i in range(max(n_qubits - 1, 0))]
        + [{"gate": "sx", "qubits": [i], "error": 0.0002, "length_ns": 35} for i in range(n_qubits)],
        "general": {"units": {"time": "ns", "coherence": "us"}},
        "provenance": {"source": "simulated"},
    }


def execute(
    qc,
    *,
    calibration: dict | None = None,
    shots: int = 1024,
    seed: int = 1337,
    seed_transpiler: int = 42,
    basis_gates: list[str] | None = None,
    coupling_map: list | None = None,
    optimization_level: int = 1,
    engine: str = "pure",
) -> ExecResult:
    # Normalize away final measurements before transpiling: we always measure all
    # qubits for counts, so the transpiled artifact (and its hash) must not depend
    # on whether the caller's circuit carried explicit measurements. This keeps the
    # compilation hash identical whether a run is captured live or replayed on
    # ingest from the measurement-free logical IR.
    if getattr(qc, "num_clbits", 0):
        try:
            qc = qc.remove_final_measurements(inplace=False)
        except Exception:
            pass
    transpiled = bridge.transpile_qiskit(
        qc,
        basis_gates=basis_gates,
        coupling_map=coupling_map,
        optimization_level=optimization_level,
        seed=seed_transpiler,
    )
    ir = bridge.qiskit_to_ir(transpiled)
    metrics = bridge.circuit_metrics(transpiled)
    src, fmt = _qasm3(transpiled)

    if engine == "aer":
        counts = _run_aer(transpiled, calibration, shots, seed)
    else:
        counts = pure_python.run_counts(ir, calibration, shots, seed)

    return ExecResult(
        counts=counts,
        distribution=counts_to_distribution(counts),
        transpiled_ir=bridge.ir_to_dict(ir),
        transpiled_source=src,
        transpiled_fmt=fmt,
        metrics=metrics,
        basis_gates=basis_gates or bridge.TARGET_BASIS,
        coupling_map=coupling_map,
    )


def ideal(qc) -> dict[str, float]:
    transpiled = bridge.transpile_qiskit(qc)
    ir = bridge.qiskit_to_ir(transpiled)
    return pure_python.ideal_distribution(ir)


def _run_aer(qc, calibration, shots, seed):  # pragma: no cover - requires aer
    from qiskit_aer import AerSimulator

    from .noise import noise_from_calibration

    nm = noise_from_calibration(calibration) if calibration else None
    sim = AerSimulator(noise_model=nm)
    tqc = qc.copy()
    if not tqc.cregs:
        tqc.measure_all()
    result = sim.run(tqc, shots=shots, seed_simulator=seed).result()
    raw = result.get_counts()
    # normalize qiskit's big-endian, space-separated keys to little-endian compact
    counts: dict[str, int] = {}
    for k, v in raw.items():
        bit = k.replace(" ", "")[::-1]
        counts[bit] = counts.get(bit, 0) + v
    return counts
