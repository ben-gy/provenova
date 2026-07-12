"""Database-level tamper-evidence.

Immutable tables reject UPDATE and DELETE outright. ``runs`` allow mutation only
while ``pending``/``running`` (the seal transition ``pending -> completed`` fills
the run hash); once ``completed`` or ``failed`` a run is frozen and cannot be
deleted. Enforced by DB triggers so even raw SQL cannot silently rewrite history.

Both dialects are supported; the app layer also guards via SQLAlchemy events, but
the DB trigger is the real enforcement.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine

from .models import IMMUTABLE_TABLES

_SEALED_STATES = ("completed", "failed")


def _sqlite_ddl() -> list[str]:
    stmts: list[str] = []
    for tbl in IMMUTABLE_TABLES:
        for op in ("UPDATE", "DELETE"):
            name = f"trg_{tbl}_no_{op.lower()}"
            stmts.append(f"DROP TRIGGER IF EXISTS {name}")
            stmts.append(
                f"CREATE TRIGGER {name} BEFORE {op} ON {tbl} "
                f"BEGIN SELECT RAISE(ABORT, '{tbl} rows are immutable'); END"
            )
    # runs: block delete always; block update once sealed
    stmts.append("DROP TRIGGER IF EXISTS trg_runs_no_delete")
    stmts.append(
        "CREATE TRIGGER trg_runs_no_delete BEFORE DELETE ON runs "
        "BEGIN SELECT RAISE(ABORT, 'runs cannot be deleted'); END"
    )
    stmts.append("DROP TRIGGER IF EXISTS trg_runs_seal")
    stmts.append(
        "CREATE TRIGGER trg_runs_seal BEFORE UPDATE ON runs "
        "WHEN OLD.status IN ('completed','failed') "
        "BEGIN SELECT RAISE(ABORT, 'run is sealed and immutable'); END"
    )
    return stmts


def _postgres_ddl() -> list[str]:
    stmts: list[str] = []
    stmts.append(
        """
        CREATE OR REPLACE FUNCTION ql_reject_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION '% rows are immutable', TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    stmts.append(
        """
        CREATE OR REPLACE FUNCTION ql_runs_guard() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'runs cannot be deleted';
            END IF;
            IF OLD.status IN ('completed','failed') THEN
                RAISE EXCEPTION 'run is sealed and immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    for tbl in IMMUTABLE_TABLES:
        stmts.append(f"DROP TRIGGER IF EXISTS trg_{tbl}_immutable ON {tbl}")
        stmts.append(
            f"CREATE TRIGGER trg_{tbl}_immutable BEFORE UPDATE OR DELETE ON {tbl} "
            f"FOR EACH ROW EXECUTE FUNCTION ql_reject_mutation()"
        )
    stmts.append("DROP TRIGGER IF EXISTS trg_runs_guard ON runs")
    stmts.append(
        "CREATE TRIGGER trg_runs_guard BEFORE UPDATE OR DELETE ON runs "
        "FOR EACH ROW EXECUTE FUNCTION ql_runs_guard()"
    )
    return stmts


def install_immutability(engine: Engine) -> None:
    dialect = engine.dialect.name
    if dialect == "sqlite":
        stmts = _sqlite_ddl()
    elif dialect in ("postgresql", "postgres"):
        stmts = _postgres_ddl()
    else:  # pragma: no cover - other dialects not targeted
        return
    with engine.begin() as conn:
        for stmt in stmts:
            conn.execute(text(stmt))


def drop_immutability(engine: Engine) -> None:
    """Drop the tamper-evidence triggers (controlled maintenance/reset only).

    Re-install with :func:`install_immutability` after the maintenance operation.
    """
    dialect = engine.dialect.name
    stmts: list[str] = []
    if dialect == "sqlite":
        for tbl in IMMUTABLE_TABLES:
            stmts.append(f"DROP TRIGGER IF EXISTS trg_{tbl}_no_update")
            stmts.append(f"DROP TRIGGER IF EXISTS trg_{tbl}_no_delete")
        stmts.append("DROP TRIGGER IF EXISTS trg_runs_no_delete")
        stmts.append("DROP TRIGGER IF EXISTS trg_runs_seal")
    elif dialect in ("postgresql", "postgres"):
        for tbl in IMMUTABLE_TABLES:
            stmts.append(f"DROP TRIGGER IF EXISTS trg_{tbl}_immutable ON {tbl}")
        stmts.append("DROP TRIGGER IF EXISTS trg_runs_guard ON runs")
    else:  # pragma: no cover
        return
    with engine.begin() as conn:
        for stmt in stmts:
            conn.execute(text(stmt))
