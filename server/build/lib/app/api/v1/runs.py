"""Runs read API + reproduce + card publish + reproduction submit."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import false, select
from sqlalchemy.orm import Session

from provenova_core.models import ReproductionEvent, ResultCard, Run, VIS_PUBLIC, Workspace
from provenova_core.provenance import build_run_doc
from provenova_core.reproduce import runner
from provenova_core.reproduce.report import build_report

from ...config import get_settings
from ...db import get_db
from ...deps import Principal, owned_run, require_feature, require_principal
from ...ratelimit import rate_limit
from ...services import cards as cards_svc
from ...services import doi as doi_svc
from ...services.accounts import audit

router = APIRouter(prefix="/api/v1", tags=["runs"])


def _visible_runs(db: Session, principal: Principal):
    """Runs the caller may list. Fail closed: superadmins see everything, an
    ordinary principal sees only its own workspace, and a principal without a
    workspace sees nothing (never the whole table)."""
    stmt = select(Run).order_by(Run.created_at.desc())
    if principal.is_superadmin:
        return stmt
    if not principal.workspace_id:
        return stmt.where(false())
    return stmt.where(Run.workspace_id == principal.workspace_id)


@router.get("/runs")
def list_runs(limit: int = Query(50, le=500), db: Session = Depends(get_db),
              p: Principal = Depends(require_principal)):
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
def reproduce(run_id: str, days: float = Query(30.0, ge=0, le=365), profile: str = "typical",
              db: Session = Depends(get_db),
              p: Principal = Depends(require_feature("reproduce")),
              _rl: None = Depends(rate_limit("reproduce", limit=30, window_s=300))):
    from provenova_core.simulate.drift import PROFILES

    if profile not in PROFILES:
        raise HTTPException(422, f"unknown profile; choose one of {sorted(PROFILES)}")
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
    settings = get_settings()
    card, mint = cards_svc.publish_card(
        db, card, plan=p.plan, provider=doi_svc.provider_for(settings),
        base_url=settings.base_url)
    audit(db, workspace_id=run.workspace_id, account_id=p.account_id, action="card.publish",
          resource_type="card", resource_id=card.id)
    if mint["status"] in ("minted", "mint_failed", "quota_exceeded"):
        audit(db, workspace_id=run.workspace_id, account_id=p.account_id,
              action="card.doi.mint", resource_type="card", resource_id=card.id,
              detail=mint)
    db.commit()
    return {"slug": card.slug, "visibility": card.visibility, "pid": card.pid,
            "doi": card.doi, "doi_status": mint["status"]}


@router.post("/runs/{run_id}/card/unpublish")
def unpublish(run_id: str, db: Session = Depends(get_db),
              p: Principal = Depends(require_principal)):
    if not p.can("publish"):  # mirror publish — retracting a card has side effects
        raise HTTPException(403, "forbidden")
    run = owned_run(db, run_id, p)
    card = db.scalar(select(ResultCard).where(ResultCard.run_id == run_id))
    if card is None:
        raise HTTPException(404, "no card")
    cards_svc.unpublish_card(db, card, provider=doi_svc.provider_for(get_settings()))
    audit(db, workspace_id=run.workspace_id, account_id=p.account_id, action="card.unpublish",
          resource_type="card", resource_id=card.id)
    db.commit()
    return {"slug": card.slug, "visibility": card.visibility}


@router.post("/runs/{run_id}/card/mint-doi")
def mint_doi(run_id: str, db: Session = Depends(get_db),
             p: Principal = Depends(require_feature("public_result_cards"))):
    """Explicit, opt-in DOI mint via Zenodo for an already-public card."""
    if not p.can("publish"):
        raise HTTPException(403, "forbidden")
    run = owned_run(db, run_id, p)
    card = db.scalar(select(ResultCard).where(ResultCard.run_id == run_id))
    if card is None or card.visibility != VIS_PUBLIC:
        raise HTTPException(409, "card must be published first")
    if card.doi:
        raise HTTPException(409, {"error": "exists", "doi": card.doi})
    provider = doi_svc.zenodo_provider(get_settings())
    if provider is None:
        raise HTTPException(400, "DOI minting is not configured (no Zenodo token)")
    info = cards_svc.mint_card_doi(db, card, provider=provider, plan=p.plan,
                                   base_url=get_settings().base_url)
    if info["status"] == "quota_exceeded":
        raise HTTPException(402, {"error": "quota_exceeded", **info})
    if info["status"] == "mint_failed":
        raise HTTPException(502, {"error": "mint_failed", **info})
    if info["status"] == "exists":
        raise HTTPException(409, {"error": "exists", "doi": info["doi"]})
    audit(db, workspace_id=run.workspace_id, account_id=p.account_id,
          action="card.doi.mint", resource_type="card", resource_id=card.id, detail=info)
    db.commit()
    return {"slug": card.slug, "doi": card.doi, "doi_status": info["status"],
            "record_url": info.get("record_url")}
