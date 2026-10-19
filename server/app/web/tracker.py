"""/tracker — the plain-language quantum progress tracker, plus its JSON."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from ..db import datasets_dir, get_db
from ..deps import Principal, current_principal
from ..services import tracker as tracker_svc
from .routes import render

router = APIRouter(tags=["tracker"])


@router.get("/tracker", response_class=HTMLResponse)
def tracker_page(request: Request, db: Session = Depends(get_db),
                 p: Principal | None = Depends(current_principal)):
    model = tracker_svc.build(db, datasets_dir())
    desc = " ".join(f"{h['kicker']}: {h['value']} {h['label']}" for h in model["headlines"][:2])
    return render(request, "tracker.html", p, m=model, one_in=tracker_svc.one_in,
                  month_year=tracker_svc.month_year,
                  meta_description=(desc or "How quantum computing is progressing, "
                                    "from public, cited data.")[:300],
                  canonical_path="/tracker")


@router.get("/api/v1/tracker", tags=["public"])
def tracker_json(db: Session = Depends(get_db)):
    """Every series and fact behind /tracker, with its source."""
    return tracker_svc.build(db, datasets_dir())
