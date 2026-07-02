"""IonQ / Quantinuum connector (REST + characterization JSON; graceful)."""

from __future__ import annotations

import datetime as _dt

from .base import CaptureBundle, Connector, gap, normalize_counts


class IonQConnector(Connector):
    name = "ionq"
    provider = "ionq"

    def claims(self, obj) -> bool:
        mod = (type(obj).__module__ or "").lower()
        return "ionq" in mod or "quantinuum" in mod

    def extract(self, *, circuit=None, backend=None, job=None, result=None) -> CaptureBundle:
        bundle = CaptureBundle(
            provider="ionq",
            backend_name=getattr(backend, "name", "ionq_backend"),
            backend_kind="qpu",
            n_qubits=getattr(circuit, "num_qubits", None),
            basis_gates=["gpi", "gpi2", "ms"],
            coupling_map=None,  # all-to-all
            circuit=circuit,
        )
        bundle.gaps.append(gap("coupling_map", "vendor_not_exposed", "trapped-ion all-to-all"))
        if result is not None:
            try:
                raw = result.get_counts() if hasattr(result, "get_counts") else dict(result)
                bundle.counts = normalize_counts(raw)
                bundle.shots = sum(bundle.counts.values())
            except Exception:
                bundle.gaps.append(gap("counts", "sdk_version"))
        bundle.calibration = self._characterization(backend)
        return bundle

    def _characterization(self, backend) -> dict:
        payload = {
            "schema": "qlprov/calibration/1.0",
            "backend": {"vendor": "ionq", "name": getattr(backend, "name", "ionq_backend"), "kind": "qpu"},
            "captured_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "qubits": [], "gates": [], "provenance": {"source": "vendor_api"},
            "gaps": [gap("calibration", "auth_missing", "no live IonQ characterization")],
        }
        return payload
