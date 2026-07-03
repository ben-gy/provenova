"""Aggregate public-QPU calibration corpus (proprietary data asset, E7.2).

Populated by the crawler. Content-hash dedup means the corpus only grows when a
device's calibration actually changes, giving a compact longitudinal time-series
that powers cross-fleet compare and the public "State of Quantum Hardware".
"""

from __future__ import annotations

import datetime as _dt

from sqlalchemy import Boolean, DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, Timestamped, ULIDPk


class CorpusSnapshot(ULIDPk, Timestamped, Base):
    __tablename__ = "corpus_snapshots"
    __table_args__ = (
        UniqueConstraint("provider", "backend_id", "content_hash", name="uq_corpus_content"),
    )

    provider: Mapped[str] = mapped_column(String(40), index=True)
    backend_id: Mapped[str] = mapped_column(String(120), index=True)
    captured_at: Mapped[_dt.datetime] = mapped_column(DateTime(timezone=True), index=True)
    vendor_updated_at: Mapped[_dt.datetime | None] = mapped_column(DateTime(timezone=True))
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    snapshot_json: Mapped[dict] = mapped_column()  # normalized CalibrationSnapshot
    derived_metrics: Mapped[dict | None] = mapped_column(default=None)
    raw_ref: Mapped[str | None] = mapped_column(String(255))
    license_ref: Mapped[str | None] = mapped_column(String(120))
    redistributable_raw: Mapped[bool] = mapped_column(Boolean, default=False)
