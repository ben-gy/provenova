"""Content-addressed get-or-create helpers (store once, reference many — E2.4)."""

from __future__ import annotations

import datetime as _dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import hashing
from .models import Backend, CalibrationSnapshot, Circuit, Compilation


def get_or_create_backend(
    session: Session,
    *,
    vendor: str,
    name: str,
    kind: str = "simulator",
    n_qubits: int | None = None,
    basis_gates: list[str] | None = None,
    coupling_map: list[list[int]] | None = None,
    meta: dict | None = None,
) -> Backend:
    existing = session.scalar(
        select(Backend).where(Backend.vendor == vendor, Backend.name == name)
    )
    if existing is not None:
        return existing
    backend = Backend(
        vendor=vendor,
        name=name,
        kind=kind,
        n_qubits=n_qubits,
        basis_gates=basis_gates,
        coupling_map=coupling_map,
        identity_hash=hashing.backend_identity_hash(
            vendor, name, n_qubits, basis_gates, coupling_map
        ),
        meta=meta,
    )
    session.add(backend)
    session.flush()
    return backend


def get_or_create_calibration(
    session: Session,
    *,
    backend: Backend,
    payload: dict,
    captured_at: _dt.datetime,
    source: str = "simulated",
) -> CalibrationSnapshot:
    content = hashing.calibration_hash(payload)
    existing = session.scalar(
        select(CalibrationSnapshot).where(
            CalibrationSnapshot.backend_id == backend.id,
            CalibrationSnapshot.content_sha256 == content,
        )
    )
    if existing is not None:
        return existing
    snap = CalibrationSnapshot(
        backend_id=backend.id,
        content_sha256=content,
        captured_at=captured_at,
        payload=payload,
        source=source,
    )
    session.add(snap)
    session.flush()
    return snap


def get_or_create_circuit(
    session: Session,
    *,
    fmt: str,
    source: str,
    n_qubits: int | None = None,
    n_clbits: int | None = None,
    metadata: dict | None = None,
) -> Circuit:
    content = hashing.circuit_hash(fmt, source)
    existing = session.scalar(select(Circuit).where(Circuit.content_sha256 == content))
    if existing is not None:
        return existing
    circ = Circuit(
        content_sha256=content,
        fmt=fmt,
        source=source,
        n_qubits=n_qubits,
        n_clbits=n_clbits,
        circ_metadata=metadata,
    )
    session.add(circ)
    session.flush()
    return circ


def get_or_create_compilation(
    session: Session,
    *,
    circuit: Circuit,
    backend: Backend,
    fmt: str,
    transpiled_source: str,
    optimization_level: int | None = None,
    transpiler_options: dict | None = None,
    metrics: dict | None = None,
    toolchain: dict | None = None,
) -> Compilation:
    content = hashing.compilation_hash(fmt, transpiled_source)
    existing = session.scalar(
        select(Compilation).where(
            Compilation.logical_circuit_id == circuit.id,
            Compilation.backend_id == backend.id,
            Compilation.transpiled_sha256 == content,
        )
    )
    if existing is not None:
        return existing
    comp = Compilation(
        logical_circuit_id=circuit.id,
        backend_id=backend.id,
        transpiled_sha256=content,
        transpiled_source=transpiled_source,
        fmt=fmt,
        optimization_level=optimization_level,
        transpiler_options=transpiler_options,
        metrics=metrics,
        toolchain=toolchain,
    )
    session.add(comp)
    session.flush()
    return comp
