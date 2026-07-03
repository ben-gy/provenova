"""Ingestion API — receives pushed run bundles from the SDK."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from provenova_core.models import Run, Workspace

from ...db import get_db
from ...deps import Principal, require_principal
from ...services.ingest import materialize_bundle
from ...services.limits import private_run_usage

router = APIRouter(prefix="/api/v1/ingest", tags=["ingest"])


@router.post("/runs")
def ingest_run(bundle: dict, db: Session = Depends(get_db),
               principal: Principal = Depends(require_principal)):
    run_hash = bundle.get("provenance", {}).get("run_hash")
    if not run_hash:
        raise HTTPException(422, "missing provenance.run_hash")
    # Bind strictly to the caller's own (org-validated) workspace — never fall
    # back to a shared global workspace, which would mix tenants' runs.
    ws = db.get(Workspace, principal.workspace_id) if principal.workspace_id else None
    if ws is None:
        raise HTTPException(400, "no target workspace for this account")
    # Private-run cap: only NEW runs count (re-pushing an existing run is
    # idempotent and always allowed). Publishing a run publicly frees a slot.
    is_new = db.scalar(select(Run.id).where(Run.workspace_id == ws.id, Run.run_hash == run_hash)) is None
    if is_new:
        usage = private_run_usage(db, principal.plan, ws.id)
        if usage["at_cap"]:
            raise HTTPException(402, detail={
                "error": "cap_reached", "used": usage["used"], "cap": usage["cap"],
                "can_publish": True,
                "message": (f"Private-run limit reached ({usage['cap']}). Publish an existing "
                            "run publicly to free a slot, or upgrade for unlimited private runs."),
            })
    try:
        return materialize_bundle(db, ws, bundle)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        raise HTTPException(422, f"ingest failed: {e}")
