"""Account lifecycle: register, login, academic verification, admin upgrade, audit."""

from __future__ import annotations

import datetime as _dt

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from quantumledger_core import hashing
from quantumledger_core.models import (
    PLAN_ACADEMIC,
    PLAN_FREE,
    Account,
    AuditLog,
    Grant,
    Org,
    OrgMembership,
    Workspace,
    new_ulid,
)

from ..security import hash_password, verify_password

# Academic email-domain heuristics (allowlist patterns).
_ACADEMIC_SUFFIXES = (".edu", ".ac.uk", ".edu.au", ".ac.jp", ".edu.cn")
_ACADEMIC_TOKENS = (".ac.", ".edu.", "uni-", "university")
_ACADEMIC_ALLOWLIST = {"cern.ch", "mit.edu", "ethz.ch", "riken.jp", "ornl.gov", "lanl.gov"}


def is_academic_domain(email: str) -> bool:
    domain = email.split("@")[-1].lower()
    if domain in _ACADEMIC_ALLOWLIST:
        return True
    if any(domain.endswith(s) for s in _ACADEMIC_SUFFIXES):
        return True
    return any(tok in domain for tok in _ACADEMIC_TOKENS)


def audit(session: Session, *, workspace_id, account_id, action, resource_type=None,
          resource_id=None, detail=None) -> AuditLog:
    prev = session.scalar(select(AuditLog).order_by(AuditLog.created_at.desc()))
    prev_hash = prev.entry_hash if prev else None
    payload = {"action": action, "account_id": account_id, "resource_type": resource_type,
               "resource_id": resource_id, "detail": detail}
    entry_hash = hashing.sha256_hex({"prev": prev_hash, "payload": payload})
    log = AuditLog(
        workspace_id=workspace_id, account_id=account_id, action=action,
        resource_type=resource_type, resource_id=resource_id, detail=detail,
        prev_hash=prev_hash, entry_hash=entry_hash,
    )
    session.add(log)
    session.flush()
    return log


def register(session: Session, *, email: str, password: str, display_name: str | None = None) -> Account:
    existing = session.scalar(select(Account).where(Account.email == email))
    if existing is not None:
        raise ValueError("email already registered")
    acc = Account(
        email=email,
        display_name=display_name or email.split("@")[0],
        password_hash=hash_password(password),
        email_verified=False,
    )
    session.add(acc)
    session.flush()
    # personal org + workspace on the free tier
    slug = f"{email.split('@')[0]}-{new_ulid()[-6:].lower()}"
    org = Org(name=display_name or email.split("@")[0], slug=slug, plan=PLAN_FREE)
    session.add(org)
    session.flush()
    session.add(OrgMembership(account_id=acc.id, org_id=org.id, role="owner"))
    ws = Workspace(org_id=org.id, name="Default", slug=f"ws-{new_ulid()[-6:].lower()}", store_mode="hosted")
    session.add(ws)
    session.flush()
    audit(session, workspace_id=ws.id, account_id=acc.id, action="account.register")
    return acc


def authenticate(session: Session, *, email: str, password: str) -> Account | None:
    acc = session.scalar(select(Account).where(Account.email == email))
    if acc and verify_password(password, acc.password_hash):
        return acc
    return None


def verify_email(session: Session, account: Account) -> Account:
    """Mark email verified; auto-grant Academic (=Pro-free) for academic domains."""
    account.email_verified = True
    if is_academic_domain(account.email):
        account.academic_verified = True
        org = _primary_org(session, account)
        if org:
            grant_plan(session, org, PLAN_ACADEMIC, source="academic", granted_by=account.id,
                       expires_at=_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(days=365))
    session.flush()
    return account


def grant_plan(session: Session, org: Org, plan: str, *, source: str = "admin_override",
               granted_by: str | None = None, expires_at=None) -> Grant:
    # deactivate prior grants of the same source
    for g in session.scalars(select(Grant).where(Grant.org_id == org.id, Grant.source == source)):
        g.active = False
    grant = Grant(org_id=org.id, plan=plan, source=source, granted_by=granted_by,
                  expires_at=expires_at, active=True)
    session.add(grant)
    org.plan = _highest(session, org, plan)
    session.flush()
    audit(session, workspace_id=None, account_id=granted_by, action="org.grant",
          resource_type="org", resource_id=org.id, detail={"plan": plan, "source": source})
    return grant


def _highest(session: Session, org: Org, new_plan: str) -> str:
    from quantumledger_core.models import PLAN_ORDER

    plans = [new_plan, org.plan or PLAN_FREE]
    for g in session.scalars(select(Grant).where(Grant.org_id == org.id, Grant.active.is_(True))):
        plans.append(g.plan)
    return max(plans, key=lambda p: PLAN_ORDER.index(p) if p in PLAN_ORDER else -1)


def _primary_org(session: Session, account: Account) -> Org | None:
    m = session.scalar(select(OrgMembership).where(OrgMembership.account_id == account.id))
    return session.get(Org, m.org_id) if m else None
