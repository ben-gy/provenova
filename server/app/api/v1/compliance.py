"""Compliance API: frameworks, enable, evaluate, status, gaps, attestations."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from quantumledger_core.models import (
    Attestation,
    ComplianceAlert,
    ComplianceFramework,
    Control,
    EvidenceItem,
    Workspace,
    WorkspaceFramework,
)

from ...db import attestation_key, get_db
from ...deps import Principal, require_feature, require_principal
from ...entitlements import is_unlimited, quota_for
from ...services import compliance as comp
from ...services import limits as limits_svc
from ...services.accounts import audit
from ...services.attestation import create_attestation, revoke_attestation

router = APIRouter(prefix="/api/v1", tags=["compliance"])


def _ws(db: Session, ws_id: str, p: Principal) -> Workspace:
    """Fetch a workspace only if it belongs to the caller's org (or superadmin)."""
    ws = db.get(Workspace, ws_id)
    if ws is None or (not p.is_superadmin and ws.org_id != p.org_id):
        raise HTTPException(404, "workspace not found")
    return ws


@router.get("/frameworks")
def list_frameworks(db: Session = Depends(get_db)):
    out = []
    for fw in db.scalars(select(ComplianceFramework)):
        n = db.scalar(select(Control).where(Control.framework_id == fw.id))
        controls = db.scalars(select(Control).where(Control.framework_id == fw.id)).all()
        out.append({"id": fw.id, "key": fw.key, "name": fw.name, "version": fw.version,
                    "jurisdiction": fw.jurisdiction, "controls": len(controls)})
    return out


@router.get("/frameworks/{fw_id}")
def get_framework(fw_id: str, db: Session = Depends(get_db)):
    fw = db.get(ComplianceFramework, fw_id)
    if fw is None:
        raise HTTPException(404, "not found")
    controls = db.scalars(select(Control).where(Control.framework_id == fw.id)).all()
    return {"id": fw.id, "key": fw.key, "name": fw.name, "version": fw.version,
            "controls": [{"key": c.key, "title": c.title, "severity": c.severity,
                          "evidence_rules": c.evidence_rules} for c in controls]}


@router.post("/workspaces/{ws_id}/frameworks/{fw_id}/enable")
def enable(ws_id: str, fw_id: str, db: Session = Depends(get_db),
           p: Principal = Depends(require_feature("compliance_frameworks"))):
    ws = _ws(db, ws_id, p)
    fw = db.get(ComplianceFramework, fw_id)
    if fw is None:
        raise HTTPException(404, "framework not found")
    # Plan limits: Free may enable only FAIR; every plan is capped by
    # frameworks_allowed. Re-enabling an already-enabled framework is idempotent.
    already = db.scalar(select(WorkspaceFramework).where(
        WorkspaceFramework.workspace_id == ws.id, WorkspaceFramework.framework_id == fw.id))
    if already is None:
        if p.plan == "free" and not fw.key.startswith("fair"):
            raise HTTPException(402, detail={"error": "upgrade_required",
                                             "message": "Free includes FAIR only"})
        cap = quota_for(p.plan, "frameworks_allowed")
        if not is_unlimited(cap) and limits_svc.frameworks_enabled_count(db, ws.id) >= cap:
            raise HTTPException(402, detail={"error": "framework_limit",
                                             "message": f"framework limit reached ({cap})"})
    comp.enable_framework(db, ws, fw)
    audit(db, workspace_id=ws.id, account_id=p.account_id, action="compliance.enable",
          resource_type="framework", resource_id=fw.id)
    db.commit()
    return {"workspace_id": ws.id, "framework": fw.key, "enabled": True}


@router.post("/workspaces/{ws_id}/compliance/evaluate")
def evaluate(ws_id: str, db: Session = Depends(get_db),
             p: Principal = Depends(require_feature("compliance_frameworks"))):
    ws = _ws(db, ws_id, p)
    results = comp.evaluate_all(db, ws)
    db.commit()
    return {"workspace_id": ws.id, "results": results}


@router.get("/workspaces/{ws_id}/compliance")
def status(ws_id: str, db: Session = Depends(get_db),
           p: Principal = Depends(require_principal)):
    ws = _ws(db, ws_id, p)
    out = []
    for wf in db.scalars(select(WorkspaceFramework).where(WorkspaceFramework.workspace_id == ws.id)):
        fw = db.get(ComplianceFramework, wf.framework_id)
        out.append({"framework": fw.name if fw else wf.framework_id, "framework_id": wf.framework_id,
                    "status": wf.status, "detail": wf.status_detail,
                    "last_evaluated_at": wf.last_evaluated_at.isoformat() if wf.last_evaluated_at else None})
    return {"workspace_id": ws.id, "frameworks": out}


@router.get("/workspaces/{ws_id}/gaps")
def gaps(ws_id: str, db: Session = Depends(get_db),
         p: Principal = Depends(require_principal)):
    ws = _ws(db, ws_id, p)
    alerts = db.scalars(
        select(ComplianceAlert).where(ComplianceAlert.workspace_id == ws.id,
                                      ComplianceAlert.resolved.is_(False))
    ).all()
    return [{"id": a.id, "kind": a.kind, "message": a.message,
             "framework_id": a.framework_id, "control_id": a.control_id} for a in alerts]


@router.post("/workspaces/{ws_id}/attestations")
def create(ws_id: str, framework_id: str, db: Session = Depends(get_db),
           p: Principal = Depends(require_feature("attestation_signing"))):
    ws = _ws(db, ws_id, p)
    fw = db.get(ComplianceFramework, framework_id)
    if fw is None:
        raise HTTPException(404, "framework not found")
    control_ids = [c.id for c in db.scalars(select(Control).where(Control.framework_id == fw.id))]
    items = db.scalars(
        select(EvidenceItem).where(EvidenceItem.workspace_id == ws.id,
                                   EvidenceItem.control_id.in_(control_ids))
    ).all()
    if not items:
        raise HTTPException(400, "no evidence collected; evaluate the framework first")
    priv, kid, _jwks = attestation_key()
    att = create_attestation(db, workspace=ws, framework=fw, subject_type="workspace",
                             subject_id=ws.id, evidence_items=items, private_key=priv, kid=kid,
                             issuer_org=ws.org_id)
    audit(db, workspace_id=ws.id, account_id=p.account_id, action="attestation.create",
          resource_type="attestation", resource_id=att.id)
    db.commit()
    return {"attestation_id": att.id, "evidence_root": att.evidence_root, "kid": att.kid,
            "evidence_items": len(items),
            "verify_url": f"/api/v1/attestations/{att.id}/verify"}


@router.post("/attestations/{att_id}/revoke")
def revoke(att_id: str, db: Session = Depends(get_db),
           p: Principal = Depends(require_feature("attestation_signing"))):
    att = db.get(Attestation, att_id)
    if att is None:
        raise HTTPException(404, "not found")
    ws = db.get(Workspace, att.workspace_id)
    if not p.is_superadmin and (ws is None or ws.org_id != p.org_id):
        raise HTTPException(404, "not found")
    revoke_attestation(db, att)
    audit(db, workspace_id=att.workspace_id, account_id=p.account_id, action="attestation.revoke",
          resource_type="attestation", resource_id=att.id)
    db.commit()
    return {"attestation_id": att.id, "revoked": True}
