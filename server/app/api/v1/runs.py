"""Runs read API + reproduce + card publish + reproduction submit."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from quantumledger_core.models import ReproductionEvent, ResultCard, Run, VIS_PUBLIC, Workspace
from quantumledger_core.provenance import build_run_doc
from quantumledger_core.reproduce import runner
from quantumledger_core.reproduce.report import build_report

from ...db import get_db
from ...deps import Principal, current_principal, owned_run, require_feature, require_principal
from ...services import cards as cards_svc
from ...services.accounts import audit

router = APIRouter(prefix="/api/v1", tags=["runs"])


def _visible_runs(db: Session, principal: Principal | None):
    stmt = select(Run).order_by(Run.created_at.desc())
    if principal and principal.workspace_id:
        stmt = stmt.where(Run.workspace_id == principal.workspace_id)
    return stmt


@router.get("/runs")
def list_runs(limit: int = Query(50, le=500), db: Session = Depends(get_db),
              p: Principal | None = Depends(current_principal)):
    runs = db.scalars(_visible_runs(db, p).limit(limit)).all()
    return [
        {"id": r.id, "project": r.project, "vendor": r.backend.vendor, "backend": r.backend.name,
         "shots": r.shots, "status": r.status, "run_hash": r.run_hash,
         "created_at": r.created_at.isoformat() if r.created_at else None}
        for r in runs
    ]


@router.get("/runs/{run_id}")
def get_run(run_id: str, db: Session = Depends(get_db),
            p: Principal = Depends(require_principal)):
    run = owned_run(db, run_id, p)
    return build_run_doc(run)


@router.get("/runs/{run_id}/report")
def run_report(run_id: str, db: Session = Depends(get_db),
               p: Principal = Depends(require_principal)):
    run = owned_run(db, run_id, p)
    ev = db.scalar(
        select(ReproductionEvent)
        .where(ReproductionEvent.original_run_id == run_id)
        .order_by(ReproductionEvent.created_at.desc())
    )
    if ev is None:
        raise HTTPException(404, "no reproduction yet")
    reproduced = db.get(Run, ev.reproduced_run_id)
    return build_report(run, reproduced, ev)


@router.post("/runs/{run_id}/reproduce")
def reproduce(run_id: str, days: float = 30.0, profile: str = "typical",
              db: Session = Depends(get_db),
              p: Principal = Depends(require_feature("reproduce"))):
    run = owned_run(db, run_id, p)
    ws = db.get(Workspace, run.workspace_id)
    new_run, ev = runner.reproduce_run(db, run, workspace=ws, days=days, profile=profile,
                                       account_id=p.account_id)
    db.commit()
    return {"reproduced_run_id": new_run.id, "verdict": ev.verdict,
            "reproducibility_score": ev.reproducibility_score,
            "report": build_report(run, new_run, ev)}


@router.post("/runs/{run_id}/card/publish")
def publish(run_id: str, db: Session = Depends(get_db),
            p: Principal = Depends(require_feature("public_result_cards"))):
    if not p.can("publish"):
        raise HTTPException(403, "forbidden")
    run = owned_run(db, run_id, p)
    card = cards_svc.get_or_create_card(db, run)
    cards_svc.publish_card(db, card)
    audit(db, workspace_id=run.workspace_id, account_id=p.account_id, action="card.publish",
          resource_type="card", resource_id=card.id)
    db.commit()
    return {"slug": card.slug, "visibility": card.visibility, "pid": card.pid}


@router.post("/runs/{run_id}/card/unpublish")
def unpublish(run_id: str, db: Session = Depends(get_db),
              p: Principal = Depends(require_principal)):
    run = owned_run(db, run_id, p)
    card = db.scalar(select(ResultCard).where(ResultCard.run_id == run_id))
    if card is None:
        raise HTTPException(404, "no card")
    cards_svc.unpublish_card(db, card)
    audit(db, workspace_id=run.workspace_id, account_id=p.account_id, action="card.unpublish",
          resource_type="card", resource_id=card.id)
    db.commit()
    return {"slug": card.slug, "visibility": card.visibility}
