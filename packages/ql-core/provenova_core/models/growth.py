"""Growth-engine artifacts: auto-published reports and paper attributions.

These tables back the autonomous content pipeline (the scheduled research
routine + the Growth API). They are additive-only — the schema is created via
``Base.metadata.create_all`` (no migrations), so NEVER alter existing tables;
each model carries an ``extra`` JSON column as the forward-compat escape hatch.
"""

from __future__ import annotations

import datetime as _dt

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, Timestamped, ULIDPk

# Fixed relation vocabulary for paper attributions. The server renders the
# honesty banner from this — the bot can never alter the framing.
REL_INSPIRED_BY = "inspired_by"
REL_REFERENCES = "references"
ATTRIBUTION_RELATIONS = {REL_INSPIRED_BY, REL_REFERENCES}

REPORT_KIND_WEEKLY = "weekly_fleet"


class Report(ULIDPk, Timestamped, Base):
    """A DB-backed public content page (e.g. the weekly State of Quantum report).

    DB-backed (not markdown-on-disk) because Fly machines have ephemeral disks
    and run >1 instance. ``body_md`` is the source of truth; ``body_html`` is a
    sanitised render cache written by the publish path (never by web routes).
    """

    __tablename__ = "reports"

    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(30), default=REPORT_KIND_WEEKLY)
    body_md: Mapped[str] = mapped_column(Text)
    body_html: Mapped[str | None] = mapped_column(Text, default=None)
    meta_description: Mapped[str] = mapped_column(String(200))
    published: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    published_at: Mapped[_dt.datetime | None] = mapped_column(DateTime(timezone=True))
    extra: Mapped[dict | None] = mapped_column(default=None)  # forward-compat


class CardAttribution(ULIDPk, Timestamped, Base):
    """Links an auto-published ResultCard to the paper that inspired it.

    Separate table (not columns on ``result_cards``) because create_all cannot
    ALTER the existing prod table, and attribution is growth-specific. The
    idempotency key for the pipeline is (arxiv_id|doi, circuit_sha256) —
    enforced select-first in the service (NULLs make a pure constraint
    unreliable across SQLite/Postgres).
    """

    __tablename__ = "card_attributions"
    __table_args__ = (
        UniqueConstraint("arxiv_id", "circuit_sha256", name="uq_attr_paper_circuit"),
    )

    card_id: Mapped[str] = mapped_column(ForeignKey("result_cards.id"), index=True)
    arxiv_id: Mapped[str | None] = mapped_column(String(40), index=True)
    doi: Mapped[str | None] = mapped_column(String(120), index=True)
    title: Mapped[str] = mapped_column(String(500))
    authors: Mapped[list] = mapped_column()  # ["A. Author", ...]
    year: Mapped[int | None] = mapped_column(Integer)
    url: Mapped[str] = mapped_column(String(500))  # arxiv.org / doi.org only (API-enforced)
    relation: Mapped[str] = mapped_column(String(30), default=REL_INSPIRED_BY)
    commentary_md: Mapped[str | None] = mapped_column(Text, default=None)  # sanitised at render
    circuit_sha256: Mapped[str] = mapped_column(String(64), index=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("accounts.id"))
    extra: Mapped[dict | None] = mapped_column(default=None)  # forward-compat
