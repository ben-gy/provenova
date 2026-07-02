"""Connector abstraction. One vendor path each; community plugins subclass this."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class CaptureBundle:
    provider: str
    backend_name: str
    backend_kind: str = "simulator"
    n_qubits: int | None = None
    basis_gates: list[str] | None = None
    coupling_map: list | None = None
    circuit: object | None = None  # a qiskit QuantumCircuit (logical)
    calibration: dict | None = None  # qlprov/calibration/1.0 payload
    counts: dict[str, int] | None = None
    shots: int | None = None
    job_ref: str | None = None
    gaps: list[dict] = field(default_factory=list)


def gap(field_name: str, reason: str, detail: str | None = None) -> dict:
    return {"field": field_name, "reason": reason, "detail": detail}


def normalize_counts(raw: dict) -> dict[str, int]:
    """Normalize vendor count keys to our little-endian, compact convention."""
    out: dict[str, int] = {}
    for k, v in raw.items():
        bit = str(k).replace(" ", "")
        if bit.startswith("0x"):
            # hex key -> leave as-is bucket (rare); skip normalization
            out[bit] = out.get(bit, 0) + int(v)
            continue
        bit = bit[::-1]  # big-endian (qiskit) -> little-endian (ql)
        out[bit] = out.get(bit, 0) + int(v)
    return out


class Connector(ABC):
    name: str = "base"
    provider: str = "unknown"
    version: str = "0.1.0"

    @abstractmethod
    def claims(self, obj: object) -> bool:
        """Cheap, side-effect-free: does this object belong to this vendor?"""

    @abstractmethod
    def extract(self, *, circuit=None, backend=None, job=None, result=None) -> CaptureBundle:
        """Pull circuit/backend/calibration/result into a bundle.

        MUST NOT raise on missing data — append a gap flag instead (E1.6).
        """

    def fetch_calibration(self, backend) -> dict:  # pragma: no cover - overridden
        raise NotImplementedError
