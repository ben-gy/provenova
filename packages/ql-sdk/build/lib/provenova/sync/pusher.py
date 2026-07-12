"""Resumable, content-hash-idempotent push of local runs to the hosted store."""

from __future__ import annotations

from ..store import LocalLedger
from .client import SyncClient
from .errors import SyncError


class Pusher:
    def __init__(self, ledger: LocalLedger, client: SyncClient):
        self.ledger = ledger
        self.client = client

    def push(self, run_ids: list[str] | None = None, *, dry_run: bool = False) -> dict:
        ids = run_ids or self.ledger.pending_push()
        pushed, existed, failed = [], [], []
        for rid in ids:
            bundle = self.ledger.export_bundle(rid)
            if bundle is None:
                continue
            if dry_run:
                pushed.append(rid)
                continue
            try:
                resp = self.client.ingest(bundle)
                if resp.get("status") == "exists":
                    existed.append(rid)
                else:
                    pushed.append(rid)
            except SyncError as e:
                # Connection / auth / server-level failures affect every run —
                # stop and report once instead of repeating the same error N times.
                if e.transport or (e.status in (401, 403, 404) or (e.status or 0) >= 500):
                    return {"pushed": pushed, "existed": existed, "failed": failed,
                            "dry_run": dry_run, "aborted": True,
                            "error": e.message, "hint": e.hint}
                failed.append({"run_id": rid, "error": e.message, "hint": e.hint})
            except Exception as e:  # noqa: BLE001 — unexpected; keep going but record it
                failed.append({"run_id": rid, "error": str(e), "hint": None})
        return {"pushed": pushed, "existed": existed, "failed": failed, "dry_run": dry_run}
