"""Aggregate public-QPU calibration corpus (proprietary data asset, E7.2).

Populated by the crawler. Content-hash dedup means the corpus only grows when a
device's calibration actually changes, giving a compact longitudinal time-series
that powers cross-fleet compare and the public "State of Quantum Hardware".
"""

from __future__ import annotations

import datetime as _dt

from sqlalchemy import Boolean, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, Timestamped, ULIDPk, UTCDateTime


class CorpusSnapshot(ULIDPk, Timestamped, Base):
    __tablename__ = "corpus_snapshots"
    __table_args__ = (
        UniqueConstraint("provider", "backend_id", "content_hash", name="uq_corpus_content"),
    )

    provider: Mapped[str] = mapped_column(String(40), index=True)
    backend_id: Mapped[str] = mapped_column(String(120), index=True)
    captured_at: Mapped[_dt.datetime] = mapped_column(UTCDateTime(), index=True)
    vendor_updated_at: Mapped[_dt.datetime | None] = mapped_column(UTCDateTime())
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    snapshot_json: Mapped[dict] = mapped_column()  # normalized CalibrationSnapshot
    derived_metrics: Mapped[dict | None] = mapped_column(default=None)
    raw_ref: Mapped[str | None] = mapped_column(String(255))
    license_ref: Mapped[str | None] = mapped_column(String(120))
    redistributable_raw: Mapped[bool] = mapped_column(Boolean, default=False)


class CollectorState(Base):
    """One row per scheduled data collector: when it last ran, how it went,
    and whatever it needs to remember between runs (e.g. upstream versions)."""

    __tablename__ = "collector_state"

    name: Mapped[str] = mapped_column(String(60), primary_key=True)
    last_run_at: Mapped[_dt.datetime | None] = mapped_column(UTCDateTime())
    last_ok_at: Mapped[_dt.datetime | None] = mapped_column(UTCDateTime())
    last_status: Mapped[str | None] = mapped_column(String(20))  # ok | error
    last_detail: Mapped[dict | None] = mapped_column(default=None)
    state: Mapped[dict | None] = mapped_column(default=None)


class PublicDataset(ULIDPk, Timestamped, Base):
    """A fetched version of an openly-licensed upstream dataset (files as text).

    Lets a collector publish a newer upstream version without a redeploy; the
    copy committed under ``datasets/`` remains the fallback.
    """

    __tablename__ = "public_datasets"
    __table_args__ = (UniqueConstraint("name", "version", name="uq_public_dataset_version"),)

    name: Mapped[str] = mapped_column(String(60), index=True)
    version: Mapped[str] = mapped_column(String(80))
    version_date: Mapped[_dt.datetime | None] = mapped_column(UTCDateTime())
    meta: Mapped[dict] = mapped_column()
    files: Mapped[dict] = mapped_column()
