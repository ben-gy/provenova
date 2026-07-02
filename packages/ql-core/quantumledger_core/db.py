"""Engine/session factory. One schema, two backends (SQLite + PostgreSQL)."""

from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .immutability import install_immutability
from .models import Base

# Fixed key for the startup advisory lock (serializes schema-init + seeding when
# several workers/containers boot at once). Arbitrary but stable.
_STARTUP_LOCK_KEY = 727274


@contextmanager
def advisory_lock(engine: Engine, key: int = _STARTUP_LOCK_KEY):
    """Serialize a critical section across processes on PostgreSQL.

    No-op on SQLite (single-writer already). On Postgres, holds a session-level
    advisory lock on a dedicated connection for the duration so concurrent
    workers/containers can't run ``create_all`` / trigger DDL / seeding at once.
    """
    if not engine.dialect.name.startswith("postgres"):
        yield
        return
    conn = engine.connect()
    try:
        conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": key})
        conn.commit()
        yield
    finally:
        conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
        conn.commit()
        conn.close()


def make_engine(url: str, *, echo: bool = False) -> Engine:
    connect_args = {}
    if url.startswith("sqlite"):
        connect_args = {"check_same_thread": False}
    engine = create_engine(url, echo=echo, future=True, connect_args=connect_args)

    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _rec):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

    return engine


def init_db(url: str, *, echo: bool = False, immutability: bool = True) -> Engine:
    """Create the schema and (optionally) install tamper-evidence triggers."""
    engine = make_engine(url, echo=echo)
    with advisory_lock(engine):
        Base.metadata.create_all(engine)
        if immutability:
            install_immutability(engine)
    return engine


def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, class_=Session, future=True)
