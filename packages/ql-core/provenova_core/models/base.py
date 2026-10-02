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
from sqlalchemy.types import TypeDecorator
from ulid import ULID


def new_ulid() -> str:
    return str(ULID())


def utcnow() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


class UTCDateTime(TypeDecorator):
    """``DateTime(timezone=True)`` that round-trips as aware UTC on every backend.

    PostgreSQL returns aware datetimes, but SQLite has no timezone storage and
    hands back naive values — so ``isoformat()`` in exported provenance docs and
    API payloads would lose its ``+00:00`` after a Postgres -> SQLite move. Values
    are normalised to UTC on write and re-tagged as UTC on read; the DDL is
    unchanged (same underlying column type).
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and value.tzinfo is not None:
            value = value.astimezone(_dt.timezone.utc)
            if dialect.name == "sqlite":
                value = value.replace(tzinfo=None)
        return value

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=_dt.timezone.utc)
        return value


class Base(DeclarativeBase):
    """Shared declarative base for every Provenova table.

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
        UTCDateTime(), default=utcnow, nullable=False
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
