"""Local simulator connector — the always-works path (qiskit-aer / Aer)."""

from __future__ import annotations

import datetime as _dt

from .base import CaptureBundle, Connector, gap, normalize_counts


class LocalSimulatorConnector(Connector):
    name = "simulator"
    provider = "local_sim"

    def claims(self, obj) -> bool:
        mod = type(obj).__module__ or ""
        tn = type(obj).__name__
        return mod.startswith("qiskit_aer") or "Aer" in tn

    def extract(self, *, circuit=None, backend=None, job=None, result=None) -> CaptureBundle:
        name = "aer_simulator"
        if backend is not None:
            try:
                name = backend.name if isinstance(backend.name, str) else backend.name()
            except Exception:
                name = getattr(backend, "name", "aer_simulator")
        n_qubits = getattr(circuit, "num_qubits", None) if circuit is not None else None
        bundle = CaptureBundle(
            provider="local_sim",
            backend_name=name,
            backend_kind="simulator",
            n_qubits=n_qubits,
            basis_gates=["rz", "sx", "x", "cx", "id"],
            circuit=circuit,
        )
        # result / counts
        res = result
        if res is None and job is not None:
            try:
                res = job.result()
            except Exception:
                bundle.gaps.append(gap("result", "sdk_version", "job.result() failed"))
        if res is not None:
            try:
                raw = res.get_counts()
                if isinstance(raw, list):
                    raw = raw[0]
                bundle.counts = normalize_counts(raw)
                bundle.shots = sum(bundle.counts.values())
            except Exception:
                bundle.gaps.append(gap("counts", "sdk_version", "could not read counts"))
        bundle.calibration = self.fetch_calibration(backend) if backend is not None else None
        if bundle.calibration is None and n_qubits:
            from quantumledger_core.simulate.engine import default_simulator_calibration

            bundle.calibration = default_simulator_calibration(n_qubits, name)
        return bundle

    def fetch_calibration(self, backend) -> dict:
        from quantumledger_core.simulate.engine import default_simulator_calibration

        n = None
        try:
            cfg = backend.configuration()
            n = getattr(cfg, "n_qubits", None)
        except Exception:
            n = getattr(getattr(backend, "options", None), "n_qubits", None)
        nm = getattr(getattr(backend, "options", None), "noise_model", None)
        payload = default_simulator_calibration(n or 2, getattr(backend, "name", "aer_simulator")
                                                if isinstance(getattr(backend, "name", None), str) else "aer_simulator")
        if nm is None:
            payload.setdefault("gaps", []).append(
                gap("calibration", "vendor_not_exposed", "ideal simulator: no noise model")
            )
        else:
            payload["provenance"]["source"] = "aer_noise_model"
        payload["captured_at"] = _dt.datetime.now(_dt.timezone.utc).isoformat()
        return payload
