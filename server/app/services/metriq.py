"""Server-side Metriq corpus refresh (in-memory port of scripts/fetch_metriq.py).

Fetches the latest openly-licensed (CC-BY-4.0) metriq-gym benchmark records
from the ``unitaryfoundation/metriq-data`` GitHub repo and ADDITIVELY upserts
them into ``corpus_snapshots`` — content-hash dedup means unchanged devices
are no-ops, so the endpoint is safely re-runnable. Nothing touches the
filesystem (Fly disks are ephemeral).

``insert_snapshot`` is the single corpus upsert path, shared with
``scripts/seed_real.py``. The record→snapshot conversion and the content-hash
formula are IDENTICAL to the seed path, so server refreshes dedupe against
rows seeded from ``datasets/metriq/*.json``.
"""

from __future__ import annotations

import datetime as _dt
import re
import sys
import time

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from quantumledger_core import hashing
from quantumledger_core.models import CorpusSnapshot

from ..config import get_settings

RAW_BASE = "https://raw.githubusercontent.com/unitaryfoundation/metriq-data/main"
TREE_API = "https://api.github.com/repos/unitaryfoundation/metriq-data/git/trees/main?recursive=1"
LICENSE_URL = "https://github.com/unitaryfoundation/metriq-data/blob/main/LICENSE"
LICENSE_REF = "CC-BY-4.0 · metriq.info"

_HASH = re.compile(r"[0-9a-f]{6,}$")


# --- shared corpus upsert (also imported by scripts/seed_real.py) -----------

