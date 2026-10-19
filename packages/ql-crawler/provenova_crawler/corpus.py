"""Corpus ingestion + analytics query helpers.

``ingest_snapshot`` is the heart of the crawler: normalize a vendor-native
reading, content-hash it, dedup by (provider, backend_id, content_hash), compute
derived aggregate metrics, apply the ToS gate, and persist a
:class:`~provenova_core.models.CorpusSnapshot`. Because dedup is on the
canonical content hash, the corpus only grows when a device's calibration
actually changes — yielding a compact longitudinal time-series.
"""

from __future__ import annotations

import datetime as _dt

from provenova_core import hashing
from provenova_core.models import CorpusSnapshot
from sqlalchemy import select

from . import compliance
from .normalize import to_snapshot

# Leaderboard metric registry. ``higher`` marks higher-is-better ranking.
# Calibration metrics come from raw snapshots; the benchmark metrics
# (clops/eplg/algorithmic_qubits/two_q_fidelity/quantum_volume) come from
# cross-vendor sources (Metriq, vendor-reported) carried in derived_metrics.
LEADERBOARD_METRICS: list[dict] = [
    {"key": "best_2q_fidelity", "label": "Best 2Q fidelity", "higher": True, "unit": ""},
    {"key": "two_q_fidelity", "label": "2Q gate fidelity", "higher": True, "unit": ""},
    {"key": "median_2q_error", "label": "Median 2Q error", "higher": False, "unit": ""},
    {"key": "median_t1_us", "label": "Median T1 (µs)", "higher": True, "unit": "µs"},
    {"key": "median_t2_us", "label": "Median T2 (µs)", "higher": True, "unit": "µs"},
    {"key": "median_readout_error", "label": "Median readout error", "higher": False, "unit": ""},
    {"key": "algorithmic_qubits", "label": "Algorithmic qubits (#AQ)", "higher": True, "unit": ""},
    {"key": "quantum_volume", "label": "Quantum Volume", "higher": True, "unit": ""},
    {"key": "clops", "label": "CLOPS", "higher": True, "unit": ""},
    {"key": "eplg", "label": "EPLG (error/layer)", "higher": False, "unit": ""},
    {"key": "bseq", "label": "Bell-state effective qubits", "higher": True, "unit": ""},
    {"key": "qft_accuracy", "label": "QFT accuracy", "higher": True, "unit": ""},
    {"key": "qaoa_ratio", "label": "QAOA approx. ratio", "higher": True, "unit": ""},
    {"key": "n_qubits", "label": "Qubits", "higher": True, "unit": ""},
]
HIGHER_IS_BETTER = {m["key"] for m in LEADERBOARD_METRICS if m["higher"]}


def _parse_dt(value) -> _dt.datetime | None:
    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        return value
    s = str(value)
    # Accept trailing 'Z' (RFC 3339) which fromisoformat rejects pre-3.11.
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return _dt.datetime.fromisoformat(s)
    except ValueError:
        return None


def _derived_metrics(payload: dict) -> dict:
    """Aggregate metrics powering the leaderboard, taken from the normalized payload."""
    fleet = dict(payload.get("fleet_metrics") or {})
    # fleet_metrics already carries median_t1_us/median_t2_us/median_2q_error/
    # best_2q_fidelity from normalization; surface them under stable keys.
    out = {
        "median_t1_us": fleet.get("median_t1_us"),
        "median_t2_us": fleet.get("median_t2_us"),
        "median_2q_error": fleet.get("median_2q_error"),
        "best_2q_fidelity": fleet.get("best_2q_fidelity"),
        "n_qubits": payload.get("backend", {}).get("n_qubits"),
        "n_gaps": len(payload.get("gaps") or []),
    }
    return out


