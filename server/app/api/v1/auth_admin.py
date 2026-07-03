"""Auth, accounts, API keys, and admin tier management."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from quantumledger_core.models import Account, ApiKey, Org, OrgMembership, Workspace

from ...db import get_db
from ...deps import Principal, current_principal, require_principal
from ...entitlements import effective_plan, features_for
from ...security import generate_api_key
from ...services import accounts as acc_svc

router = APIRouter(prefix="/api/v1", tags=["auth"])


class RegisterIn(BaseModel):
    email: EmailStr
    password: str
    display_name: str | None = None


class LoginIn(BaseModel):
    email: EmailStr
    password: str


def _login_session(request: Request, db: Session, account: Account) -> None:
    request.session["account_id"] = account.id
    m = db.scalar(select(OrgMembership).where(OrgMembership.account_id == account.id))
    if m:
        ws = db.scalar(select(Workspace).where(Workspace.org_id == m.org_id))
        if ws:
            request.session["workspace_id"] = ws.id


@router.post("/auth/register")
def register(body: RegisterIn, request: Request, db: Session = Depends(get_db)):
    try:
        acc = acc_svc.register(db, email=body.email, password=body.password,
                               display_name=body.display_name)
    except ValueError as e:
        raise HTTPException(409, str(e))
    db.commit()
    _login_session(request, db, acc)
    return {"account_id": acc.id, "email": acc.email}


@router.post("/auth/login")
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    acc = acc_svc.authenticate(db, email=body.email, password=body.password)
    if acc is None:
        raise HTTPException(401, "invalid credentials")
    _login_session(request, db, acc)
    return {"account_id": acc.id, "email": acc.email}


@router.post("/auth/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@router.post("/auth/verify-email")
def verify_email(db: Session = Depends(get_db), p: Principal = Depends(require_principal)):
    acc = db.get(Account, p.account_id)
    acc_svc.verify_email(db, acc)
    db.commit()
    return {"email_verified": acc.email_verified, "academic_verified": acc.academic_verified}


@router.get("/me")
def me(db: Session = Depends(get_db), p: Principal | None = Depends(current_principal)):
    if p is None:
        return {"authenticated": False}
    return {"authenticated": True, "account_id": p.account_id, "email": p.email,
            "is_superadmin": p.is_superadmin, "org_id": p.org_id, "org_role": p.org_role,
            "workspace_id": p.workspace_id, "plan": p.plan, "features": sorted(p.features)}


# Scopes that unlock automation surfaces; minting one requires superadmin.
PRIVILEGED_SCOPES = {"growth"}


class ApiKeyIn(BaseModel):
    name: str = "default"
    scopes: list[str] | None = None


@router.post("/orgs/{org_id}/api-keys")
def create_api_key(org_id: str, body: ApiKeyIn | None = None, name: str = "default",
                   db: Session = Depends(get_db),
                   p: Principal = Depends(require_principal)):
    if p.org_id != org_id and not p.is_superadmin:
        raise HTTPException(403, "forbidden")
    body = body or ApiKeyIn(name=name)
    scopes = list(dict.fromkeys(body.scopes or []))  # dedupe, keep order
    if any(s in PRIVILEGED_SCOPES for s in scopes) and not p.is_superadmin:
        raise HTTPException(403, "privileged scopes require superadmin")
    full, prefix, key_hash = generate_api_key()
    k = ApiKey(org_id=org_id, account_id=p.account_id, name=body.name, prefix=prefix,
               key_hash=key_hash, scopes=scopes or None)
    db.add(k)
    db.commit()
    return {"api_key": full, "prefix": prefix, "scopes": scopes,
            "note": "store this now; it is not shown again"}


# -- admin ------------------------------------------------------------------

@router.get("/admin/orgs")
def admin_list_orgs(db: Session = Depends(get_db), p: Principal = Depends(require_principal)):
    if not p.is_superadmin:
        raise HTTPException(403, "superadmin only")
    out = []
    for org in db.scalars(select(Org)):
        out.append({"id": org.id, "name": org.name, "slug": org.slug,
                    "plan": org.plan, "effective_plan": effective_plan(db, org)})
    return out


@router.post("/admin/orgs/{org_id}/upgrade")
def admin_upgrade(org_id: str, plan: str, db: Session = Depends(get_db),
                  p: Principal = Depends(require_principal)):
    """Admin-driven upgrade: assign a plan grant (invoicing handled off-platform)."""
    if not p.is_superadmin:
        raise HTTPException(403, "superadmin only")
    org = db.get(Org, org_id)
    if org is None:
        raise HTTPException(404, "org not found")
    acc_svc.grant_plan(db, org, plan, source="admin_override", granted_by=p.account_id)
    db.commit()
    return {"org_id": org.id, "plan": org.plan, "effective_plan": effective_plan(db, org),
            "features": sorted(features_for(effective_plan(db, org)))}
