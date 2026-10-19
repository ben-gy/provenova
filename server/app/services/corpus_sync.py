"""Keep the public corpus in step with the datasets committed to the repo.

The ``datasets/`` directory is the reviewed, versioned source of truth for the
public hardware corpus (IBM calibration history, Metriq benchmarks, vendor
claims). On startup the server reconciles ``corpus_snapshots`` against it, so a
deploy is all it takes to publish a data change — no scripts run against the
production database by hand.

Every row records its ``origin`` in ``snapshot_json``: ``bundle`` for rows
loaded from ``datasets/``, ``collector:<name>`` for rows the scheduled
collectors add between deploys (``app.services.collectors``). Reconciliation
inserts missing bundle rows and deletes bundle rows of a bundled source that
no longer appear in the bundle (superseded identities, corrected dates).
Collector rows and rows from any other source are never deleted, and both
loaders compute the same content hash, so they dedupe against each other.
Snapshots are compact records (aggregates plus a permalink to the raw
source), so a full pass costs well under a second.

Two file shapes are accepted:

* ``qlprov/corpus-series/1.0`` — one device, many dated ``snapshots``.
* ``qlprov/corpus-record/1.0`` — one device, one reading (vendor specs).
"""

from __future__ import annotations

import datetime as _dt
import glob
import json
import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from provenova_core import hashing
from provenova_core.models import CorpusSnapshot

_log = logging.getLogger("provenova.corpus_sync")

BUNDLE_DIRS = ("ibm", "metriq", "vendor_specs", "iqm")


def parse_dt(value: str) -> _dt.datetime:
    dt = _dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=_dt.timezone.utc)


def snapshot_hash(provider: str, backend_id: str, captured_at: str, source: str,
                  metrics: dict) -> str:
    """Content identity of one corpus reading (shared by every loader)."""
    return hashing.calibration_hash({"provider": provider, "backend_id": backend_id,
                                     "captured_at": captured_at, "source": source,
                                     "metrics": metrics})


def insert_snapshot(session: Session, *, provider, backend_id, captured_at, content_hash,
                    snapshot_json, derived_metrics, license_ref, redistributable_raw,
                    raw_ref=None) -> bool:
    """Insert one corpus snapshot unless (provider, backend, hash) exists."""
    exists = session.scalar(select(CorpusSnapshot.id).where(
        CorpusSnapshot.provider == provider, CorpusSnapshot.backend_id == backend_id,
        CorpusSnapshot.content_hash == content_hash))
    if exists:
        return False
    session.add(CorpusSnapshot(
        provider=provider, backend_id=backend_id, captured_at=captured_at,
        content_hash=content_hash, snapshot_json=snapshot_json,
        derived_metrics=derived_metrics, license_ref=license_ref,
        redistributable_raw=redistributable_raw, raw_ref=raw_ref))
    return True


def record_rows(rec: dict, origin: str = "bundle") -> list[dict]:
    """Rows for a ``qlprov/corpus-record/1.0`` (or ``corpus-series/1.0``) document."""
    if rec.get("snapshots") is not None:
        return series_rows(rec, origin)
    provider, bid = rec["provider"], rec["backend_id"]
    source = rec.get("source", "unknown")
    dm = dict(rec.get("derived_metrics") or {})
    dm.setdefault("source", source)
    snapshot_json = dict(rec.get("snapshot_json") or {
        "schema": "qlprov/corpus-record/1.0",
        "backend": {"vendor": provider, "name": bid},
        "captured_at": rec["captured_at"], "source": source,
        "provenance": {"source": source, "license_ref": rec.get("license_ref"),
                       "raw_ref": rec.get("raw_ref"), "note": rec.get("note", "")},
        "metrics": dm,
    })
    snapshot_json["origin"] = origin
    return [{
        "provider": provider, "backend_id": bid,
        "captured_at": parse_dt(rec["captured_at"]),
        "content_hash": snapshot_hash(provider, bid, rec["captured_at"], source, dm),
        "snapshot_json": snapshot_json, "derived_metrics": dm,
        "license_ref": rec["license_ref"],
        "redistributable_raw": bool(rec.get("redistributable_raw", False)),
        "raw_ref": rec.get("raw_ref"),
    }]


