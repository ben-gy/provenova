"""Build a Qiskit-Aer NoiseModel from a QuantumLedger calibration snapshot.

Only used on the optional Aer execution path. The pure-Python engine applies its
own (simpler) calibration-driven noise directly. Either way, the calibration
snapshot deterministically parameterizes the noise, so drift is observable.
"""

from __future__ import annotations


def noise_from_calibration(payload: dict):  # pragma: no cover - requires qiskit-aer
    from qiskit_aer.noise import (
        NoiseModel,
        ReadoutError,
        depolarizing_error,
        thermal_relaxation_error,
    )

    nm = NoiseModel()
    qubits = {q["index"]: q for q in payload.get("qubits", [])}

    for idx, q in qubits.items():
        p01 = q.get("prob_meas1_prep0")
        p10 = q.get("prob_meas0_prep1")
        if p01 is None and p10 is None and q.get("readout_error") is not None:
            p01 = p10 = q["readout_error"] * 0.5
        if p01 is not None or p10 is not None:
            p01 = p01 or 0.0
            p10 = p10 or 0.0
            ro = ReadoutError([[1 - p01, p01], [p10, 1 - p10]])
            nm.add_readout_error(ro, [idx])

    for g in payload.get("gates", []):
        err = g.get("error")
        if not err:
            continue
        qs = g["qubits"]
        length_ns = g.get("length_ns") or 0.0
        try:
            if len(qs) == 1 and qubits.get(qs[0]):
                q = qubits[qs[0]]
                t1 = (q.get("T1_us") or 1e6) * 1e3  # us -> ns
                t2 = (q.get("T2_us") or 1e6) * 1e3
                terr = thermal_relaxation_error(t1, min(t2, 2 * t1), length_ns)
                derr = depolarizing_error(min(err, 0.5), 1)
                nm.add_quantum_error(terr.compose(derr), g["gate"], qs)
            else:
                derr = depolarizing_error(min(err, 0.75), len(qs))
                nm.add_quantum_error(derr, g["gate"], qs)
        except Exception:
            continue
    return nm
