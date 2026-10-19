"""Internal endpoints for the platform's own scheduler (not part of the public API).

``POST /internal/collect`` runs the due data collectors. On provenova.net the
Cloudflare Worker cron calls it daily; anyone holding ``QL_COLLECT_TOKEN`` can
also trigger it by hand (``scripts/trigger_collect.py``). With no token
configured the endpoint doesn't exist (404), and a wrong token gets the same
404, so it never advertises itself.
"""

from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..services import collectors

router = APIRouter(include_in_schema=False)


def _authorised(request: Request) -> bool:
    token = get_settings().collect_token
    header = request.headers.get("authorization") or ""
    return bool(token) and hmac.compare_digest(header.encode(), f"Bearer {token}".encode())


@router.post("/internal/collect")
def collect(request: Request, force: str = "", db: Session = Depends(get_db)):
    if not _authorised(request):
        raise HTTPException(404)
    names = {n.strip() for n in force.split(",") if n.strip()}
    if "all" in names:
        names = {c.name for c in collectors.COLLECTORS}
    return {"results": collectors.run_due(db, force=names)}