def series_rows(series: dict, origin: str = "bundle") -> list[dict]:
    """One row per dated snapshot of a ``qlprov/corpus-series/1.0`` document."""
    provider, bid = series["provider"], series["backend_id"]
    source = series.get("source", "unknown")
    rows = []
    for snap in series.get("snapshots") or []:
        dm = dict(snap.get("derived_metrics") or {})
        dm.setdefault("source", source)
        provenance = {k: v for k, v in snap.items() if k not in ("derived_metrics",)}
        rows.append({
            "provider": provider, "backend_id": bid,
            "captured_at": parse_dt(snap["captured_at"]),
            "content_hash": snapshot_hash(provider, bid, snap["captured_at"], source, dm),
            "snapshot_json": {
                "schema": "qlprov/corpus-record/1.0",
                "backend": {"vendor": provider, "name": bid,
                            "n_qubits": dm.get("n_qubits")},
                "captured_at": snap["captured_at"], "source": source,
                "provenance": {"source": source, "license_ref": series.get("license_ref"),
                               "note": series.get("note", ""), **provenance},
                "origin": origin,
            },
            "derived_metrics": dm,
            "license_ref": series.get("license_ref"),
            "redistributable_raw": bool(series.get("redistributable_raw", False)),
            "raw_ref": snap.get("raw_ref") or series.get("raw_ref"),
        })
    return rows


def load_bundle(datasets_dir: Path) -> list[dict]:
    rows: list[dict] = []
    for sub in BUNDLE_DIRS:
        for fn in sorted(glob.glob(str(Path(datasets_dir) / sub / "*.json"))):
            with open(fn) as fh:
                rows += record_rows(json.load(fh))
    return rows


def _source(dm: dict | None, provider: str) -> str:
    return (dm or {}).get("source") or provider


def _origin(snapshot_json: dict | None) -> str:
    # Rows loaded before origins were recorded all came from the bundle.
    return (snapshot_json or {}).get("origin") or "bundle"


def add_rows(session: Session, rows: list[dict], *, replace_same_day: bool = False) -> int:
    """Insert collector rows that aren't already stored; returns how many were new.

    ``replace_same_day`` is for per-day sources (Metriq): a later collection of
    the same device-day holds more runs, so earlier *collector* rows for that
    day whose metrics it covers are replaced rather than kept as near-duplicates
    (bundle rows are left for the startup sync to manage).
    """
    inserted = 0
    for r in rows:
        if not insert_snapshot(session, **r):
            continue
        inserted += 1
        if replace_same_day:
            day = r["captured_at"].date()
            source = r["derived_metrics"].get("source")
            new_keys = set(r["derived_metrics"])
            for old in session.scalars(select(CorpusSnapshot).where(
                    CorpusSnapshot.provider == r["provider"],
                    CorpusSnapshot.backend_id == r["backend_id"],
                    CorpusSnapshot.content_hash != r["content_hash"])):
                dt = old.captured_at
                if dt is not None and dt.date() == day \
                        and _origin(old.snapshot_json).startswith("collector:") \
                        and _source(old.derived_metrics, old.provider) == source \
                        and set(old.derived_metrics or {}) <= new_keys:
                    session.delete(old)
    session.flush()
    return inserted


def sync_bundled(session: Session, datasets_dir: Path) -> dict:
    """Reconcile ``corpus_snapshots`` with the committed datasets. Idempotent."""
    wanted = load_bundle(datasets_dir)
    owned = {r["derived_metrics"]["source"] for r in wanted} | {"ibm", "metriq", "vendor-reported"}
    want_keys = {(r["provider"], r["backend_id"], r["content_hash"]) for r in wanted}

    have: set[tuple[str, str, str]] = set()
    deleted = 0
    for row in session.scalars(select(CorpusSnapshot)):
        key = (row.provider, row.backend_id, row.content_hash)
        if (_source(row.derived_metrics, row.provider) in owned and key not in want_keys
                and _origin(row.snapshot_json) == "bundle"):
            session.delete(row)
            deleted += 1
        else:
            have.add(key)

    inserted = 0
    for r in wanted:
        key = (r["provider"], r["backend_id"], r["content_hash"])
        if key in have:
            continue
        session.add(CorpusSnapshot(**r))
        have.add(key)
        inserted += 1
    session.commit()
    return {"bundled": len(wanted), "inserted": inserted, "deleted": deleted}


def sync_on_startup(session: Session, datasets_dir: Path) -> None:
    """Best-effort startup hook: a bad dataset file must never stop the server."""
    if not Path(datasets_dir).is_dir():
        return
    try:
        result = sync_bundled(session, datasets_dir)
        _log.info("corpus sync: %s", result)
    except Exception:  # noqa: BLE001
        session.rollback()
        _log.exception("corpus sync failed; serving the existing corpus")
