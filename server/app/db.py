"""Database session management + startup bootstrap."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Iterator

from sqlalchemy import select
from sqlalchemy.orm import Session

import quantumledger_core as qc
from quantumledger_core.models import (
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
    from .services.attestation import jwks_from_keys, load_or_create_private_key

    settings = get_settings()
    priv, kid = load_or_create_private_key(settings.attestation_key_path)
    return priv, kid, jwks_from_keys([priv])


def default_workspace(session: Session) -> Workspace:
    ws = session.scalar(select(Workspace).where(Workspace.slug == "default"))
    return ws


def bootstrap(session: Session) -> None:
    """Idempotent startup seed: frameworks, keys, admin, default org/workspace."""
    from .services.compliance import load_all_frameworks

    settings = get_settings()

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
    org = session.scalar(select(Org).where(Org.slug == "quantumledger"))
    if org is None:
        org = Org(name="QuantumLedger", slug="quantumledger", plan=PLAN_ENTERPRISE)
        session.add(org)
        session.flush()
        session.add(OrgMembership(account_id=admin.id, org_id=org.id, role="owner"))
    ws = session.scalar(select(Workspace).where(Workspace.slug == "default"))
    if ws is None:
        ws = Workspace(org_id=org.id, name="Default", slug="default", store_mode="hosted")
        session.add(ws)
        session.flush()
    session.commit()
