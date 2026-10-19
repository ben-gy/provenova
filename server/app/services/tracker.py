"""The quantum progress tracker: plain-language trends, every number sourced.

Three evidence streams, each kept separate and labelled:

* **Published experiments** — the "Awesome Quantum Computing Experiments"
  database (MIT licence, vendored under ``datasets/experiments``): record lab
  results by year and hardware platform, each linked to its paper.
* **Device calibration** — IBM's public cloud fleet from the corpus (Qiskit's
  fake backends, Apache-2.0), one point per dated calibration snapshot.
* **Context** — hand-curated, cited facts (resource estimates, DARPA's
  programme) in ``datasets/context/tracker.json``.

Everything here is computed from those files and the corpus at request time —
no number on the tracker is typed into a template.
"""

from __future__ import annotations

import csv
import datetime as _dt
import io
import json
import re
import statistics
from functools import lru_cache
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from provenova_core.models import CollectorState, CorpusSnapshot, PublicDataset

# Display name, chart colour — one palette for every chart on the page.
PLATFORMS = {
    "Superconducting": "#4f46e5",
    "Trapped ions": "#059669",
    "Neutral atoms": "#d97706",
    "Semiconductor spins": "#e11d48",
    "Photonic": "#0284c7",
    "NV centres": "#7c3aed",
    "NMR": "#64748b",
}
_PLATFORM_ALIASES = {
    "superconducting circuit": "Superconducting",
    "superconducting circuits": "Superconducting",
    "ion traps": "Trapped ions",
    "neutral atoms": "Neutral atoms",
    "semiconductor spins": "Semiconductor spins",
    "photons": "Photonic",
    "nv centers": "NV centres",
    "nmr": "NMR",
}
# The surface code tolerates physical error rates up to roughly 1%; below it,
# adding qubits to a logical qubit makes it better rather than worse.
THRESHOLD = 0.01


def platform(name: str) -> str:
    return _PLATFORM_ALIASES.get((name or "").strip().lower(), (name or "Other").strip())


def one_in(p: float) -> str:
    """0.0021 -> 'about 1 in 480' — the plain-English reading of an error rate."""
    if not p or p <= 0:
        return "—"
    n = 1 / p
    if n < 10:
        return f"about 1 in {n:.1f}".replace(".0", "")
    digits = 2 if n < 1000 else 3
    rounded = int(float(f"{n:.{digits}g}"))
    return f"about 1 in {rounded:,}"


def month_year(iso: str) -> str:
    """'2026-10-07' -> 'October 2026'."""
    try:
        return _dt.date.fromisoformat(str(iso)[:10]).strftime("%B %Y")
    except ValueError:
        return str(iso)


def _num(s: str) -> float | None:
    try:
        return float(str(s).strip())
    except (TypeError, ValueError):
        return None


# --- published experiments -------------------------------------------------

# Awesome Quantum Computing Experiments (MIT): vendored under datasets/experiments
# by scripts/fetch_experiments.py; newer upstream versions arrive between deploys
# through the "experiments" collector (stored as a PublicDataset).
EXPERIMENTS_REPO = "francois-marie/awesome-quantum-computing-experiments"
EXPERIMENTS_FILES = ["entangled_state_error_exp.csv", "qubit_count.csv", "physical_qubits.csv",
                     "qec_exp.csv", "research_groups.csv"]
EXPERIMENTS_COLUMNS = {
    "entangled_state_error_exp.csv": {"Entangled State Error", "Year", "Platform", "Link"},
    "qubit_count.csv": {"Number of qubits", "Year", "Platform", "Link"},
    "qec_exp.csv": {"Code Parameters", "Year", "Platform", "Link"},
}


def _rows(text: str) -> list[dict]:
    return [{k.strip(): (v or "").strip() for k, v in r.items() if k}
            for r in csv.DictReader(io.StringIO(text))]


def _point(r: dict, value: float) -> dict:
    return {"year": int(r["Year"]), "value": value, "platform": platform(r.get("Platform", "")),
            "title": r.get("Article Title", ""), "author": r.get("First Author", ""),
            "url": r.get("Link", ""), "note": r.get("Notes", "")}


def _frontier(points: list[dict], lower_is_better: bool) -> dict[str, list[dict]]:
    """Per platform, the running record (each point that beat every earlier one)."""
    out: dict[str, list[dict]] = {}
    for p in sorted(points, key=lambda p: (p["year"], p["value"] if lower_is_better else -p["value"])):
        best = out.setdefault(p["platform"], [])
        if not best or (p["value"] < best[-1]["value"] if lower_is_better else p["value"] > best[-1]["value"]):
            best.append(p)
    return out


_DISTANCE = re.compile(r"\[\[?\s*\d+\s*,\s*\d+\s*,\s*(\d+)\s*\]\]?")


