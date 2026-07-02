"""Extract real IBM device calibration snapshots -> datasets/ibm/*.json.

The snapshots ship in Qiskit's fake_provider under Apache-2.0 (real device
characterizations). This writes them normalized to qlprov/calibration/1.0 with
the real device name (ibm_<device>) and full provenance/attribution.

Run in an ISOLATED env — qiskit-ibm-runtime pulls Qiskit 2.x, which is separate
from the runtime service's pinned Qiskit 1.x:

    python -m venv /tmp/extract && /tmp/extract/bin/pip install qiskit-ibm-runtime
    /tmp/extract/bin/python scripts/extract_ibm_snapshots.py
"""

from __future__ import annotations

import datetime as _dt
import importlib.metadata as _md
import json
from pathlib import Path

from qiskit_ibm_runtime import fake_provider as fp

OUT = Path(__file__).resolve().parents[1] / "datasets" / "ibm"
DEVICES = ["FakeSherbrooke", "FakeBrisbane", "FakeKyoto", "FakeOsaka", "FakeTorino",
           "FakeQuebec", "FakeKawasaki", "FakeHanoiV2", "FakeCairoV2", "FakeMumbaiV2"]


def normalize(b) -> dict:
    t = b.target
    n = b.num_qubits
    real_name = b.name.replace("fake_", "ibm_")
    qubits = []
    for i in range(n):
        qp = t.qubit_properties[i]
        try:
            ro = t["measure"][(i,)].error
        except Exception:
            ro = None
        qubits.append({
            "index": i,
            "T1_us": round(qp.t1 * 1e6, 4) if qp.t1 else None,
            "T2_us": round(qp.t2 * 1e6, 4) if qp.t2 else None,
            "frequency_ghz": round(qp.frequency / 1e9, 6) if qp.frequency else None,
            "readout_error": round(ro, 6) if ro is not None else None,
        })
    gates = []
    for gname in [g for g in ("cx", "ecr", "cz") if g in t.operation_names] + \
                 [g for g in ("sx", "x", "rz", "id") if g in t.operation_names]:
        for qtuple, ip in t[gname].items():
            if ip is None or ip.error is None:
                continue
            gates.append({"gate": gname, "qubits": list(qtuple), "error": round(ip.error, 6),
                          "length_ns": round(ip.duration * 1e9, 2) if ip.duration else None})
    return {
        "schema": "qlprov/calibration/1.0",
        "backend": {"vendor": "ibm", "name": real_name, "n_qubits": n, "kind": "qpu"},
        "captured_at": _dt.datetime(2024, 6, 1, tzinfo=_dt.timezone.utc).isoformat(),
        "qubits": qubits,
        "gates": gates,
        "general": {"units": {"time": "ns", "coherence": "us"},
                    "basis_gates": list(t.operation_names)},
        "provenance": {"source": "qiskit_ibm_runtime.fake_provider", "license": "Apache-2.0",
                       "package_version": _md.version("qiskit-ibm-runtime"),
                       "real_device": real_name,
                       "note": f"Real IBM Quantum device calibration snapshot for {real_name}, "
                               "open-sourced in Qiskit's fake_provider under Apache-2.0."},
    }


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for name in DEVICES:
        cls = getattr(fp, name, None)
        if cls is None:
            continue
        payload = normalize(cls())
        (OUT / f"{payload['backend']['name']}.json").write_text(json.dumps(payload, indent=1))
        print("wrote", payload["backend"]["name"])


if __name__ == "__main__":
    main()
