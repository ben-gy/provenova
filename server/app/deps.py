"""FastAPI dependencies: principal resolution, entitlement gating, RBAC."""

from __future__ import annotations

from dataclasses import dataclass, field

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from quantumledger_core.models import (
    Account,
    ApiKey,
    Org,
    OrgMembership,
    Run,
    Workspace,
    WorkspaceMember,
)

from .db import get_db
from .entitlements import effective_plan, features_for, has_feature
from .rbac import can as rbac_can
from .security import verify_api_key


@dataclass
class Principal:
    account_id: str
    email: str
    is_superadmin: bool = False
    org_id: str | None = None
    org_role: str | None = None
    workspace_id: str | None = None
    ws_role: str | None = None
    plan: str = "free"
    features: set[str] = field(default_factory=set)
    # API-key scopes (empty for session/JWT principals). Privileged endpoints
    # gate on require_scope(); superadmins bypass.
    scopes: set[str] = field(default_factory=set)

    def has(self, feature: str) -> bool:
        return feature in self.features

    def can(self, action: str) -> bool:
        return rbac_can(org_role=self.org_role, ws_role=self.ws_role, action=action,
                        is_superadmin=self.is_superadmin)


def _resolve_from_api_key(db: Session, token: str) -> tuple[Account, Org, ApiKey] | None:
    prefix = token[:16]
    keys = db.scalars(select(ApiKey).where(ApiKey.prefix == prefix, ApiKey.revoked.is_(False))).all()
    for k in keys:
        if verify_api_key(token, k.key_hash):
            acc = db.get(Account, k.account_id)
            org = db.get(Org, k.org_id)
            if acc and org:
                return acc, org, k
    return None


def _build_principal(db: Session, account: Account, org: Org | None,
                     workspace_id: str | None, scopes: set[str] | None = None) -> Principal:
    org_role = None
    if org is not None:
        m = db.scalar(
            select(OrgMembership).where(
                OrgMembership.account_id == account.id, OrgMembership.org_id == org.id
            )
        )
        org_role = m.role if m else None
    plan = effective_plan(db, org) if org else "free"
    # workspace resolution — a caller-supplied workspace_id (query param or
    # session) is only trusted if it belongs to THIS principal's org. Otherwise
    # it is ignored and we fall back to the org's default workspace. Without this
    # check, `?workspace_id=<other tenant>` would grant cross-tenant access.
    ws = None
    if workspace_id:
        candidate = db.get(Workspace, workspace_id)
        if candidate is not None and (
            account.is_superadmin or (org is not None and candidate.org_id == org.id)
        ):
            ws = candidate
    if ws is None and org is not None:
        ws = db.scalar(select(Workspace).where(Workspace.org_id == org.id))
    ws_role = None
    if ws is not None:
        wm = db.scalar(
            select(WorkspaceMember).where(
                WorkspaceMember.account_id == account.id, WorkspaceMember.workspace_id == ws.id
            )
        )
        ws_role = wm.role if wm else ("ws_admin" if org_role in ("owner", "admin") else None)
    return Principal(
        account_id=account.id,
        email=account.email,
        is_superadmin=account.is_superadmin,
        org_id=org.id if org else None,
        org_role=org_role,
        workspace_id=ws.id if ws else None,
        ws_role=ws_role,
        plan=plan,
        features=features_for(plan),
        scopes=scopes or set(),
    )


def current_principal(request: Request, db: Session = Depends(get_db)) -> Principal | None:
    # 1) web session cookie
    account_id = None
    try:
        account_id = request.session.get("account_id")
    except Exception:
        account_id = None
    workspace_id = request.query_params.get("workspace_id") or (
        request.session.get("workspace_id") if hasattr(request, "session") else None
    )
    if account_id:
        acc = db.get(Account, account_id)
        if acc:
            org = _first_org(db, acc)
            return _build_principal(db, acc, org, workspace_id)
    # 2) bearer token (API key or JWT)
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:].strip()
        if token.startswith("ql_live_"):
            resolved = _resolve_from_api_key(db, token)
            if resolved:
                acc, org, key = resolved
                return _build_principal(db, acc, org, workspace_id,
                                        scopes=set(key.scopes or []))
        else:
            from .security import decode_access_token

            claims = decode_access_token(token)
            if claims:
                acc = db.get(Account, claims["sub"])
                org = db.get(Org, claims.get("org_id")) if claims.get("org_id") else None
                if acc:
                    return _build_principal(db, acc, org or _first_org(db, acc), workspace_id)
    return None


def _first_org(db: Session, account: Account) -> Org | None:
    m = db.scalar(select(OrgMembership).where(OrgMembership.account_id == account.id))
    return db.get(Org, m.org_id) if m else None


def require_principal(p: Principal | None = Depends(current_principal)) -> Principal:
    if p is None:
        raise HTTPException(status_code=401, detail="authentication required")
    return p


def owned_run(db: Session, run_id: str, p: Principal | None) -> Run:
    """Return a run only if the caller owns it (same workspace) or is superadmin.

    Runs are private provenance; public sharing happens via Result Cards. Used by
    both the web and REST layers to prevent cross-tenant read/write by run id.
    Raises 401 if unauthenticated, 404 otherwise (don't leak existence).
    """
    if p is None:
        raise HTTPException(status_code=401, detail="authentication required")
    run = db.get(Run, run_id)
    if run is None or (not p.is_superadmin and run.workspace_id != p.workspace_id):
        raise HTTPException(status_code=404, detail="not found")
    return run


def require_feature(feature: str):
    def _dep(p: Principal = Depends(require_principal)) -> Principal:
        if not has_feature(p.plan, feature):
            raise HTTPException(
                status_code=402,
                detail={"error": "upgrade_required", "feature": feature, "plan": p.plan},
            )
        return p

    return _dep


def require_action(action: str):
    def _dep(p: Principal = Depends(require_principal)) -> Principal:
        if not p.can(action):
            raise HTTPException(status_code=403, detail=f"forbidden: {action}")
        return p

    return _dep


def require_scope(scope: str):
    """Gate an endpoint on an API-key scope (superadmins bypass).

    Session/JWT principals carry no scopes, so scoped endpoints are effectively
    API-key-only for non-superadmins — the right posture for automation surfaces
    like the Growth API.
    """

    def _dep(p: Principal = Depends(require_principal)) -> Principal:
        if p.is_superadmin or scope in p.scopes:
            return p
        raise HTTPException(status_code=403, detail=f"scope required: {scope}")

    return _dep
