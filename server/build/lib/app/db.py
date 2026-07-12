"""Database session management + startup bootstrap."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Iterator

from sqlalchemy import inspect as sa_inspect, select, text
from sqlalchemy.orm import Session

import provenova_core as qc
from provenova_core.models import (
    PLAN_ENTERPRISE,
    Account,
    Org,
    OrgMembership,
    Workspace,
)

from .config import get_settings

REPO_ROOT = Path(__file__).resolve().parents[2]
FRAMEWORKS_DIR = REPO_ROOT / "frameworks"

_engine = None
_SessionLocal = None


def engine():
    global _engine, _SessionLocal
    if _engine is None:
        settings = get_settings()
        _engine = qc.init_db(settings.database_url)
        _SessionLocal = qc.session_factory(_engine)
    return _engine


def SessionLocal() -> Session:
    engine()
    return _SessionLocal()


def get_db() -> Iterator[Session]:
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


@lru_cache
def attestation_key():
    from .services.attestation import (
        jwks_from_keys,
        load_or_create_private_key,
        private_key_from_b64,
    )

    settings = get_settings()
    if settings.attestation_key_b64:
        # Stable signing key supplied via env (QL_ATTESTATION_KEY_B64): keeps the
        # attestation trust root constant across redeploys on ephemeral disks.
        priv, kid = private_key_from_b64(settings.attestation_key_b64)
    else:
        priv, kid = load_or_create_private_key(settings.attestation_key_path)
    return priv, kid, jwks_from_keys([priv])


def default_workspace(session: Session) -> Workspace:
    ws = session.scalar(select(Workspace).where(Workspace.slug == "default"))
    return ws


# Additive column migrations for deployments whose tables predate a column.
# ``Base.metadata.create_all`` only creates missing *tables*, never missing
# *columns*, so a new mapped column would be absent on an existing DB and every
# ORM query touching it would error. Each entry is an idempotent ADD COLUMN with
# a constant default (required by SQLite for a NOT NULL add). Applied before any
# ORM read so the models and the physical schema agree.
_COLUMN_MIGRATIONS: dict[str, dict[str, str]] = {
    "accounts": {"token_version": "INTEGER NOT NULL DEFAULT 0"},
    "mfa_credentials": {"last_used_counter": "BIGINT"},
}


def _apply_column_migrations(session: Session) -> None:
    bind = session.get_bind()
    insp = sa_inspect(bind)
    tables = set(insp.get_table_names())
    for table, cols in _COLUMN_MIGRATIONS.items():
        if table not in tables:
            continue  # create_all already made it with the column present
        have = {c["name"] for c in insp.get_columns(table)}
        for col, ddl in cols.items():
            if col not in have:
                session.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))
    session.commit()


def bootstrap(session: Session) -> None:
    """Idempotent startup seed: frameworks, keys, admin, default org/workspace.

    Serialized with a Postgres advisory lock so concurrent workers/containers
    don't race on the framework/account unique constraints (no-op on SQLite).
    """
    from provenova_core.db import advisory_lock

    from .services.compliance import load_all_frameworks

    settings = get_settings()

    with advisory_lock(session.get_bind()):
        # Bring an existing DB's columns up to date before any ORM read below.
        _apply_column_migrations(session)

        # frameworks-as-data
        if FRAMEWORKS_DIR.exists():
            load_all_frameworks(session, directory=FRAMEWORKS_DIR)

        # attestation signing key
        attestation_key()

        # admin account + default org/workspace
        admin = session.scalar(select(Account).where(Account.email == settings.admin_email))
        if admin is None:
            admin = Account(
                email=settings.admin_email,
                display_name="Administrator",
                email_verified=True,
                is_superadmin=True,
            )
            session.add(admin)
            session.flush()
        org = session.scalar(select(Org).where(Org.slug == "provenova"))
        if org is None:
            # migrate the pre-rename slug in place so existing deployments keep their org
            org = session.scalar(select(Org).where(Org.slug == "quantumledger"))
            if org is not None:
                org.slug = "provenova"
        if org is None:
            org = Org(name="Provenova", slug="provenova", plan=PLAN_ENTERPRISE)
            session.add(org)
            session.flush()
            session.add(OrgMembership(account_id=admin.id, org_id=org.id, role="owner"))
        ws = session.scalar(select(Workspace).where(Workspace.slug == "default"))
        if ws is None:
            ws = Workspace(org_id=org.id, name="Default", slug="default", store_mode="hosted")
            session.add(ws)
            session.flush()
        session.commit()
