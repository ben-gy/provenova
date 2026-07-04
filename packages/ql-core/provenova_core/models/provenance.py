"""The immutable core record.

``Backend`` owns a time-series of ``CalibrationSnapshot``s. A ``Run`` binds
exactly one ``CalibrationSnapshot`` + one ``Circuit`` + one ``Compilation`` and
produces one or more ``Result``s. ``ReproductionEvent`` links an original run to
a later re-run with a computed diff, score and verdict.

Rows marked immutable are protected by DB triggers (see ``immutability.py``);
the only permitted mutation is the single ``pending -> completed`` seal of a Run.
"""

from __future__ import annotations

import datetime as _dt

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, Timestamped, ULIDPk

# Run lifecycle
STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"

# Reproduction verdicts (from the reproducibility score, scoring.py)
VERDICT_REPRODUCIBLE = "reproducible"
VERDICT_DRIFTED = "drifted"
VERDICT_DIVERGENT = "divergent"
VERDICT_IRREPRODUCIBLE = "irreproducible"


class Backend(ULIDPk, Timestamped, Base):
    __tablename__ = "backends"
    __table_args__ = (UniqueConstraint("vendor", "name", name="uq_backend"),)

    vendor: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="simulator")  # qpu|simulator
    n_qubits: Mapped[int | None] = mapped_column(Integer)
    basis_gates: Mapped[list | None] = mapped_column(default=None)
    coupling_map: Mapped[list | None] = mapped_column(default=None)
    identity_hash: Mapped[str] = mapped_column(String(64), index=True)
    meta: Mapped[dict | None] = mapped_column(default=None)

    snapshots: Mapped[list["CalibrationSnapshot"]] = relationship(back_populates="backend")


class CalibrationSnapshot(ULIDPk, Timestamped, Base):
    """Content-addressed, time-indexed device state. The dedup target (E2.4)."""

    __tablename__ = "calibration_snapshots"
    __table_args__ = (
        UniqueConstraint("backend_id", "content_sha256", name="uq_calib_content"),
    )

    backend_id: Mapped[str] = mapped_column(ForeignKey("backends.id"), index=True)
    content_sha256: Mapped[str] = mapped_column(String(64), index=True)
    captured_at: Mapped[_dt.datetime] = mapped_column(DateTime(timezone=True), index=True)
    payload: Mapped[dict] = mapped_column()
    source: Mapped[str] = mapped_column(String(20), default="simulated")

    backend: Mapped[Backend] = relationship(back_populates="snapshots")


class Circuit(ULIDPk, Timestamped, Base):
    __tablename__ = "circuits"
    __table_args__ = (UniqueConstraint("content_sha256", name="uq_circuit_content"),)

    content_sha256: Mapped[str] = mapped_column(String(64), index=True)
    fmt: Mapped[str] = mapped_column(String(20), default="qasm3")
    source: Mapped[str] = mapped_column()  # program text (or blob ref for large)
    n_qubits: Mapped[int | None] = mapped_column(Integer)
    n_clbits: Mapped[int | None] = mapped_column(Integer)
    circ_metadata: Mapped[dict | None] = mapped_column(default=None)


class Compilation(ULIDPk, Timestamped, Base):
    __tablename__ = "compilations"
    __table_args__ = (
        UniqueConstraint(
            "logical_circuit_id", "backend_id", "transpiled_sha256", name="uq_compilation"
        ),
    )

    logical_circuit_id: Mapped[str] = mapped_column(ForeignKey("circuits.id"), index=True)
    backend_id: Mapped[str] = mapped_column(ForeignKey("backends.id"), index=True)
    transpiled_sha256: Mapped[str] = mapped_column(String(64), index=True)
    transpiled_source: Mapped[str] = mapped_column()
    fmt: Mapped[str] = mapped_column(String(20), default="qasm3")
    optimization_level: Mapped[int | None] = mapped_column(Integer)
    transpiler_options: Mapped[dict | None] = mapped_column(default=None)
    metrics: Mapped[dict | None] = mapped_column(default=None)  # depth, gate_counts, ...
    toolchain: Mapped[dict | None] = mapped_column(default=None)


