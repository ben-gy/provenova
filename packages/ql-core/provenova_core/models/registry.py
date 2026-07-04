"""Schema-version registry, retention policy, and chain checkpoints."""

from __future__ import annotations

import datetime as _dt

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, Timestamped, ULIDPk


class SchemaVersion(ULIDPk, Timestamped, Base):
    """The provenance JSON Schemas stored as data, versioned."""

    __tablename__ = "schema_versions"
    __table_args__ = (UniqueConstraint("component", "version", name="uq_schema_version"),)

    component: Mapped[str] = mapped_column(String(40))  # run_provenance|calibration_snapshot
    version: Mapped[str] = mapped_column(String(40))
    json_schema: Mapped[dict] = mapped_column()


class CheckpointAnchor(ULIDPk, Timestamped, Base):
    """A notarized snapshot of a workspace's hash-chain head."""

    __tablename__ = "checkpoint_anchors"

    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    head_run_hash: Mapped[str] = mapped_column(String(64))
    run_count: Mapped[int] = mapped_column(Integer)
    anchored_at: Mapped[_dt.datetime] = mapped_column(DateTime(timezone=True))