def ingest_snapshot(
    session,
    provider: str,
    backend_id: str,
    raw: dict,
    meta: dict,
) -> CorpusSnapshot | None:
    """Normalize -> hash -> dedup -> gate -> persist one reading.

    Returns the newly-created :class:`CorpusSnapshot`, or ``None`` if an
    identical (provider, backend_id, content_hash) row already exists.
    """
    payload = to_snapshot(provider, raw)
    content_hash = hashing.calibration_hash(payload)

    existing = session.execute(
        select(CorpusSnapshot).where(
            CorpusSnapshot.provider == provider,
            CorpusSnapshot.backend_id == backend_id,
            CorpusSnapshot.content_hash == content_hash,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return None  # unchanged calibration — dedup, do not grow the corpus

    redistributable_raw, license_ref = compliance.gate(provider, payload)

    # If raw redistribution is disallowed, the persisted payload is the
    # aggregate-only projection; otherwise the full normalized snapshot.
    if redistributable_raw:
        snapshot_json = payload
    else:
        snapshot_json = compliance.aggregate_view(payload)

    captured_at = _parse_dt(payload.get("captured_at")) or _dt.datetime.now(_dt.timezone.utc)
    vendor_updated_at = _parse_dt(meta.get("updated_at"))

    row = CorpusSnapshot(
        provider=provider,
        backend_id=backend_id,
        captured_at=captured_at,
        vendor_updated_at=vendor_updated_at,
        content_hash=content_hash,
        snapshot_json=snapshot_json,
        derived_metrics=_derived_metrics(payload),
        raw_ref=meta.get("source_url"),
        license_ref=license_ref,
        redistributable_raw=redistributable_raw,
    )
    session.add(row)
    session.commit()
    return row


def crawl_all(session, source) -> list[CorpusSnapshot]:
    """Ingest every timepoint of every backend a source exposes.

    Returns the list of newly-created snapshots (dedup'd rows are skipped).
    """
    created: list[CorpusSnapshot] = []
    for backend_id in source.list_backends():
        for raw, meta in source.iter_readings(backend_id):
            row = ingest_snapshot(session, source.provider, backend_id, raw, meta)
            if row is not None:
                created.append(row)
    return created


# ---------------------------------------------------------------------------
# Analytics query helpers
# ---------------------------------------------------------------------------

def _iso(dt) -> str | None:
    return dt.isoformat() if dt else None


def merged_devices(session, period: tuple | None = None) -> list[dict]:
    """Every device's latest known value of each metric, with its own date.

    Snapshots can carry partial metric sets (a benchmark run measures one
    thing), so a device's current picture is built oldest → newest: each
    metric takes its most recent measurement, and ``metric_dates`` records
    when that was. Device-level ``captured_at`` / ``source`` / licence come
    from the newest snapshot. ``period`` restricts to ``(start, end)``.
    """
    stmt = select(CorpusSnapshot)
    if period is not None:
        start, end = period
        if start is not None:
            stmt = stmt.where(CorpusSnapshot.captured_at >= start)
        if end is not None:
            stmt = stmt.where(CorpusSnapshot.captured_at <= end)
    rows = session.execute(stmt).scalars().all()

    _MIN = _dt.datetime.min.replace(tzinfo=_dt.timezone.utc)

    def _key(r: CorpusSnapshot):
        dt = r.captured_at
        dt = _MIN if dt is None else (dt if dt.tzinfo else dt.replace(tzinfo=_dt.timezone.utc))
        return (dt, len(r.derived_metrics or {}), r.id or "")

    devices: dict[tuple[str, str], dict] = {}
    for row in sorted(rows, key=_key):
        dm = row.derived_metrics or {}
        d = devices.setdefault((row.provider, row.backend_id), {
            "provider": row.provider, "backend_id": row.backend_id,
            "derived_metrics": {}, "metric_dates": {}, "metric_sources": {},
            "n_snapshots": 0, "first_captured_at": _iso(row.captured_at)})
        source = dm.get("source") or row.provider
        for k, v in dm.items():
            if k == "source" or v is None:
                continue
            d["derived_metrics"][k] = v
            d["metric_dates"][k] = _iso(row.captured_at)
            d["metric_sources"][k] = source
        d["derived_metrics"]["source"] = source
        d.update(captured_at=_iso(row.captured_at), source=source,
                 license_ref=row.license_ref, redistributable_raw=row.redistributable_raw,
                 raw_ref=row.raw_ref)
        d["n_snapshots"] += 1
    return list(devices.values())


def fleet_leaderboard(
    session,
    metric: str = "median_2q_error",
    period: tuple | None = None,
) -> list[dict]:
    """Rank each device by its latest measurement of ``metric`` in ``period``.

    Lower-is-better metrics (errors) rank ascending; fidelity/coherence metrics
    (``best_2q_fidelity``, ``median_t1_us``, ``median_t2_us``) rank descending.
    Devices missing the metric are dropped. ``captured_at`` is the date of the
    ranked measurement itself. Returns a list of ranked dicts.
    """
    higher_is_better = metric in HIGHER_IS_BETTER
    entries = []
    for d in merged_devices(session, period=period):
        value = d["derived_metrics"].get(metric)
        if value is None:
            continue
        entries.append(
            {
                "provider": d["provider"],
                "backend_id": d["backend_id"],
                "metric": metric,
                "value": value,
                "captured_at": d["metric_dates"].get(metric) or d["captured_at"],
                "derived_metrics": d["derived_metrics"],
                "source": d["metric_sources"].get(metric) or d["source"],
                "license_ref": d["license_ref"],
                "redistributable_raw": d["redistributable_raw"],
            }
        )
    entries.sort(key=lambda e: e["value"], reverse=higher_is_better)
    for i, e in enumerate(entries, start=1):
        e["rank"] = i
    return entries


def list_devices(session) -> list[dict]:
    """Current picture of every (provider, backend_id) — public pages.

    One dict per device (see :func:`merged_devices`): provider, backend_id,
    captured_at, derived_metrics, metric_dates, source, license_ref,
    redistributable_raw, n_snapshots. Sorted by (provider, backend_id).
    """
    out = merged_devices(session)
    # Lowercase sort key: pair canonical ordering (compare pages use lowercase
    # URLs) must agree with this ordering for any letter-case of backend ids.
    out.sort(key=lambda d: (d["provider"].lower(), d["backend_id"].lower()))
    return out


# Metric keys that count toward compare-page eligibility (from the registry,
# excluding n_qubits/n_gaps which alone don't make a meaningful comparison).
_COMPARE_METRICS = {m["key"] for m in LEADERBOARD_METRICS if m["key"] not in {"n_qubits"}}
MIN_COMPARE_OVERLAP = 2


def comparable_pairs(session, min_overlap: int = MIN_COMPARE_OVERLAP) -> list[dict]:
    """Canonically-ordered device pairs sharing >= min_overlap ranked metrics.

    Powers the /hardware compare pages and the sitemap; pairs below the
    overlap threshold are never rendered (thin-page guard).
    """
    devices = list_devices(session)
    pairs = []
    for i, a in enumerate(devices):
        a_keys = {k for k, v in (a["derived_metrics"] or {}).items()
                  if k in _COMPARE_METRICS and v is not None}
        for b in devices[i + 1:]:
            b_keys = {k for k, v in (b["derived_metrics"] or {}).items()
                      if k in _COMPARE_METRICS and v is not None}
            shared = sorted(a_keys & b_keys)
            if len(shared) >= min_overlap:
                pairs.append({"a": a, "b": b, "shared_metrics": shared})
    return pairs


def device_timeseries(session, provider: str, backend_id: str) -> list[dict]:
    """Return the ordered longitudinal series for one device.

    One entry per stored (deduplicated) calibration change, oldest first — the
    backbone of drift charts and the "State of Quantum Hardware" trend view.
    """
    rows = session.execute(
        select(CorpusSnapshot)
        .where(
            CorpusSnapshot.provider == provider,
            CorpusSnapshot.backend_id == backend_id,
        )
    ).scalars().all()
    rows.sort(key=lambda r: (r.captured_at or _dt.datetime.min))
    return [
        {
            "captured_at": r.captured_at.isoformat() if r.captured_at else None,
            "vendor_updated_at": r.vendor_updated_at.isoformat() if r.vendor_updated_at else None,
            "content_hash": r.content_hash,
            "derived_metrics": r.derived_metrics,
            "redistributable_raw": r.redistributable_raw,
        }
        for r in rows
    ]
