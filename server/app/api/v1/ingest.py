"""Ingestion API — receives pushed run bundles from the SDK."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from quantumledger_core.models import Workspace

from ...db import get_db
from ...deps import Principal, require_principal
from ...services.ingest import materialize_bundle

router = APIRouter(prefix="/api/v1/ingest", tags=["ingest"])


@router.post("/runs")
def ingest_run(bundle: dict, db: Session = Depends(get_db),
               principal: Principal = Depends(require_principal)):
    if "provenance" not in bundle or "run_hash" not in bundle.get("provenance", {}):
        raise HTTPException(422, "missing provenance.run_hash")
    # Bind strictly to the caller's own (org-validated) workspace — never fall
    # back to a shared global workspace, which would mix tenants' runs.
    ws = db.get(Workspace, principal.workspace_id) if principal.workspace_id else None
    if ws is None:
        raise HTTPException(400, "no target workspace for this account")
    try:
        return materialize_bundle(db, ws, bundle)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        raise HTTPException(422, f"ingest failed: {e}")
