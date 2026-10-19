"""Metriq benchmark history: shared builder + the server-side refresh.

Metriq (Unitary Foundation) publishes every ``metriq-gym`` benchmark run as a
timestamped JSON file in ``unitaryfoundation/metriq-data`` under CC-BY-4.0.
``build_series`` turns those files into one ``qlprov/corpus-series/1.0``
document per device, with one snapshot per day that a benchmark was run and
only the metrics measured that day — so device histories are real
measurements, never carried-forward values.

The same builder backs ``scripts/fetch_metriq.py`` (which writes
``datasets/metriq/``) and ``refresh_metriq_corpus`` (the Growth API refresh),
so both produce identical content hashes and dedupe against each other.

Device identity is normalised to the machine's maker: Braket-hosted devices
(``arn_aws_braket_..._device_qpu_rigetti_cepheus-1-108q``) and their short
aliases map to ``rigetti/cepheus-1-108q``, and so on.
"""

from __future__ import annotations

import datetime as _dt
import re
import sys
import time
from typing import Callable

import httpx
from sqlalchemy.orm import Session

from ..config import get_settings
from .corpus_sync import add_rows, insert_snapshot, series_rows  # noqa: F401  (insert_snapshot re-exported)

RAW_BASE = "https://raw.githubusercontent.com/unitaryfoundation/metriq-data/main"
TREE_API = "https://api.github.com/repos/unitaryfoundation/metriq-data/git/trees/main?recursive=1"
LICENSE_URL = "https://github.com/unitaryfoundation/metriq-data/blob/main/LICENSE"
LICENSE_REF = "CC-BY-4.0 · metriq.info"

_HASH = re.compile(r"[0-9a-f]{6,}$")
_BRAKET_ARN = re.compile(r"^arn_aws_braket_[a-z0-9-]+_device_qpu_([a-z]+)_(.+)$")
_MAKERS = ("iqm", "ionq", "rigetti")


def _first(results, *keys):
    for k in keys:
        v = results.get(k)
        if isinstance(v, (int, float)):
            return v
    sc = results.get("score")
    if isinstance(sc, dict) and isinstance(sc.get("value"), (int, float)):
        return sc["value"]
    return None


# benchmark name in the filename -> (metric key, extractor)
BENCHMARKS = {
    "eplg": ("eplg", lambda r: _first(r, "eplg_10")),
    "clops": ("clops", lambda r: _first(r, "clops_score", "steady_state_clops")),
    "bseq": ("bseq", lambda r: _first(r, "largest_connected_size")),
    "quantum_fourier_transform": ("qft_accuracy", lambda r: _first(r, "accuracy_score")),
    "linear_ramp_qaoa": ("qaoa_ratio", lambda r: _first(r, "effective_approx_ratio", "approx_ratio")),
}


def parse_path(path: str):
    """``metriq-gym/<ver>/<provider_dir>/<device>/<date>_<time>_<bench>_<hash>.json``."""
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


def device_identity(provider_dir: str, device: str) -> tuple[str, str]:
    """The machine's maker and a stable backend id for a metriq-data folder."""
    d = device.lower()
    m = _BRAKET_ARN.match(d)
    if m:
        d = f"{m.group(1)}_{m.group(2)}"
    for maker in _MAKERS:
        if d.startswith(maker + "_"):
            name = d[len(maker) + 1:]
            # IQM devices keep their iqm_ prefix (the long-standing public URLs).
            return maker, (f"iqm_{name}" if maker == "iqm" else name)
    return provider_dir, device


def _iso(ts: str) -> str:
    dt = _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt.astimezone(_dt.timezone.utc).replace(microsecond=0).isoformat()


