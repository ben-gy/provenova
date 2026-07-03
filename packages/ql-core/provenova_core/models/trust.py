"""Trust artifacts (derived from the core, references only).

A ResultCard is a publishable view of a Run; a Badge is issued against a Card; a
Benchmark references the runs it scores. These are what get shared publicly.
"""

from __future__ import annotations

import datetime as _dt

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, Timestamped, ULIDPk

VIS_PRIVATE = "private"
VIS_ORG = "org"
VIS_PUBLIC = "public"

# Badge ladder (ascending credibility)
BADGE_RECORDED = "recorded"
BADGE_REPRODUCED = "reproduced"
BADGE_BENCHMARKED = "benchmarked"
BADGE_COMPLIANT = "compliant"
BADGE_AUDIT_READY = "audit-ready"
BADGE_LADDER = [BADGE_RECORDED, BADGE_REPRODUCED, BADGE_BENCHMARKED, BADGE_COMPLIANT, BADGE_AUDIT_READY]


class ResultCard(ULIDPk, Timestamped, Base):
    __tablename__ = "result_cards"

    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(300))
    visibility: Mapped[str] = mapped_column(String(10), default=VIS_PRIVATE, index=True)
    summary: Mapped[dict | None] = mapped_column(default=None)
    card_sha256: Mapped[str | None] = mapped_column(String(64))
    doi: Mapped[str | None] = mapped_column(String(120))
    pid: Mapped[str | None] = mapped_column(String(120))
    license: Mapped[str | None] = mapped_column(String(80), default="CC-BY-4.0")
    published_at: Mapped[_dt.datetime | None] = mapped_column(DateTime(timezone=True))

    badges: Mapped[list["Badge"]] = relationship(back_populates="card")


class Badge(ULIDPk, Timestamped, Base):
    __tablename__ = "badges"

    result_card_id: Mapped[str] = mapped_column(ForeignKey("result_cards.id"), index=True)
    badge_type: Mapped[str] = mapped_column(String(20))
    criteria: Mapped[dict | None] = mapped_column(default=None)
    issued_by: Mapped[str | None] = mapped_column(String(120), default="quantumledger")
    evidence_run_ids: Mapped[list | None] = mapped_column(default=None)
    badge_sha256: Mapped[str | None] = mapped_column(String(64))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)

    card: Mapped[ResultCard] = relationship(back_populates="badges")


class Benchmark(ULIDPk, Timestamped, Base):
    __tablename__ = "benchmarks"

    workspace_id: Mapped[str | None] = mapped_column(ForeignKey("workspaces.id"))
    name: Mapped[str] = mapped_column(String(200))
    spec: Mapped[dict] = mapped_column()  # metric definition
    scope: Mapped[str] = mapped_column(String(20), default="private")  # private|public_fleet

    entries: Mapped[list["BenchmarkEntry"]] = relationship(back_populates="benchmark")


class BenchmarkEntry(ULIDPk, Timestamped, Base):
    __tablename__ = "benchmark_entries"

    benchmark_id: Mapped[str] = mapped_column(ForeignKey("benchmarks.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    backend_id: Mapped[str | None] = mapped_column(ForeignKey("backends.id"), index=True)
    score: Mapped[float] = mapped_column(Float)
    rank: Mapped[int | None] = mapped_column(Integer)

    benchmark: Mapped[Benchmark] = relationship(back_populates="entries")