def parse_experiments(files: dict[str, str], source: dict) -> dict:
    """Chart points from the dataset's CSV texts (keyed by file name)."""
    gate = [_point(r, v) for r in _rows(files["entangled_state_error_exp.csv"])
            if (v := _num(r.get("Entangled State Error"))) and r.get("Year", "").isdigit()]
    qubits = [_point(r, v) for r in _rows(files["qubit_count.csv"])
              if (v := _num(r.get("Number of qubits"))) and r.get("Year", "").isdigit()]
    qec = []
    for r in _rows(files["qec_exp.csv"]):
        dists = [int(x) for x in _DISTANCE.findall(r.get("Code Parameters", ""))]
        if not dists or not r.get("Year", "").isdigit():
            continue
        p = _point(r, max(dists))
        p["code"] = r.get("Code Name", "")
        # Only when the note credits this row's own code family ("Repetition codes
        # below threshold" on a surface-code row is about a side experiment).
        note, family = r.get("Notes", "").lower(), p["code"].lower().split(" ")[0]
        p["below_threshold"] = "below threshold" in note and family in note
        qec.append(p)
    return {"source": source, "gate_error": gate, "qubit_count": qubits, "qec": qec}


@lru_cache(maxsize=4)
def _bundled_experiments(directory: str, _stamp: float) -> dict:
    d = Path(directory)
    files = {name: (d / name).read_text(encoding="utf-8") for name in EXPERIMENTS_COLUMNS}
    return parse_experiments(files, json.loads((d / "SOURCE.json").read_text()))


# Parsed collected versions, by PublicDataset id (ULIDs are unique, so this is
# safe across databases); the table only grows when upstream publishes.
_collected: dict[str, dict] = {}


def _aware(dt: _dt.datetime) -> _dt.datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=_dt.timezone.utc)


def experiments(datasets: Path, db: Session | None = None) -> dict | None:
    """The newest version available: a collected one if it postdates the bundle."""
    d = Path(datasets) / "experiments"
    bundled = None
    if (d / "SOURCE.json").exists():
        stamp = max(f.stat().st_mtime for f in d.iterdir())
        bundled = _bundled_experiments(str(d), stamp)
    if db is None:
        return bundled
    latest = db.execute(
        select(PublicDataset.id, PublicDataset.version_date)
        .where(PublicDataset.name == "experiments")
        .order_by(PublicDataset.version_date.desc()).limit(1)).first()
    if latest is None or latest.version_date is None:
        return bundled
    bundled_date = (bundled or {}).get("source", {}).get("commit_date")
    if bundled_date and _aware(latest.version_date) <= _aware(
            _dt.datetime.fromisoformat(bundled_date.replace("Z", "+00:00"))):
        return bundled
    if latest.id not in _collected:
        ds = db.get(PublicDataset, latest.id)
        _collected[latest.id] = parse_experiments(ds.files, ds.meta)
    return _collected[latest.id]


def context(datasets: Path) -> dict:
    f = Path(datasets) / "context" / "tracker.json"
    return json.loads(f.read_text()) if f.exists() else {}


# --- IBM fleet (corpus) ----------------------------------------------------