def build_series(paths: list[str], fetch_json: Callable[[str], object],
                 deadline: float | None = None) -> tuple[list[dict], bool]:
    """metriq-data file paths -> per-device ``corpus-series`` documents.

    ``deadline`` (a ``time.monotonic()`` value) is only checked between
    devices, so a cut-short pass never emits a device with partial history.
    """
    entries = []
    for p in paths:
        parsed = parse_path(p)
        if parsed and parsed[2] in BENCHMARKS:
            provider_dir, device, bench, path = parsed
            entries.append((*device_identity(provider_dir, device), bench, path))
    entries.sort(key=lambda e: (e[0], e[1], e[3].split("/")[-1]))

    devices: dict[tuple[str, str], dict[str, dict]] = {}
    complete, current = True, None
    for maker, bid, bench, path in entries:
        if (maker, bid) != current:
            if deadline is not None and time.monotonic() > deadline:
                complete = False
                break
            current = (maker, bid)
        metric_key, extract = BENCHMARKS[bench]
        try:
            doc = fetch_json(f"{RAW_BASE}/{path}")
        except Exception as e:  # noqa: BLE001 — one bad file must not sink the build
            print(f"metriq: skip {path}: {e}", file=sys.stderr)
            continue
        rec = doc[0] if isinstance(doc, list) else doc
        value = extract(rec.get("results") or {})
        ts = rec.get("timestamp")
        if value is None or not ts:
            continue
        captured = _iso(ts)
        day = devices.setdefault((maker, bid), {}).setdefault(
            captured[:10], {"captured_at": captured, "metrics": {}, "prov": {}})
        if captured >= day["captured_at"] or metric_key not in day["metrics"]:
            day["metrics"][metric_key] = round(float(value), 8)
            day["prov"][metric_key] = {"metric": metric_key,
                                       "benchmark": rec.get("job_type", bench),
                                       "value": float(value), "timestamp": captured,
                                       "params": rec.get("params") or {},
                                       "source_url": f"{RAW_BASE}/{path}"}
        day["captured_at"] = max(day["captured_at"], captured)
        nq = ((rec.get("platform") or {}).get("device_metadata") or {}).get("num_qubits")
        if nq:
            day["metrics"]["n_qubits"] = nq

    out = []
    for (maker, bid), days in sorted(devices.items()):
        snaps = []
        for _, day in sorted(days.items()):
            dm = dict(day["metrics"])
            dm["source"] = "metriq"
            snaps.append({"captured_at": day["captured_at"], "derived_metrics": dm,
                          "metric_provenance": [day["prov"][k] for k in sorted(day["prov"])]})
        out.append({
            "schema": "qlprov/corpus-series/1.0",
            "provider": maker, "backend_id": bid, "source": "metriq",
            "license_ref": LICENSE_REF, "raw_ref": LICENSE_URL,
            "redistributable_raw": True,
            "note": "Metriq metriq-gym community benchmark runs (CC-BY-4.0); one snapshot "
                    "per day a benchmark ran, holding only that day's measurements.",
            "snapshots": snaps,
        })
    return out, complete


def insert_series(session: Session, series: dict, origin: str = "collector:metriq") -> tuple[int, int]:
    """Add one device's series; same-day collector rows it supersedes are replaced."""
    rows = series_rows(series, origin)
    inserted = add_rows(session, rows, replace_same_day=True)
    return inserted, len(rows) - inserted


def insert_corpus_record(session: Session, rec: dict) -> bool:
    """Upsert a single ``qlprov/corpus-record/1.0`` dict."""
    from .corpus_sync import record_rows

    return any([insert_snapshot(session, **row) for row in record_rows(rec)])


def refresh_metriq_corpus(session: Session, deadline_s: float = 45.0) -> dict:
    """Fetch the metriq-data history and upsert; bounded by a soft deadline.

    Returns {"complete", "fetched", "inserted", "deduped", "by_provider"}.
    Safe to call repeatedly: additive + content-hash dedup.
    """
    deadline = time.monotonic() + deadline_s
    settings = get_settings()
    headers = {"User-Agent": "provenova-growth/1.0", "Accept": "application/json"}
    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"

    with httpx.Client(timeout=20.0, headers=headers) as client:
        tree = client.get(TREE_API).raise_for_status().json()
        paths = [o.get("path", "") for o in tree.get("tree", [])]
        series, complete = build_series(
            paths, lambda url: client.get(url).raise_for_status().json(), deadline=deadline)

    inserted = deduped = 0
    by_provider: dict[str, int] = {}
    for s in series:
        i, d = insert_series(session, s)
        inserted += i
        deduped += d
        if i:
            by_provider[s["provider"]] = by_provider.get(s["provider"], 0) + i
    session.commit()
    return {"complete": complete, "fetched": len(series), "inserted": inserted,
            "deduped": deduped, "by_provider": by_provider}
