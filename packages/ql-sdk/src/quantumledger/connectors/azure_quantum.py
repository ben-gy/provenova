"""Azure Quantum connector (graceful degradation; live path needs azure-quantum)."""

from __future__ import annotations

import datetime as _dt

from .base import CaptureBundle, Connector, gap, normalize_counts


class AzureQuantumConnector(Connector):
    name = "azure_quantum"
    provider = "azure_quantum"

    def claims(self, obj) -> bool:
        return (type(obj).__module__ or "").startswith("azure.quantum")

    def extract(self, *, circuit=None, backend=None, job=None, result=None) -> CaptureBundle:
        bundle = CaptureBundle(
            provider="azure_quantum",
            backend_name=getattr(backend, "name", "azure_target"),
            backend_kind="qpu",
            n_qubits=getattr(circuit, "num_qubits", None),
            circuit=circuit,
        )
        if result is not None:
            try:
                raw = result.get_counts() if hasattr(result, "get_counts") else dict(result)
                bundle.counts = normalize_counts(raw)
                bundle.shots = sum(bundle.counts.values())
            except Exception:
                bundle.gaps.append(gap("counts", "sdk_version"))
        bundle.gaps.append(gap("calibration", "vendor_not_exposed",
                               "Azure targets rarely expose per-qubit calibration"))
        bundle.calibration = {
            "schema": "qlprov/calibration/1.0",
            "backend": {"vendor": "azure", "name": bundle.backend_name, "kind": "qpu"},
            "captured_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "qubits": [], "gates": [], "provenance": {"source": "vendor_api"},
            "gaps": [gap("calibration", "vendor_not_exposed")],
        }
        return bundle