class Run(ULIDPk, Timestamped, Base):
    """The core immutable record. Sealed once (pending -> completed)."""

    __tablename__ = "runs"

    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    account_id: Mapped[str | None] = mapped_column(ForeignKey("accounts.id"))
    circuit_id: Mapped[str] = mapped_column(ForeignKey("circuits.id"), index=True)
    compilation_id: Mapped[str | None] = mapped_column(ForeignKey("compilations.id"))
    backend_id: Mapped[str] = mapped_column(ForeignKey("backends.id"), index=True)
    calibration_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("calibration_snapshots.id"), index=True
    )

    shots: Mapped[int] = mapped_column(Integer, default=1024)
    seed_simulator: Mapped[int | None] = mapped_column(Integer)
    seed_transpiler: Mapped[int | None] = mapped_column(Integer)
    execution_params: Mapped[dict | None] = mapped_column(default=None)

    status: Mapped[str] = mapped_column(String(20), default=STATUS_PENDING, index=True)
    capture_status: Mapped[str] = mapped_column(String(20), default="complete")
    run_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    inputs_root: Mapped[str | None] = mapped_column(String(64))
    chain_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    prev_chain_hash: Mapped[str | None] = mapped_column(String(64))
    merkle_leaves: Mapped[dict | None] = mapped_column(default=None)
    provenance_schema_version: Mapped[str] = mapped_column(String(40), default="qlprov/run/1.0")

    project: Mapped[str | None] = mapped_column(String(120), index=True)
    gaps: Mapped[list | None] = mapped_column(default=None)
    started_at: Mapped[_dt.datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[_dt.datetime | None] = mapped_column(DateTime(timezone=True))

    results: Mapped[list["Result"]] = relationship(
        back_populates="run", order_by="Result.result_index"
    )
    circuit: Mapped[Circuit] = relationship()
    compilation: Mapped[Compilation | None] = relationship()
    backend: Mapped[Backend] = relationship()
    calibration: Mapped[CalibrationSnapshot] = relationship()


class Result(ULIDPk, Timestamped, Base):
    __tablename__ = "results"
    __table_args__ = (UniqueConstraint("run_id", "result_index", name="uq_result_index"),)

    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    result_index: Mapped[int] = mapped_column(Integer, default=0)
    counts: Mapped[dict | None] = mapped_column(default=None)
    counts_sha256: Mapped[str] = mapped_column(String(64))
    distribution: Mapped[dict] = mapped_column()
    shots: Mapped[int] = mapped_column(Integer)
    raw_memory_blob_ref: Mapped[str | None] = mapped_column(String(255))
    retained: Mapped[str] = mapped_column(String(20), default="counts")
    result_metadata: Mapped[dict | None] = mapped_column(default=None)

    run: Mapped[Run] = relationship(back_populates="results")


class ReproductionEvent(ULIDPk, Timestamped, Base):
    __tablename__ = "reproduction_events"

    original_run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    reproduced_run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    kind: Mapped[str] = mapped_column(String(20), default="reproduce")  # reproduce|cross_vendor|replay
    submitted_by: Mapped[str | None] = mapped_column(ForeignKey("accounts.id"))
    status: Mapped[str] = mapped_column(String(20), default="verified")  # pending|verified|rejected
    diff: Mapped[dict | None] = mapped_column(default=None)
    reproducibility_score: Mapped[float | None] = mapped_column(Float)
    verdict: Mapped[str | None] = mapped_column(String(20))
    calibration_drift: Mapped[dict | None] = mapped_column(default=None)
    transpilation_delta: Mapped[dict | None] = mapped_column(default=None)


# Tables whose rows are immutable after insert (protected by DB triggers). Runs
# are handled specially — they allow exactly one seal transition.
IMMUTABLE_TABLES = [
    "calibration_snapshots",
    "circuits",
    "compilations",
    "results",
    "reproduction_events",
]
SEALABLE_TABLE = "runs"
