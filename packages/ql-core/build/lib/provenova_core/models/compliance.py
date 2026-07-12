"""Compliance entities (derived — evidence points back into the core).

A ComplianceFramework owns a set of Controls; each Control is satisfied by
EvidenceItems; an Attestation binds a framework's satisfied state to a workspace
at a point in time. ``EvidenceItem.source_ref_*`` points at a Run/Result/Snapshot
already in the record — compliance evidence is a byproduct of the core data.
"""

from __future__ import annotations

import datetime as _dt

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, Timestamped, ULIDPk


class ComplianceFramework(ULIDPk, Timestamped, Base):
    __tablename__ = "compliance_frameworks"
    __table_args__ = (UniqueConstraint("key", "version", name="uq_framework"),)

    key: Mapped[str] = mapped_column(String(80), index=True)
    name: Mapped[str] = mapped_column(String(200))
    version: Mapped[str] = mapped_column(String(40))
    jurisdiction: Mapped[str | None] = mapped_column(String(80))
    description: Mapped[str | None] = mapped_column(String(2000))
    spec: Mapped[dict | None] = mapped_column(default=None)

    controls: Mapped[list["Control"]] = relationship(back_populates="framework")


class Control(ULIDPk, Timestamped, Base):
    __tablename__ = "controls"

    framework_id: Mapped[str] = mapped_column(ForeignKey("compliance_frameworks.id"), index=True)
    key: Mapped[str] = mapped_column(String(80), index=True)
    title: Mapped[str] = mapped_column(String(300))
    requirement_text: Mapped[str | None] = mapped_column(String(2000))
    severity: Mapped[str] = mapped_column(String(20), default="medium")
    evidence_rules: Mapped[list | None] = mapped_column(default=None)
    remediation: Mapped[str | None] = mapped_column(String(2000))

    framework: Mapped[ComplianceFramework] = relationship(back_populates="controls")


class WorkspaceFramework(ULIDPk, Timestamped, Base):
    """A framework enabled on a workspace + last-computed status."""

    __tablename__ = "workspace_frameworks"
    __table_args__ = (
        UniqueConstraint("workspace_id", "framework_id", name="uq_ws_framework"),
    )

    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    framework_id: Mapped[str] = mapped_column(ForeignKey("compliance_frameworks.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="unknown")  # pass|gap|unknown
    last_evaluated_at: Mapped[_dt.datetime | None] = mapped_column(DateTime(timezone=True))
    status_detail: Mapped[dict | None] = mapped_column(default=None)


class EvidenceItem(ULIDPk, Timestamped, Base):
    """Auto-collected evidence: a pointer into the core + the target's hash."""

    __tablename__ = "evidence_items"
    __table_args__ = (
        UniqueConstraint("control_id", "source_ref_id", "rule_id", name="uq_evidence"),
    )

    control_id: Mapped[str] = mapped_column(ForeignKey("controls.id"), index=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    rule_id: Mapped[str] = mapped_column(String(80))
    source_ref_type: Mapped[str] = mapped_column(String(40))  # run|result|calibration_snapshot|...
    source_ref_id: Mapped[str] = mapped_column(String(26))
    source_content_hash: Mapped[str | None] = mapped_column(String(64))
    value: Mapped[dict | None] = mapped_column(default=None)


class Attestation(ULIDPk, Timestamped, Base):
    __tablename__ = "attestations"

    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    framework_id: Mapped[str | None] = mapped_column(ForeignKey("compliance_frameworks.id"))
    subject_type: Mapped[str] = mapped_column(String(40), default="workspace")  # workspace|card
    subject_id: Mapped[str | None] = mapped_column(String(26))
    satisfied_state: Mapped[dict | None] = mapped_column(default=None)
    evidence_root: Mapped[str] = mapped_column(String(64))
    statement: Mapped[dict] = mapped_column()
    signature: Mapped[str] = mapped_column(String(255))
    kid: Mapped[str] = mapped_column(String(80))
    point_in_time: Mapped[_dt.datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[_dt.datetime | None] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    attestation_sha256: Mapped[str | None] = mapped_column(String(64))


class ComplianceAlert(ULIDPk, Timestamped, Base):
    __tablename__ = "compliance_alerts"

    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    framework_id: Mapped[str | None] = mapped_column(ForeignKey("compliance_frameworks.id"))
    control_id: Mapped[str | None] = mapped_column(ForeignKey("controls.id"))
    kind: Mapped[str] = mapped_column(String(20))  # gap|drift
    message: Mapped[str] = mapped_column(String(1000))
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)


class AuditLog(ULIDPk, Timestamped, Base):
    """Append-only, hash-chained audit trail."""

    __tablename__ = "audit_logs"

    workspace_id: Mapped[str | None] = mapped_column(ForeignKey("workspaces.id"), index=True)
    account_id: Mapped[str | None] = mapped_column(ForeignKey("accounts.id"))
    action: Mapped[str] = mapped_column(String(80))
    resource_type: Mapped[str | None] = mapped_column(String(40))
    resource_id: Mapped[str | None] = mapped_column(String(26))
    detail: Mapped[dict | None] = mapped_column(default=None)
    prev_hash: Mapped[str | None] = mapped_column(String(64))
    entry_hash: Mapped[str] = mapped_column(String(64))
