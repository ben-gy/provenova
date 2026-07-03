"""Provenova core: provenance data model, canonical hashing, open schemas."""

from __future__ import annotations

import json
from importlib import resources

from . import content_address, hashing
from .db import init_db, make_engine, session_factory
from .models import Base

__version__ = "0.1.0"


def load_schema(name: str) -> dict:
    """Load a published ``qlprov`` JSON Schema by file stem.

    e.g. ``load_schema("run_1_0")`` or ``load_schema("calibration_1_0")``.
    """
    pkg = resources.files("quantumledger_core.schemas.qlprov")
    return json.loads((pkg / f"{name}.json").read_text(encoding="utf-8"))


def verify_run_hash(doc: dict) -> bool:
    """Recompute the Merkle inputs-root + run hash from a provenance document.

    ``run_hash`` is a portable content identity (independent of ledger position),
    so this verifies offline with only the document itself.
    """
    leaves = doc["merkle"]["leaves"]
    labeled = [hashing.sha256_hex({k: leaves[k]}) for k in sorted(leaves)]
    inputs_root = hashing.merkle_root(labeled)
    if inputs_root != doc["merkle"]["inputs_root"]:
        return False
    expected = hashing.sha256_hex({"inputs_root": inputs_root})
    return expected == doc["run_hash"]


def verify_chain(session, workspace_id: str) -> dict:
    """Recompute a workspace's append-only ledger; report the first broken link."""
    from sqlalchemy import select

    from .models import Run

    runs = session.scalars(
        select(Run)
        .where(Run.workspace_id == workspace_id, Run.chain_hash.is_not(None))
        .order_by(Run.created_at, Run.id)
    ).all()
    prev = None
    for r in runs:
        expected = hashing.compute_chain_hash(prev, r.run_hash)
        if r.prev_chain_hash != prev or r.chain_hash != expected:
            return {"ok": False, "broken_at": r.id, "count": len(runs)}
        prev = r.chain_hash
    return {"ok": True, "broken_at": None, "count": len(runs)}


__all__ = [
    "__version__",
    "hashing",
    "content_address",
    "Base",
    "init_db",
    "make_engine",
    "session_factory",
    "load_schema",
    "verify_run_hash",
    "verify_chain",
]
