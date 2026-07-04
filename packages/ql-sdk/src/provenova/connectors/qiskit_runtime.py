"""Qiskit Runtime (IBM) connector — normalizes BackendProperties to qlprov."""

from __future__ import annotations

import datetime as _dt

from .base import CaptureBundle, Connector, gap, normalize_counts


class QiskitRuntimeConnector(Connector):
    name = "qiskit_runtime"
    provider = "ibm_quantum"

    def claims(self, obj) -> bool:
        return (type(obj).__module__ or "").startswith("qiskit_ibm_runtime")

    def extract(self, *, circuit=None, backend=None, job=None, result=None) -> CaptureBundle:
        if backend is None and job is not None:
            try:
                backend = job.backend()
            except Exception:
                backend = None
        name = getattr(backend, "name", "ibm_backend")
        bundle = CaptureBundle(
            provider="ibm_quantum",
            backend_name=name if isinstance(name, str) else "ibm_backend",
            backend_kind="qpu",
            n_qubits=getattr(backend, "num_qubits", None),
            basis_gates=list(getattr(backend, "basis_gates", []) or []) or ["rz", "sx", "x", "cx", "id"],
            coupling_map=[list(e) for e in (getattr(backend, "coupling_map", None) or [])] or None,
            circuit=circuit,
        )
        res = result or (job.result() if job is not None else None)
        if res is not None:
            try:
                raw = res.get_counts() if hasattr(res, "get_counts") else res
                if isinstance(raw, list):
                    raw = raw[0]
                bundle.counts = normalize_counts(raw)
                bundle.shots = sum(bundle.counts.values())
            except Exception:
                bundle.gaps.append(gap("counts", "sdk_version"))
        bundle.calibration = self.fetch_calibration(backend) if backend is not None else None
        return bundle

    def fetch_calibration(self, backend) -> dict:
        payload = {
            "schema": "qlprov/calibration/1.0",
            "backend": {"vendor": "ibm", "name": getattr(backend, "name", "ibm_backend"),
                        "n_qubits": getattr(backend, "num_qubits", None), "kind": "qpu"},
            "captured_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "qubits": [], "gates": [], "general": {}, "fleet_metrics": {},
            "provenance": {"source": "vendor_api"}, "gaps": [],
        }
        props = None
        try:
            props = backend.properties()
        except Exception:
            payload["gaps"].append(gap("calibration", "sdk_version", "backend.properties() raised"))
        if props is None:
            payload["gaps"].append(gap("calibration", "vendor_not_exposed", "no BackendProperties"))
            return payload
        try:
            n = backend.num_qubits
            for q in range(n):
                payload["qubits"].append({
                    "index": q,
                    "T1_us": _scaled(props, "t1", q, 1e6),
                    "T2_us": _scaled(props, "t2", q, 1e6),
                    "frequency_ghz": _scaled(props, "frequency", q, 1e-9),
                    "readout_error": _safe(lambda: props.readout_error(q)),
                    "prob_meas0_prep1": _named(props, "prob_meas0_prep1", q),
                    "prob_meas1_prep0": _named(props, "prob_meas1_prep0", q),
                })
            for g in getattr(props, "gates", []) or []:
                payload["gates"].append({
                    "gate": g.gate, "qubits": list(g.qubits),
                    "error": _gate_param(g, "gate_error"),
                    "length_ns": _gate_param(g, "gate_length", 1e9),
                })
        except Exception:
            payload["gaps"].append(gap("calibration", "sdk_version", "normalization error"))
        return payload


def _safe(fn):
    try:
        return fn()
    except Exception:
        return None


def _scaled(props, attr, q, scale):
    fn = getattr(props, attr, None)
    if fn is None:
        return None
    v = _safe(lambda: fn(q))
    return v * scale if v is not None else None


def _named(props, name, q):
    return _safe(lambda: props.qubit_property(q, name)[0])


def _gate_param(g, name, scale=1.0):
    for p in getattr(g, "parameters", []) or []:
        if p.name == name:
            return p.value * scale
    return None
