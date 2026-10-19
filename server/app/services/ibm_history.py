"""IBM calibration snapshots from Qiskit's open-source fake backends.

Every ``props_<device>.json`` in Qiskit's fake backends is a real
BackendProperties dump from the named IBM Quantum machine (Apache-2.0),
stamped with the device's own ``last_update_date``. ``derive`` reduces one to
the aggregates the corpus keeps; ``snapshot`` wraps that with its provenance
(a commit-pinned permalink and the raw file's SHA-256).

Shared by ``scripts/build_ibm_history.py`` (which walks the files' whole git
history) and the ``ibm`` collector (which checks the current tree each week).
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import statistics

REPO = "Qiskit/qiskit-ibm-runtime"
BACKENDS_DIR = "qiskit_ibm_runtime/fake_provider/backends"
LICENSE_REF = "Apache-2.0 · Qiskit fake backend calibration snapshot"
NOTE = ("Aggregates of real IBM Quantum calibration snapshots published in Qiskit's "
        "fake backends (Apache-2.0). raw_ref is the exact source file at its commit.")
TWO_QUBIT_GATES = {"cx", "ecr", "cz"}


def _to_us(value: float, unit: str) -> float | None:
    unit = (unit or "").strip().lower().replace("µ", "u")
    scale = {"us": 1.0, "ns": 1e-3, "ms": 1e3, "s": 1e6}.get(unit)
    return value * scale if scale is not None and value is not None else None


def _sig(v: float | None, digits: int = 6) -> float | None:
    return None if v is None else float(f"{v:.{digits}g}")


def utc_iso(stamp: str) -> str:
    dt = _dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt.astimezone(_dt.timezone.utc).replace(microsecond=0).isoformat()


def derive(props: dict) -> dict:
    """Aggregate metrics from a raw BackendProperties dict."""
    t1, t2, ro = [], [], []
    for q in props.get("qubits") or []:
        for p in q:
            name, val = p.get("name"), p.get("value")
            if not isinstance(val, (int, float)):
                continue
            if name == "T1":
                v = _to_us(val, p.get("unit"))
                if v and v > 0:
                    t1.append(v)
            elif name == "T2":
                v = _to_us(val, p.get("unit"))
                if v and v > 0:
                    t2.append(v)
            elif name == "readout_error" and 0 <= val < 0.5:
                ro.append(val)
    twoq = []
    for g in props.get("gates") or []:
        if len(g.get("qubits") or []) != 2 and g.get("gate") not in TWO_QUBIT_GATES:
            continue
        for p in g.get("parameters") or []:
            v = p.get("value")
            # error 1.0 marks a broken coupler; 0 is a placeholder, not a measurement
            if p.get("name") == "gate_error" and isinstance(v, (int, float)) and 0 < v < 0.5:
                twoq.append(v)
    out = {
        "n_qubits": len(props.get("qubits") or []),
        "median_2q_error": _sig(statistics.median(twoq)) if twoq else None,
        "best_2q_fidelity": _sig(1 - min(twoq)) if twoq else None,
        "median_t1_us": _sig(statistics.median(t1), 5) if t1 else None,
        "median_t2_us": _sig(statistics.median(t2), 5) if t2 else None,
        "median_readout_error": _sig(statistics.median(ro)) if ro else None,
        "source": "ibm",
    }
    return {k: v for k, v in out.items() if v is not None}


def snapshot(body: bytes, *, repo: str, commit: str, path: str,
             commit_date: str | None = None) -> tuple[str, dict] | None:
    """(backend_name, corpus-series snapshot) for one raw props file, or None.

    Only files that name a real device (``ibm_*`` / ``ibmq*``) and carry a
    usable measurement are kept; ``fake_*`` names don't identify a machine.
    """
    try:
        props = json.loads(body)
    except ValueError:
        return None
    name = (props.get("backend_name") or "").strip()
    stamp = props.get("last_update_date")
    if not stamp or not (name.startswith("ibm_") or name.startswith("ibmq")):
        return None
    dm = derive(props)
    if "median_2q_error" not in dm and "median_t1_us" not in dm:
        return None
    snap = {
        "captured_at": utc_iso(stamp),
        "derived_metrics": dm,
        "raw_ref": f"https://github.com/{repo}/blob/{commit}/{path}",
        "raw_sha256": hashlib.sha256(body).hexdigest(),
    }
    if commit_date:
        snap["commit_date"] = commit_date
    return name, snap


def series(name: str, snapshots: list[dict]) -> dict:
    """A ``qlprov/corpus-series/1.0`` document for one device."""
    return {
        "schema": "qlprov/corpus-series/1.0",
        "provider": "ibm",
        "backend_id": name,
        "source": "ibm",
        "license_ref": LICENSE_REF,
        "redistributable_raw": True,
        "note": NOTE,
        "snapshots": sorted(snapshots, key=lambda s: s["captured_at"]),
    }
