"""AWS Braket connector (graceful degradation; live path needs braket SDK)."""

from __future__ import annotations

import datetime as _dt

from .base import CaptureBundle, Connector, gap, normalize_counts


class BraketConnector(Connector):
    name = "braket"
    provider = "aws_braket"

    def claims(self, obj) -> bool:
        return (type(obj).__module__ or "").startswith("braket")

    def extract(self, *, circuit=None, backend=None, job=None, result=None) -> CaptureBundle:
        bundle = CaptureBundle(
            provider="aws_braket",
            backend_name=getattr(backend, "name", "braket_device"),
            backend_kind="qpu",
            n_qubits=getattr(circuit, "num_qubits", None),
            circuit=circuit,
        )
        if result is not None:
            try:
                raw = getattr(result, "measurement_counts", None) or result.get_counts()
                bundle.counts = normalize_counts(dict(raw))
                bundle.shots = sum(bundle.counts.values())
            except Exception:
                bundle.gaps.append(gap("counts", "sdk_version"))
        bundle.calibration = self.fetch_calibration(backend) if backend is not None else None
        if bundle.calibration is None:
            bundle.gaps.append(gap("calibration", "auth_missing", "no live Braket device properties"))
        return bundle

    def fetch_calibration(self, backend) -> dict | None:
        props = getattr(backend, "properties", None)
        payload = {
            "schema": "qlprov/calibration/1.0",
            "backend": {"vendor": "aws", "name": getattr(backend, "name", "braket_device"), "kind": "qpu"},
            "captured_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "qubits": [], "gates": [], "provenance": {"source": "vendor_api"}, "gaps": [],
        }
        if props is None:
            return None
        return payload
