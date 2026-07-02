"""Declarative base, mixins and portable column types.

The same models run on SQLite (offline SDK / self-host-small) and PostgreSQL
(hosted). We therefore avoid dialect-specific column types in the model layer:

* JSON uses SQLAlchemy's generic :class:`~sqlalchemy.JSON`, which maps to
  ``JSONB`` on Postgres and JSON-in-TEXT on SQLite.
* Primary keys are ULID strings (26 chars) — time-sortable and collision-free
  across independently-running offline stores that later sync to the hosted DB.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

from sqlalchemy import JSON, DateTime, String
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column
from ulid import ULID


def new_ulid() -> str:
    return str(ULID())


def utcnow() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


class Base(DeclarativeBase):
    """Shared declarative base for every QuantumLedger table.

    ``list``/``dict`` annotations map to the generic :class:`~sqlalchemy.JSON`
    type (JSONB on Postgres, JSON-in-TEXT on SQLite) so one model set runs on
    both backends.
    """

    type_annotation_map = {
        dict: JSON,
        list: JSON,
        dict[str, Any]: JSON,
        list[Any]: JSON,
    }


class ULIDPk:
    """Mixin: a ULID string primary key."""

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_ulid)


class Timestamped:
    """Mixin: a UTC ``created_at`` stamped on insert."""

    created_at: Mapped[_dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class Named:
    """Convenience mixin producing a lower-case table name from the class."""

    @declared_attr.directive
    def __tablename__(cls) -> str:  # noqa: N805
        name = cls.__name__
        out = [name[0].lower()]
        for ch in name[1:]:
            if ch.isupper():
                out.append("_")
                out.append(ch.lower())
            else:
                out.append(ch)
        return "".join(out) + "s"
