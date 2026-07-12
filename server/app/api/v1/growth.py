"""Growth API: the authenticated surface the scheduled research routine calls.

All endpoints require the ``growth`` API-key scope (superadmin bypasses) and
are audit-logged. Server-side rails (validation, caps, sanitization) are
enforced here regardless of routine behaviour — see services/growth.py.
"""

from __future__ import annotations

import datetime as _dt
import logging
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from provenova_core.models import AuditLog, Report

from ...config import get_settings

_log = logging.getLogger(__name__)
from ...db import get_db
from ...deps import Principal, require_scope
from ...services import growth as growth_svc
from ...services import indexnow
from ...services import metriq as metriq_svc
from ...services.accounts import audit

router = APIRouter(prefix="/api/v1/growth", tags=["growth"])

_ALLOWED_PAPER_HOSTS = {"arxiv.org", "www.arxiv.org", "doi.org", "dx.doi.org"}


# --- corpus refresh ----------------------------------------------------------

@router.post("/corpus/refresh")
def corpus_refresh(force: bool = False, db: Session = Depends(get_db),
                   p: Principal = Depends(require_scope("growth"))):
    settings = get_settings()
    # `force` (skip the interval throttle) is superadmin-only, so a growth-scoped
    # key can't hammer the GitHub crawl in a loop. The routine never needs force:
    # an incomplete/failed refresh writes no throttle audit (below), so it can
    # immediately "call once more".
    allow_force = force and p.is_superadmin
    last = db.scalar(
        select(AuditLog).where(AuditLog.action == "growth.corpus.refresh")
        .order_by(AuditLog.created_at.desc()))
    if last is not None and not allow_force:
        age = _dt.datetime.now(_dt.timezone.utc) - (
            last.created_at if last.created_at.tzinfo
            else last.created_at.replace(tzinfo=_dt.timezone.utc))
        if age < _dt.timedelta(hours=settings.growth_refresh_min_hours):
            raise HTTPException(429, detail={
                "error": "refresh_too_soon",
                "retry_after_hours": settings.growth_refresh_min_hours,
                "last_refresh": last.created_at.isoformat()})
    growth_svc.ensure_research_bot(db)
    db.commit()
    try:
        result = metriq_svc.refresh_metriq_corpus(db)
    except Exception:  # upstream (GitHub) failure — surface a status, not internals
        _log.exception("metriq corpus refresh failed")
        raise HTTPException(502, detail={"error": "upstream_fetch_failed"})
    # Only stamp the throttle audit on a COMPLETE refresh — so a deadline-cut or
    # failed run doesn't lock refresh for 6h or mislabel corpus freshness, and
    # the routine's resumable "call again" contract holds.
    if result.get("complete"):
        audit(db, workspace_id=None, account_id=p.account_id, action="growth.corpus.refresh",
              detail={"inserted": result.get("inserted"), "fetched": result.get("fetched")})
        db.commit()
    return result


# --- research cards ----------------------------------------------------------

class PaperIn(BaseModel):
    title: str = Field(min_length=5, max_length=500)
    authors: list[str] = Field(min_length=1, max_length=50)
    year: int | None = Field(default=None, ge=1990, le=2100)
    arxiv_id: str | None = Field(default=None, pattern=r"^\d{4}\.\d{4,5}(v\d+)?$")
    doi: str | None = Field(default=None, max_length=120)
    url: str = Field(max_length=500)
    abstract_snippet: str | None = Field(default=None, max_length=600)

    @field_validator("arxiv_id")
    @classmethod
    def _strip_version(cls, v: str | None) -> str | None:
        # Canonicalise '2606.01234v2' -> '2606.01234' so a revised preprint of
        # the same circuit dedupes against the original.
        return v.split("v", 1)[0] if v else v

    @field_validator("doi")
    @classmethod
    def _normalize_doi(cls, v: str | None) -> str | None:
        return v.strip().lower() or None if v else v

    @model_validator(mode="after")
    def _need_identifier(self):
        if not self.arxiv_id and not self.doi:
            raise ValueError("paper needs arxiv_id or doi")
        return self

    @field_validator("url")
    @classmethod
    def _host_allowlist(cls, v: str) -> str:
        host = (urlparse(v).hostname or "").lower()
        if urlparse(v).scheme != "https" or host not in _ALLOWED_PAPER_HOSTS:
            raise ValueError("url must be https on arxiv.org or doi.org")
        return v


