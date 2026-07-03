"""Public per-device hardware pages + head-to-head comparison pages.

Programmatic SEO surface built on the real corpus: every page is generated
from unique calibration/benchmark data (never boilerplate), device URLs are
lowercase-canonical, and comparison pages 404 below a shared-metric threshold
so no thin/doorway pages are ever rendered.
"""

from __future__ import annotations

import datetime as _dt

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import Principal, current_principal
from .routes import render

router = APIRouter(tags=["hardware"])

STALE_DAYS = 30


def _metric_registry() -> list[dict]:
    from provenova_crawler.corpus import LEADERBOARD_METRICS

    return LEADERBOARD_METRICS


def _devices(db: Session) -> list[dict]:
    from provenova_crawler.corpus import list_devices

    return list_devices(db)


def _find_device(devices: list[dict], provider: str, backend_id: str) -> dict | None:
    for d in devices:
        if d["provider"].lower() == provider.lower() and d["backend_id"].lower() == backend_id.lower():
            return d
    return None


# Display names for providers whose brand casing isn't title-case.
PROVIDER_DISPLAY = {"ibm": "IBM", "iqm": "IQM", "ionq": "IonQ", "aws": "AWS",
                    "origin": "Origin", "quantinuum": "Quantinuum", "rigetti": "Rigetti"}


def provider_display(provider: str) -> str:
    return PROVIDER_DISPLAY.get(provider.lower(), provider.capitalize())


def _fmt(v):
    """Trim float representation noise for display (6 significant digits)."""
    if isinstance(v, float):
        out = float(f"{v:.6g}")
        return int(out) if out.is_integer() else out
    return v


def _display_metrics(dm: dict) -> list[dict]:
    """Registry-labelled metrics present on a snapshot, in registry order."""
    out = []
    for m in _metric_registry():
        v = (dm or {}).get(m["key"])
        if v is not None:
            out.append({**m, "value": _fmt(v)})
    return out


def _is_stale(captured_at: str | None) -> bool:
    if not captured_at:
        return True
    try:
        dt = _dt.datetime.fromisoformat(captured_at)
    except ValueError:
        return True
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return (_dt.datetime.now(_dt.timezone.utc) - dt).days > STALE_DAYS


def _meta_description(d: dict) -> str:
    """Deterministic, data-derived description — unique per device."""
    dm = d["derived_metrics"] or {}
    bits = []
    if dm.get("n_qubits"):
        bits.append(f"{dm['n_qubits']} qubits")
    for m in _display_metrics(dm):
        if m["key"] == "n_qubits":
            continue
        bits.append(f"{m['label']} {m['value']}")
        if len(bits) >= 4:
            break
    when = (d["captured_at"] or "")[:10]
    parts = ", ".join(bits) if bits else "calibration & benchmark provenance"
    return (f"{provider_display(d['provider'])} {d['backend_id']}: {parts}"
            f"{f' (captured {when})' if when else ''}. Trends, provenance and citations "
            f"on Provenova.")[:300]


@router.get("/hardware", response_class=HTMLResponse)
def hardware_index(request: Request, db: Session = Depends(get_db),
                   p: Principal | None = Depends(current_principal)):
    devices = _devices(db)
    by_vendor: dict[str, list[dict]] = {}
    for d in devices:
        by_vendor.setdefault(provider_display(d["provider"]), []).append(
            {**d, "metrics": _display_metrics(d["derived_metrics"])})
    return render(request, "hardware_index.html", p, by_vendor=by_vendor,
                  n_devices=len(devices), canonical_path="/hardware")