def ibm_fleet(db: Session) -> dict:
    """Every dated IBM calibration snapshot, plus a per-year fleet median.

    The yearly median takes each device's latest snapshot in that year, so a
    device that was re-captured often doesn't outweigh the rest of the fleet.
    """
    points = []
    for row in db.scalars(select(CorpusSnapshot).where(CorpusSnapshot.provider == "ibm")):
        dm = row.derived_metrics or {}
        if dm.get("source") != "ibm" or row.captured_at is None:
            continue
        points.append({"date": row.captured_at.date().isoformat(), "backend": row.backend_id,
                       "median_2q_error": dm.get("median_2q_error"),
                       "n_qubits": dm.get("n_qubits"),
                       "median_t1_us": dm.get("median_t1_us")})
    points.sort(key=lambda p: (p["date"], p["backend"]))

    per_device_year: dict[tuple[str, int], dict] = {}
    for p in points:
        per_device_year[(p["backend"], int(p["date"][:4]))] = p  # latest wins (sorted)
    years: dict[int, list[dict]] = {}
    for (_, year), p in per_device_year.items():
        years.setdefault(year, []).append(p)
    yearly = []
    for year in sorted(years):
        errs = [p["median_2q_error"] for p in years[year] if p["median_2q_error"]]
        if not errs:
            continue
        # Plot the year at the middle of its snapshots, never past the latest one.
        dates = sorted(p["date"] for p in years[year])
        yearly.append({"year": year, "date": dates[len(dates) // 2],
                       "median_2q_error": statistics.median(errs),
                       "best_device_error": min(errs), "devices": len(errs),
                       "max_qubits": max(p["n_qubits"] or 0 for p in years[year])})
    return {"points": [p for p in points if p["median_2q_error"]], "yearly": yearly,
            "n_devices": len({p["backend"] for p in points}), "n_snapshots": len(points)}


# --- the page model --------------------------------------------------------

def _fmt_factor(x: float) -> str:
    return f"{int(float(f'{x:.2g}')):,}" if x >= 10 else f"{x:.1f}"


def build(db: Session, datasets: Path) -> dict:
    exp = experiments(datasets, db)
    ctx = context(datasets)
    fleet = ibm_fleet(db)
    headlines = []
    charts: dict = {}

    if exp:
        gate = exp["gate_error"]
        frontier = _frontier(gate, lower_is_better=True)
        first = min(gate, key=lambda p: (p["year"], -p["value"]))
        best = min(gate, key=lambda p: (p["value"], p["year"]))
        headlines.append({
            "key": "gate_error",
            "kicker": "Errors are falling fast",
            "value": f"{_fmt_factor(first['value'] / best['value'])}×",
            "label": "lower two-qubit error than the first experiments",
            "detail": (f"In {first['year']}, {one_in(first['value'])} entangling operations failed. "
                       f"The best result so far ({best['platform'].lower()}, {best['year']}) fails "
                       f"{one_in(best['value'])}."),
            "evidence": "Published experiments",
            "source_url": best["url"],
        })
        charts["gate_error"] = {"points": gate, "frontier": frontier, "threshold": THRESHOLD}

        qec = exp["qec"]
        surface = sorted((p for p in qec if p["below_threshold"] and "surface" in p["code"].lower()),
                         key=lambda p: p["year"])
        charts["qec"] = {"points": qec}
        if surface:
            s = surface[0]
            headlines.append({
                "key": "qec",
                "kicker": "Error correction has started to work",
                "value": str(s["year"]),
                "label": "first surface code that improved as it grew",
                "detail": (f"{s['author']} et al. ran a distance-{int(s['value'])} surface code "
                           f"on {s['platform'].lower()} hardware below the error-correction "
                           "threshold: adding qubits made the protected (logical) qubit better, "
                           "not worse."),
                "evidence": "Published experiments",
                "source_url": s["url"],
            })

        qb = exp["qubit_count"]
        charts["qubit_count"] = {"points": qb, "frontier": _frontier(qb, lower_is_better=False)}

    if fleet["yearly"]:
        y0, y1 = fleet["yearly"][0], fleet["yearly"][-1]
        headlines.append({
            "key": "ibm",
            "kicker": "Cloud machines are improving too",
            "value": f"{_fmt_factor(y0['median_2q_error'] / y1['median_2q_error'])}×",
            "label": f"lower typical two-qubit error on IBM's cloud fleet, {y0['year']}–{y1['year']}",
            "detail": (f"A typical IBM device's two-qubit gate failed {one_in(y0['median_2q_error'])} "
                       f"times in {y0['year']} and {one_in(y1['median_2q_error'])} in {y1['year']} "
                       f"(median across {y1['devices']} devices)."),
            "evidence": "Device calibration",
            "source_url": "/hardware",
        })
        charts["ibm_fleet"] = fleet

    rsa = ctx.get("rsa_2048_estimates") or []
    if rsa and fleet["yearly"]:
        latest = rsa[-1]
        biggest_cloud = max(p["n_qubits"] or 0 for p in fleet["points"])
        millions = latest["physical_qubits"] / 1e6
        headlines.append({
            "key": "gap",
            "kicker": "Still far from breaking encryption",
            "value": f"{'<' if latest['bound'] == 'fewer than' else '~'}{millions:g}M",
            "label": f"qubits needed to break RSA-2048 ({latest['year']} estimate). "
                     f"The largest IBM chip in this record has {biggest_cloud}.",
            "detail": (f"That estimate assumes every qubit fails at most "
                       f"{one_in(latest['assumed_gate_error'])} operations, and the earlier "
                       f"{rsa[0]['year']} estimate needed {rsa[0]['physical_qubits']:,}. Better "
                       "algorithms are closing the gap from one side, bigger and cleaner "
                       "machines from the other."),
            "evidence": "Resource estimate",
            "source_url": latest["url"],
        })
        charts["gap"] = {"estimates": rsa, "largest_cloud": biggest_cloud,
                         "largest_array": max(exp["qubit_count"], key=lambda p: p["value"]) if exp else None}

    # The scheduled collectors' last runs, so readers can see the data is live.
    pipeline = [{"name": c.name, "last_status": c.last_status,
                 "last_ok_at": c.last_ok_at.isoformat() if c.last_ok_at else None}
                for c in db.scalars(select(CollectorState).order_by(CollectorState.name))]
    checks = [c["last_ok_at"] for c in pipeline if c["last_ok_at"]]

    return {
        "as_of": _dt.date.today().isoformat(),
        "pipeline": pipeline,
        "last_checked": max(checks) if checks else None,
        "headlines": headlines,
        "charts": charts,
        "platforms": PLATFORMS,
        "context": ctx,
        "experiments_source": exp["source"] if exp else None,
        "fleet": {"n_devices": fleet["n_devices"], "n_snapshots": fleet["n_snapshots"],
                  "first": fleet["points"][0]["date"] if fleet["points"] else None,
                  "last": fleet["points"][-1]["date"] if fleet["points"] else None},
    }
