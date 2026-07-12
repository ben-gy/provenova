"""Auth, accounts, API keys, and admin tier management."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from provenova_core.models import Account, ApiKey, Org, OrgMembership, Workspace

from ...config import get_settings
from ...db import get_db
from ...deps import Principal, current_principal, require_principal
from ...entitlements import effective_plan, features_for
from ...ratelimit import rate_limit
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


class VerifyEmailIn(BaseModel):
    token: str


def _login_session(request: Request, db: Session, account: Account) -> None:
    request.session["account_id"] = account.id
    request.session["tv"] = account.token_version  # session-revocation stamp
    m = db.scalar(select(OrgMembership).where(OrgMembership.account_id == account.id))
    if m:
        ws = db.scalar(select(Workspace).where(Workspace.org_id == m.org_id))
        if ws:
            request.session["workspace_id"] = ws.id


@router.post("/auth/register")
def register(body: RegisterIn, request: Request, db: Session = Depends(get_db),
             _rl: None = Depends(rate_limit("auth-register", limit=8, window_s=600))):
    try:
        acc = acc_svc.register(db, email=body.email, password=body.password,
                               display_name=body.display_name)
    except ValueError as e:
        raise HTTPException(409, str(e))
    db.commit()
    token = acc_svc.request_email_verification(db, acc, base_url=get_settings().base_url)
    db.commit()
    _login_session(request, db, acc)
    out = {"account_id": acc.id, "email": acc.email, "email_verified": acc.email_verified}
    # Selfhost is single-operator/trusted: surface the token so local flows work
    # without a mail relay. Hosted NEVER returns it (would defeat verification).
    if get_settings().deployment == "selfhost":
        out["dev_verification_token"] = token
    return out


@router.post("/auth/login")
def login(body: LoginIn, request: Request, db: Session = Depends(get_db),
          _rl: None = Depends(rate_limit("auth-login", limit=10, window_s=300))):
    acc = acc_svc.authenticate(db, email=body.email, password=body.password)
    if acc is None:
        raise HTTPException(401, "invalid credentials")
    _login_session(request, db, acc)
    return {"account_id": acc.id, "email": acc.email}


@router.post("/auth/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@router.post("/auth/logout-all")
def logout_all(request: Request, db: Session = Depends(get_db),
               p: Principal = Depends(require_principal)):
    """Revoke every session/cookie for this account by bumping its token_version."""
    acc = db.get(Account, p.account_id)
    if acc is not None:
        acc.token_version = (acc.token_version or 0) + 1
        db.commit()
    request.session.clear()
    return {"ok": True}


@router.post("/auth/request-email-verification")
def request_email_verification(db: Session = Depends(get_db),
                               p: Principal = Depends(require_principal)):
    """(Re)send the verification email for the current account."""
    acc = db.get(Account, p.account_id)
    token = acc_svc.request_email_verification(db, acc, base_url=get_settings().base_url)
    db.commit()
    out = {"sent": True}
    if get_settings().deployment == "selfhost":
        out["dev_verification_token"] = token
    return out


@router.post("/auth/verify-email")
def verify_email(body: VerifyEmailIn, db: Session = Depends(get_db)):
    """Redeem an email-verification token. The token itself is the proof of
    inbox ownership, so this endpoint is intentionally unauthenticated (the link
    is clicked from an email client, possibly without a session)."""
    try:
        acc = acc_svc.redeem_email_verification(db, body.token)
    except ValueError as e:
        raise HTTPException(400, str(e))
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
    # Minting an org credential is a privileged action — viewers/members must not
    # be able to do it. RBAC 'manage' is the org admin/owner gate.
    if not p.can("manage"):
        raise HTTPException(403, "forbidden: manage role required to mint API keys")
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