@router.get("/hardware/{provider}/{backend_id}", response_class=HTMLResponse)
def hardware_device(provider: str, backend_id: str, request: Request,
                    db: Session = Depends(get_db),
                    p: Principal | None = Depends(current_principal)):
    # Lowercase-canonical URLs.
    if provider != provider.lower() or backend_id != backend_id.lower():
        return RedirectResponse(f"/hardware/{provider.lower()}/{backend_id.lower()}",
                                status_code=301)
    devices = _devices(db)
    d = _find_device(devices, provider, backend_id)
    if d is None:
        raise HTTPException(404, "unknown device")

    from provenova_crawler.corpus import comparable_pairs, device_timeseries

    series = device_timeseries(db, d["provider"], d["backend_id"])
    # Trend lines: registry metrics with >=2 datapoints across the series.
    trends = []
    for m in _metric_registry():
        pts = [(s["captured_at"], (s["derived_metrics"] or {}).get(m["key"]))
               for s in series if (s["derived_metrics"] or {}).get(m["key"]) is not None]
        if len(pts) >= 2:
            trends.append({"key": m["key"], "label": m["label"],
                           "x": [t for t, _ in pts], "y": [v for _, v in pts]})

    # Comparison partners for this device (cross-vendor first, cap 8).
    partners = []
    for pair in comparable_pairs(db):
        for me, other in ((pair["a"], pair["b"]), (pair["b"], pair["a"])):
            if me["provider"] == d["provider"] and me["backend_id"] == d["backend_id"]:
                partners.append({"other": other, "shared": pair["shared_metrics"],
                                 "cross_vendor": other["provider"] != d["provider"],
                                 "pair": pair})
    partners.sort(key=lambda x: (not x["cross_vendor"], x["other"]["provider"],
                                 x["other"]["backend_id"]))
    partners = partners[:8]

    desc = _meta_description(d)
    today = _dt.date.today().isoformat()
    bibtex = (
        f"@misc{{provenova_{d['provider']}_{d['backend_id'].replace('-', '_').replace(' ', '_')},\n"
        f"  title        = {{{provider_display(d['provider'])} {d['backend_id']} — calibration & benchmark history}},\n"
        f"  howpublished = {{\\url{{{{BASE}}/hardware/{d['provider'].lower()}/{d['backend_id'].lower()}}}}},\n"
        f"  publisher    = {{Provenova}},\n"
        f"  note         = {{accessed {today}}}\n"
        f"}}"
    )
    return render(request, "hardware_device.html", p, d=d, pname=provider_display(d["provider"]),
                  metrics=_display_metrics(d["derived_metrics"]),
                  trends=trends, n_snapshots=len(series), partners=partners,
                  meta_description=desc, bibtex=bibtex,
                  stale=_is_stale(d["captured_at"]),
                  canonical_path=f"/hardware/{d['provider'].lower()}/{d['backend_id'].lower()}")


@router.get("/hardware/{pa}/{ba}/vs/{pb}/{bb}", response_class=HTMLResponse)
def hardware_compare(pa: str, ba: str, pb: str, bb: str, request: Request,
                     db: Session = Depends(get_db),
                     p: Principal | None = Depends(current_principal)):
    # Lowercase-canonical.
    lowered = [s.lower() for s in (pa, ba, pb, bb)]
    if [pa, ba, pb, bb] != lowered:
        return RedirectResponse("/hardware/{}/{}/vs/{}/{}".format(*lowered), status_code=301)
    if (pa, ba) == (pb, bb):
        raise HTTPException(404, "cannot compare a device with itself")
    # Canonical pair ordering: (provider, backend_id) ascending; reverse 301s.
    if (pa, ba) > (pb, bb):
        return RedirectResponse(f"/hardware/{pb}/{bb}/vs/{pa}/{ba}", status_code=301)

    from provenova_crawler.corpus import comparable_pairs

    pair = None
    for cp in comparable_pairs(db):
        if (cp["a"]["provider"].lower(), cp["a"]["backend_id"].lower()) == (pa, ba) and \
           (cp["b"]["provider"].lower(), cp["b"]["backend_id"].lower()) == (pb, bb):
            pair = cp
            break
    if pair is None:
        # Unknown device or overlap < threshold — no doorway pages.
        raise HTTPException(404, "no comparable data for this pair")

    a, b = pair["a"], pair["b"]
    registry = {m["key"]: m for m in _metric_registry()}
    rows = []
    for key in pair["shared_metrics"]:
        m = registry.get(key, {"label": key, "higher": True, "unit": ""})
        va, vb = a["derived_metrics"].get(key), b["derived_metrics"].get(key)
        winner = None
        if va is not None and vb is not None and va != vb:
            winner = "a" if ((va > vb) == bool(m.get("higher", True))) else "b"
        rows.append({"key": key, "label": m["label"], "unit": m.get("unit", ""),
                     "higher": bool(m.get("higher", True)), "a": _fmt(va), "b": _fmt(vb),
                     "winner": winner})

    stale = _is_stale(a["captured_at"]) or _is_stale(b["captured_at"])
    desc = (f"{provider_display(a['provider'])} {a['backend_id']} vs {provider_display(b['provider'])} "
            f"{b['backend_id']}: {', '.join(r['label'] for r in rows[:4])} compared side by side "
            f"with source & licence provenance on Provenova.")[:300]
    return render(request, "hardware_compare.html", p, a=a, b=b, rows=rows,
                  pname_a=provider_display(a["provider"]), pname_b=provider_display(b["provider"]),
                  meta_description=desc, noindex=stale,
                  canonical_path=f"/hardware/{pa}/{ba}/vs/{pb}/{bb}")
