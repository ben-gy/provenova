"""Public, cache-friendly endpoints: badges, cards, citations, embeds,
oEmbed, leaderboard, Trust Center, JWKS, attestation verification,
reproduction submission."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from provenova_core.models import (
    Attestation,
    CalibrationSnapshot,
    ComplianceFramework,
    Org,
    ResultCard,
    Result,
    Run,
    VIS_PUBLIC,
    Workspace,
    WorkspaceFramework,
)

from ...config import get_settings
from ...db import attestation_key, get_db
from ...deps import Principal, require_principal
from ...services import badges as badge_svc
from ...services import cards as cards_svc

router = APIRouter(tags=["public"])

# Standalone Jinja env for the embeddable card (a web↔api import of the main
# templates object would be a cycle; the embed template extends nothing).
_TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "web" / "templates"
_embed_templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


def _public_card(db: Session, slug: str) -> ResultCard:
    card = db.scalar(select(ResultCard).where(ResultCard.slug == slug))
    if card is None or card.visibility != VIS_PUBLIC:
        raise HTTPException(404, "card not found")
    return card


@router.get("/badge/{slug}/{badge_type}.svg")
def badge_svg(slug: str, badge_type: str, style: str = "flat", db: Session = Depends(get_db)):
    card = db.scalar(select(ResultCard).where(ResultCard.slug == slug))
    if card is None:
        msg, color = "unknown", "#9f9f9f"
    else:
        msg, color = badge_svc.badge_state(db, card, badge_type)
    svg = badge_svc.render_svg(msg, color, style=style)
    from provenova_core import hashing

    etag = 'W/"' + hashing.sha256_hex({"s": slug, "t": badge_type, "m": msg})[:24] + '"'
    return Response(
        content=svg,
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=300, stale-while-revalidate=86400", "ETag": etag},
    )


@router.get("/badge/{slug}/{badge_type}.json")
def badge_json(slug: str, badge_type: str, db: Session = Depends(get_db)):
    card = db.scalar(select(ResultCard).where(ResultCard.slug == slug))
    msg = "unknown"
    if card is not None:
        msg, _ = badge_svc.badge_state(db, card, badge_type)
    return badge_svc.endpoint_json(msg, "blue")


@router.get("/api/v1/cards/{slug}")
def get_card(slug: str, db: Session = Depends(get_db)):
    card = _public_card(db, slug)
    return {"slug": card.slug, "title": card.title, "visibility": card.visibility,
            "summary": card.summary, "pid": card.pid, "doi": card.doi,
            "license": card.license, "card_sha256": card.card_sha256,
            "published_at": card.published_at.isoformat() if card.published_at else None}


@router.get("/api/v1/cards/{slug}/citation")
def card_citation(slug: str, format: str = "bibtex", db: Session = Depends(get_db)):
    card = _public_card(db, slug)
    body, media = cards_svc.citation(card, get_settings().base_url, format)
    return Response(content=body, media_type=media)


_BADGE_TYPES = {"recorded", "reproduced", "benchmarked", "compliant", "audit-ready"}


@router.get("/api/v1/cards/{slug}/embed")
def card_embed(slug: str, badge_type: str = "recorded", db: Session = Depends(get_db)):
    # badge_type lands in copy-pasteable snippets; constrain it to the known
    # ladder so a crafted value can't poison the markup a user pastes elsewhere.
    if badge_type not in _BADGE_TYPES:
        raise HTTPException(400, "unknown badge_type")
    card = _public_card(db, slug)
    return cards_svc.embed_snippets(card, get_settings().base_url, badge_type)


@router.api_route("/cards/{slug}/embed.html", methods=["GET", "HEAD"])
def card_embed_html(slug: str, db: Session = Depends(get_db)):
    """Self-contained, iframe-embeddable Result Card (logo + backlink baked in).

    Explicitly framable by third parties; cached like the badge SVGs with an
    ETag bound to the card hash so republish/unpublish invalidates caches.
    """
    card = _public_card(db, slug)
    ctx = cards_svc.embed_card_context(card, get_settings().base_url)
    html = _embed_templates.get_template("embed_card.html").render(**ctx)
    from provenova_core import hashing

    etag = 'W/"' + hashing.sha256_hex({"s": slug, "h": card.card_sha256})[:24] + '"'
    return Response(
        content=html,
        media_type="text/html; charset=utf-8",
        headers={
            # No stale-while-revalidate here: the embed reveals card content, so
            # after an unpublish a shared cache must not keep serving it. (Badge
            # SVGs keep the long SWR — they only ever show a state, not content.)
            "Cache-Control": "public, max-age=300, must-revalidate",
            "ETag": etag,
            "Content-Security-Policy": "frame-ancestors *",
        },
    )


_CARD_URL_RE = re.compile(r"/cards/([a-z0-9-]+)/?$")


@router.get("/api/v1/oembed")
def oembed(url: str = Query(...), maxwidth: int = 400, maxheight: int = 420,
           format: str = "json", db: Session = Depends(get_db)):
    """oEmbed provider for Result Cards (rich type, JSON only)."""
    if format != "json":
        raise HTTPException(501, "only json format is supported")
    base = get_settings().base_url
    # Only embed our own cards — reject foreign hosts outright.
    clean = url.split("?")[0]
    if not clean.startswith(base + "/"):
        raise HTTPException(404, "not a result card url")
    m = _CARD_URL_RE.search(clean)
    if not m:
        raise HTTPException(404, "not a result card url")
    card = _public_card(db, m.group(1))
    snippets = cards_svc.embed_snippets(card, base)
    width, height = max(180, min(maxwidth, 400)), max(180, min(maxheight, 420))
    iframe = snippets["iframe"].replace('width="400"', f'width="{width}"') \
                               .replace('height="420"', f'height="{height}"')
    return {
        "version": "1.0",
        "type": "rich",
        "provider_name": "Provenova",
        "provider_url": base,
        "title": card.title,
        "width": width,
        "height": height,
        "html": iframe,
    }


@router.post("/api/v1/cards/{slug}/reproductions")
def submit_reproduction(slug: str, days: float = 20.0, profile: str = "typical",
                        db: Session = Depends(get_db), p: Principal = Depends(require_principal)):
    """Another user reproduces a public result → records a ReproductionEvent →
    the 'reproduced' badge upgrades to green (E5.3)."""
    from provenova_core.reproduce import runner

    card = _public_card(db, slug)
    run = db.get(Run, card.run_id)
    ws = db.get(Workspace, run.workspace_id)
    new_run, ev = runner.reproduce_run(db, run, workspace=ws, days=days, profile=profile,
                                       account_id=p.account_id)
    db.commit()
    return {"status": "recorded", "verdict": ev.verdict,
            "reproducibility_score": ev.reproducibility_score, "badge": "reproduced"}


@router.get("/api/v1/leaderboard")
def leaderboard(metric: str = "median_2q_error", period: str | None = None,
                db: Session = Depends(get_db)):
    try:
        from provenova_crawler.corpus import LEADERBOARD_METRICS, fleet_leaderboard

        return {"metric": metric, "metrics": LEADERBOARD_METRICS,
                "entries": fleet_leaderboard(db, metric=metric, period=period)}
    except Exception as e:  # crawler not installed / no corpus
        return {"metric": metric, "entries": [], "note": str(e)}


@router.get("/api/v1/backends/{provider}/{backend_id}/trend")
def device_trend(provider: str, backend_id: str, db: Session = Depends(get_db)):
    try:
        from provenova_crawler.corpus import device_timeseries

        return {"provider": provider, "backend_id": backend_id,
                "series": device_timeseries(db, provider, backend_id)}
    except Exception as e:
        return {"provider": provider, "backend_id": backend_id, "series": [], "note": str(e)}


@router.get("/.well-known/provenova-jwks.json")
@router.get("/.well-known/quantumledger-jwks.json")  # permanent legacy alias —
# external verifiers of already-issued attestations may have this URL embedded
def jwks():
    _priv, _kid, jwks_doc = attestation_key()
    return jwks_doc


def live_hashes_for(db: Session, entries: list[dict]) -> dict[str, str]:
    out: dict[str, str] = {}
    for e in entries:
        rid, rtype = e.get("source_ref_id"), e.get("source_ref_type")
        if rtype == "run":
            r = db.get(Run, rid)
            if r:
                out[rid] = r.run_hash
        elif rtype == "result":
            r = db.get(Result, rid)
            if r:
                out[rid] = r.counts_sha256
        elif rtype == "calibration_snapshot":
            r = db.get(CalibrationSnapshot, rid)
            if r:
                out[rid] = r.content_sha256
        elif rtype == "card":
            r = db.get(ResultCard, rid)
            if r:
                out[rid] = r.card_sha256
    return out


@router.get("/api/v1/attestations/{att_id}/verify")
def verify_attestation_endpoint(att_id: str, db: Session = Depends(get_db)):
    from ...services.attestation import verify_attestation

    att = db.get(Attestation, att_id)
    if att is None:
        raise HTTPException(404, "not found")
    _priv, _kid, jwks_doc = attestation_key()
    entries = (att.satisfied_state or {}).get("evidence_entries", [])
    result = verify_attestation(db, att, jwks_doc, live_content_hashes=live_hashes_for(db, entries))
    return {"attestation_id": att.id, "kid": att.kid, "evidence_root": att.evidence_root, **result}


@router.get("/api/v1/trust/{org_slug}")
def trust_center(org_slug: str, db: Session = Depends(get_db)):
    from ...entitlements import effective_plan, has_feature

    org = db.scalar(select(Org).where(Org.slug == org_slug))
    if org is None:
        raise HTTPException(404, "org not found")
    if not has_feature(effective_plan(db, org), "trust_center"):
        return {"org": org.name, "slug": org.slug, "trust_center": "inactive",
                "frameworks": [], "attestations": []}
    ws_ids = [w.id for w in db.scalars(select(Workspace).where(Workspace.org_id == org.id))]
    frameworks = []
    if ws_ids:
        for wf in db.scalars(select(WorkspaceFramework).where(WorkspaceFramework.workspace_id.in_(ws_ids))):
            fw = db.get(ComplianceFramework, wf.framework_id)
            frameworks.append({"framework": fw.name if fw else wf.framework_id, "status": wf.status,
                               "last_evaluated_at": wf.last_evaluated_at.isoformat() if wf.last_evaluated_at else None})
    atts = []
    if ws_ids:
        for a in db.scalars(select(Attestation).where(Attestation.workspace_id.in_(ws_ids),
                                                      Attestation.revoked.is_(False))):
            atts.append({"id": a.id, "framework_id": a.framework_id, "kid": a.kid,
                         "point_in_time": a.point_in_time.isoformat() if a.point_in_time else None,
                         "evidence_root": a.evidence_root})
    return {"org": org.name, "slug": org.slug, "frameworks": frameworks, "attestations": atts}