def insert_snapshot(session: Session, *, provider, backend_id, captured_at, content_hash,
                    snapshot_json, derived_metrics, license_ref, redistributable_raw,
                    raw_ref=None) -> bool:
    """Insert one corpus snapshot unless (provider, backend, hash) exists."""
    exists = session.scalar(select(CorpusSnapshot).where(
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


def insert_corpus_record(session: Session, rec: dict) -> bool:
    """Upsert a ``qlprov/corpus-record/1.0`` dict (same formula as seed_real)."""
    provider, bid = rec["provider"], rec["backend_id"]
    source = rec.get("source", "metriq")
    dm = dict(rec.get("derived_metrics") or {})
    dm.setdefault("source", source)
    snapshot_json = rec.get("snapshot_json") or {}
    content_hash = hashing.calibration_hash(
        {"provider": provider, "backend_id": bid, "captured_at": rec["captured_at"],
         "source": source, "metrics": dm})
    return insert_snapshot(
        session, provider=provider, backend_id=bid,
        captured_at=_dt.datetime.fromisoformat(rec["captured_at"]),
        content_hash=content_hash, snapshot_json=snapshot_json, derived_metrics=dm,
        license_ref=rec["license_ref"],
        redistributable_raw=bool(rec.get("redistributable_raw", False)),
        raw_ref=rec.get("raw_ref"))


# --- metriq-data fetch (identical extraction to scripts/fetch_metriq.py) ----

def _first(results, *keys):
    for k in keys:
        v = results.get(k)
        if isinstance(v, (int, float)):
            return v
    sc = results.get("score")
    if isinstance(sc, dict) and isinstance(sc.get("value"), (int, float)):
        return sc["value"]
    return None


BENCHMARKS = {
    "eplg": ("eplg", lambda r: _first(r, "eplg_10")),
    "clops": ("clops", lambda r: _first(r, "clops_score", "steady_state_clops")),
    "bseq": ("bseq", lambda r: _first(r, "largest_connected_size")),
    "quantum_fourier_transform": ("qft_accuracy", lambda r: _first(r, "accuracy_score")),
    "linear_ramp_qaoa": ("qaoa_ratio", lambda r: _first(r, "effective_approx_ratio", "approx_ratio")),
}


def _parse_path(path: str):
    parts = path.split("/")
    if len(parts) < 5 or parts[0] != "metriq-gym" or not path.endswith(".json"):
        return None
    provider_dir, device = parts[2], parts[3]
    toks = parts[-1][:-5].split("_")
    if len(toks) < 3:
        return None
    mid = toks[2:]
    if _HASH.fullmatch(mid[-1]):
        mid = mid[:-1]
    return provider_dir, device, "_".join(mid), path


def _real_vendor(provider_dir: str, device: str) -> tuple[str, str]:
    d = device.lower()
    if "iqm" in d:
        bid = "iqm_garnet" if "garnet" in d else ("iqm_emerald" if "emerald" in d else device)
        return "iqm", bid
    return provider_dir, device


def _build_records(client: httpx.Client, latest: dict, deadline: float) -> tuple[list[dict], bool]:
    """Download benchmark files and merge per-device records; deadline-bounded.

    The deadline is only checked at DEVICE boundaries (keys are sorted, so a
    device's benchmarks are contiguous) — so we never emit a device with a
    partial metric set that could shadow the complete row on a later refresh.
    """
    devices: dict[tuple[str, str], dict] = {}
    complete = True
    current_device: tuple[str, str] | None = None
    for (provider_dir, device, bench), path in sorted(latest.items()):
        if (provider_dir, device) != current_device:
            if time.monotonic() > deadline:
                complete = False
                break
            current_device = (provider_dir, device)
        metric_key, extract = BENCHMARKS[bench]
        try:
            doc = client.get(f"{RAW_BASE}/{path}").raise_for_status().json()
        except Exception as e:  # noqa: BLE001 — one bad file must not sink the refresh
            print(f"metriq refresh: skip {path}: {e}", file=sys.stderr)
            continue
        rec = doc[0] if isinstance(doc, list) else doc
        value = extract(rec.get("results", {}))
        if value is None:
            continue
        vendor, bid = _real_vendor(provider_dir, device)
        nq = rec.get("platform", {}).get("device_metadata", {}).get("num_qubits")
        ts = rec.get("timestamp", "")
        d = devices.setdefault((vendor, bid), {"metrics": {}, "prov": [], "ts": "", "nq": None})
        d["metrics"][metric_key] = round(float(value), 8)
        d["prov"].append({"metric": metric_key, "benchmark": rec.get("job_type", bench),
                          "value": float(value), "timestamp": ts,
                          "source_url": f"{RAW_BASE}/{path}"})
        if ts > d["ts"]:
            d["ts"] = ts
        if nq and not d["nq"]:
            d["nq"] = nq

    records = []
    for (vendor, bid), d in sorted(devices.items()):
        dm = dict(d["metrics"])
        if d["nq"]:
            dm["n_qubits"] = d["nq"]
        dm["source"] = "metriq"
        captured = d["ts"] or "2026-01-01T00:00:00"
        if "+" not in captured and "Z" not in captured:
            captured += "+00:00"
        records.append({
            "schema": "qlprov/corpus-record/1.0",
            "provider": vendor, "backend_id": bid, "captured_at": captured,
            "source": "metriq", "license_ref": LICENSE_REF, "raw_ref": LICENSE_URL,
            "redistributable_raw": True,
            "derived_metrics": dm,
            "snapshot_json": {
                "schema": "qlprov/corpus-record/1.0",
                "backend": {"vendor": vendor, "name": bid, "n_qubits": d["nq"]},
                "captured_at": captured, "source": "metriq",
                "provenance": {"source": "metriq", "license_ref": LICENSE_REF,
                               "repo": "unitaryfoundation/metriq-data"},
                "metric_provenance": d["prov"],
            },
        })
    return records, complete


def refresh_metriq_corpus(session: Session, deadline_s: float = 45.0) -> dict:
    """Fetch latest metriq records and upsert; bounded by a soft deadline.

    Returns {"complete", "fetched", "inserted", "deduped", "by_provider"}.
    Safe to call repeatedly: additive + content-hash dedup (re-running after an
    incomplete pass picks up where dedup left off).
    """
    deadline = time.monotonic() + deadline_s
    settings = get_settings()
    headers = {"User-Agent": "quantumledger-growth/1.0", "Accept": "application/json"}
    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"

    with httpx.Client(timeout=20.0, headers=headers) as client:
        tree = client.get(TREE_API).raise_for_status().json()
        entries = [e for e in (_parse_path(o.get("path", "")) for o in tree.get("tree", []))
                   if e and e[2] in BENCHMARKS]
        # newest file per (provider_dir, device, benchmark) — timestamps prefix filenames
        latest: dict[tuple[str, str, str], str] = {}
        for provider_dir, device, bench, path in entries:
            key = (provider_dir, device, bench)
            if key not in latest or path.split("/")[-1] > latest[key].split("/")[-1]:
                latest[key] = path
        records, complete = _build_records(client, latest, deadline)

    inserted = deduped = 0
    by_provider: dict[str, int] = {}
    for rec in records:
        if insert_corpus_record(session, rec):
            inserted += 1
            by_provider[rec["provider"]] = by_provider.get(rec["provider"], 0) + 1
        else:
            deduped += 1
    session.commit()
    return {"complete": complete, "fetched": len(records), "inserted": inserted,
            "deduped": deduped, "by_provider": by_provider}
