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

# Academic email-domain verification. Rules derived from the JetBrains `swot`
# academic-TLD set, applied at the domain-*label* level to avoid false positives
# (e.g. "communi-cations.com" must NOT match on "uni-", "universitypizza.com"
# must NOT match on "university").
#
# Coverage: *.edu, *.ac.<cc> and *.edu.<cc> for ANY country (so .edu.au, .ac.uk,
# .ac.nz, .ac.jp, .ac.za, .edu.cn, ... are all handled), university-style labels
# (uni / univ / uni-* / univ-*), plus a curated allowlist of research
# institutions whose domains follow none of these patterns.
_ACADEMIC_TLD2 = {"ac", "edu"}  # second-level academic labels: x.ac.uk, y.edu.au
_ACADEMIC_UNI_LABELS = {"uni", "univ", "university", "college", "institute"}
_ACADEMIC_ALLOWLIST = {
    "cern.ch", "ethz.ch", "epfl.ch", "psi.ch",                 # CH
    "mpg.de", "tum.de", "kit.edu", "desy.de", "fu-berlin.de",  # DE
    "cnrs.fr", "inria.fr", "cea.fr", "ens.fr",                 # FR
    "csic.es", "sissa.it", "infn.it",                          # ES/IT
    "riken.jp", "kek.jp", "u-tokyo.ac.jp",                     # JP
    "weizmann.ac.il", "technion.ac.il",                        # IL
    "ornl.gov", "lanl.gov", "lbl.gov", "nist.gov", "anl.gov",  # US nat labs
    "nasa.gov", "sandia.gov", "pnnl.gov", "fnal.gov",
}


def is_academic_domain(email: str) -> bool:
    domain = (email.rsplit("@", 1)[-1] or "").strip().lower().rstrip(".")
    if not domain:
        return False
    if domain in _ACADEMIC_ALLOWLIST:
        return True
    labels = domain.split(".")
    if labels[-1] == "edu":                                # *.edu (US and others)
        return True
    if len(labels) >= 3 and labels[-2] in _ACADEMIC_TLD2:  # *.ac.<cc> / *.edu.<cc>
        return True
    for lab in labels[:-1]:                                # university-style labels (never the TLD)
        if lab in _ACADEMIC_UNI_LABELS or lab.startswith(("uni-", "univ-")):
            return True
    return False


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