class ResearchCardIn(BaseModel):
    paper: PaperIn
    circuit: dict
    shots: int = Field(default=4096, ge=100, le=growth_svc.MAX_SHOTS)
    seed: int = Field(default=1729, ge=0, le=2**31 - 1)
    title: str = Field(min_length=10, max_length=200)
    commentary_md: str = Field(min_length=100, max_length=4000)
    relation: str = Field(default="inspired_by", pattern=r"^(inspired_by|references)$")


class ResearchCardsIn(BaseModel):
    items: list[ResearchCardIn] = Field(min_length=1, max_length=5)


@router.post("/research-cards")
def research_cards(body: ResearchCardsIn, db: Session = Depends(get_db),
                   p: Principal = Depends(require_scope("growth"))):
    settings = get_settings()
    bot = growth_svc.ensure_research_bot(db)
    results = []
    new_urls = []
    for item in body.items:
        # 1) validate circuit (gate allowlist + size caps)
        try:
            canonical = growth_svc.validate_qlir(item.circuit)
        except growth_svc.QlirValidationError as e:
            results.append({"status": "invalid", "error": str(e)})
            continue
        sha = growth_svc.circuit_sha256(canonical)
        # 2) idempotency
        existing = growth_svc.find_existing(
            db, arxiv_id=item.paper.arxiv_id, doi=item.paper.doi, sha=sha)
        if existing is not None:
            from provenova_core.models import ResultCard

            card = db.get(ResultCard, existing.card_id)
            results.append({"status": "exists", "slug": card.slug if card else None})
            continue
        # 3) daily cap (server-side, from DB)
        if growth_svc.cards_today(db) >= settings.growth_max_cards_per_day:
            results.append({"status": "cap_reached",
                            "daily_cap": settings.growth_max_cards_per_day})
            continue
        # 4) record run + reproduce + publish + attribute. Isolate each item so
        # one failure can't 500 the request after earlier cards are already live.
        try:
            out = growth_svc.create_research_card(
                db, bot=bot, paper=item.paper.model_dump(), canonical_circuit=canonical,
                sha=sha, shots=item.shots, seed=item.seed, title=item.title,
                commentary_md=item.commentary_md, relation=item.relation)
            db.commit()
        except Exception as e:  # noqa: BLE001 — record, roll back, keep going
            db.rollback()
            results.append({"status": "error", "error": type(e).__name__})
            continue
        new_urls.append(out["card_url"])
        results.append(out)
    if new_urls:
        pinged = indexnow.ping(new_urls)
        audit(db, workspace_id=None, account_id=p.account_id, action="growth.indexnow.ping",
              detail={"urls": new_urls, "ok": pinged})
        db.commit()
    return {"items": results,
            "created": sum(1 for r in results if r["status"] == "created")}


# --- reports -------------------------------------------------------------------

class ReportIn(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9-]{8,80}$")
    title: str = Field(min_length=10, max_length=200)
    body_md: str = Field(min_length=500, max_length=40_000)
    meta_description: str = Field(min_length=30, max_length=200)
    kind: str = Field(default="weekly_fleet", max_length=30)


@router.post("/reports")
def publish_report(body: ReportIn, db: Session = Depends(get_db),
                   p: Principal = Depends(require_scope("growth"))):
    settings = get_settings()
    if db.scalar(select(Report).where(Report.slug == body.slug)) is not None:
        raise HTTPException(409, detail={"error": "slug_exists", "slug": body.slug})
    if growth_svc.reports_this_week(db) >= settings.growth_max_reports_per_week:
        raise HTTPException(429, detail={
            "error": "report_cap_reached",
            "weekly_cap": settings.growth_max_reports_per_week})
    bot_acc, _, _ = growth_svc.ensure_research_bot(db)
    r = growth_svc.publish_report(
        db, bot_account_id=bot_acc.id, slug=body.slug, title=body.title,
        body_md=body.body_md, meta_description=body.meta_description, kind=body.kind)
    db.commit()
    url = f"{settings.base_url}/reports/{r.slug}"
    pinged = indexnow.ping([url])
    audit(db, workspace_id=None, account_id=p.account_id, action="growth.indexnow.ping",
          detail={"urls": [url], "ok": pinged})
    db.commit()
    return {"slug": r.slug, "url": url, "published_at": r.published_at.isoformat()}


# --- status --------------------------------------------------------------------

@router.get("/status")
def status(db: Session = Depends(get_db), p: Principal = Depends(require_scope("growth"))):
    last = db.scalar(
        select(AuditLog).where(AuditLog.action == "growth.corpus.refresh")
        .order_by(AuditLog.created_at.desc()))
    out = growth_svc.growth_status(db)
    out["corpus"]["last_refresh_attempt"] = (
        last.created_at.isoformat() if last and last.created_at else None)
    return out
