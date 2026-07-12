"""Ingestion API — receives pushed run bundles from the SDK."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from provenova_core.models import Run, Workspace
from provenova_core.simulate.safety import UnsafeCircuitError

from ...db import get_db
from ...deps import Principal, require_principal
from ...services.ingest import materialize_bundle
from ...services.limits import private_run_usage

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/ingest", tags=["ingest"])


# The bundle was previously an untyped ``dict``. These models give the envelope
# structural validation and, crucially, bound the size of the fields that get
# parsed/replayed (the circuit source JSON, counts) so a giant payload can't
# exhaust memory before the gate allowlist + qubit caps run. ``extra="allow"``
# keeps the SDK's other bundle fields (compilation, distribution, ...) intact.
class _Provenance(BaseModel):
    model_config = ConfigDict(extra="allow")
    run_hash: str = Field(min_length=1, max_length=256)


class _Circuit(BaseModel):
    model_config = ConfigDict(extra="allow")
    # JSON string reconstructed into a circuit; bounded so json.loads can't be
    # handed a multi-hundred-MB string. The gate allowlist/caps run afterwards.
    source: str = Field(min_length=1, max_length=262_144)  # 256 KiB


class _Backend(BaseModel):
    model_config = ConfigDict(extra="allow")
    vendor: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=200)
    kind: str = Field(default="simulator", max_length=40)


class BundleIn(BaseModel):
    model_config = ConfigDict(extra="allow")
    provenance: _Provenance
    circuit: _Circuit
    backend: _Backend
    calibration: dict
    result: dict
    run: dict | None = None


@router.post("/runs")
def ingest_run(bundle: BundleIn, db: Session = Depends(get_db),
               principal: Principal = Depends(require_principal)):
    run_hash = bundle.provenance.run_hash
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
        return materialize_bundle(db, ws, bundle.model_dump())
    except UnsafeCircuitError as e:
        # Safe to surface: describes the allowlist/caps rule that was violated,
        # not any server internal — helps a legitimate SDK client fix its bundle.
        db.rollback()
        raise HTTPException(422, f"circuit rejected: {e}")
    except Exception:
        db.rollback()
        log.exception("ingest failed for workspace %s", ws.id)
        raise HTTPException(422, "ingest failed")