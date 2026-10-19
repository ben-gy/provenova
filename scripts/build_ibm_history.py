"""Build the IBM fleet calibration history -> datasets/ibm/<backend>.json.

IBM device calibration snapshots ship in Qiskit's open-source fake backends
(Apache-2.0): every ``props_<device>.json`` is a real BackendProperties dump
from the named machine, stamped with the device's own ``last_update_date``.
The files have been refreshed over the years, so their **git history** is a
genuine longitudinal record of IBM's public fleet, from the 5-qubit
``ibmqx`` era to Heron.

This script walks that history in three places (the fake backends moved
twice) and writes one ``qlprov/corpus-series/1.0`` file per device:

* ``Qiskit/qiskit-ibm-runtime``  ``qiskit_ibm_runtime/fake_provider/backends``
* ``Qiskit/qiskit`` @ 0.46.0       ``qiskit/providers/fake_provider/backends``
* ``Qiskit/qiskit`` @ 0.19.0       ``qiskit/test/mock/backends``

Each snapshot keeps only aggregate metrics (median two-qubit error, T1, T2,
readout error, qubit count) plus a permalink to the exact raw file at its
commit and the file's SHA-256 — so every number can be re-derived from the
public source. Snapshots are deduplicated on (device, last_update_date), and
only files that name a real device (``ibm_*`` / ``ibmq_*``) are kept.

Run (needs network; a token raises GitHub's API rate limit):

    GITHUB_TOKEN=... python scripts/build_ibm_history.py
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "datasets" / "ibm"
API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"
LICENSE_REF = "Apache-2.0 · Qiskit fake backend calibration snapshot"

# (repo, directory of per-device folders, ref to list the folders at)
SOURCES = [
    ("Qiskit/qiskit-ibm-runtime", "qiskit_ibm_runtime/fake_provider/backends", "main"),
    ("Qiskit/qiskit", "qiskit/providers/fake_provider/backends", "0.46.0"),
    ("Qiskit/qiskit", "qiskit/test/mock/backends", "0.19.0"),
]
TWO_QUBIT_GATES = {"cx", "ecr", "cz"}


def _get(url: str, *, api: bool = True):
    headers = {"User-Agent": "provenova-dataset-builder"}
    if api:
        headers["Accept"] = "application/vnd.github+json"
        if os.environ.get("GITHUB_TOKEN"):
            headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if attempt == 3:
                raise
        except urllib.error.URLError:
            if attempt == 3:
                raise
        time.sleep(2 ** attempt)
    return None


def _api_json(path: str):
    body = _get(f"{API}/{path}")
    return json.loads(body) if body else None


def _to_us(value: float, unit: str) -> float | None:
    unit = (unit or "").strip().lower().replace("µ", "u")
    scale = {"us": 1.0, "ns": 1e-3, "ms": 1e3, "s": 1e6}.get(unit)
    return value * scale if scale is not None and value is not None else None


def _sig(v: float | None, digits: int = 6) -> float | None:
    return None if v is None else float(f"{v:.{digits}g}")


def _utc_iso(stamp: str) -> str:
    dt = _dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt.astimezone(_dt.timezone.utc).replace(microsecond=0).isoformat()


def derive(props: dict) -> dict:
    """Aggregate metrics from a raw BackendProperties dict."""
    t1, t2, ro = [], [], []
    for q in props.get("qubits") or []:
        for p in q:
            name, val = p.get("name"), p.get("value")
            if not isinstance(val, (int, float)):
                continue
            if name == "T1":
                v = _to_us(val, p.get("unit"))
                if v and v > 0:
                    t1.append(v)
            elif name == "T2":
                v = _to_us(val, p.get("unit"))
                if v and v > 0:
                    t2.append(v)
            elif name == "readout_error" and 0 <= val < 0.5:
                ro.append(val)
    twoq = []
    for g in props.get("gates") or []:
        if len(g.get("qubits") or []) != 2 and g.get("gate") not in TWO_QUBIT_GATES:
            continue
        for p in g.get("parameters") or []:
            v = p.get("value")
            # error 1.0 marks a broken coupler; 0 is a placeholder, not a measurement
            if p.get("name") == "gate_error" and isinstance(v, (int, float)) and 0 < v < 0.5:
                twoq.append(v)
    out = {
        "n_qubits": len(props.get("qubits") or []),
        "median_2q_error": _sig(statistics.median(twoq)) if twoq else None,
        "best_2q_fidelity": _sig(1 - min(twoq)) if twoq else None,
        "median_t1_us": _sig(statistics.median(t1), 5) if t1 else None,
        "median_t2_us": _sig(statistics.median(t2), 5) if t2 else None,
        "median_readout_error": _sig(statistics.median(ro)) if ro else None,
        "source": "ibm",
    }
    return {k: v for k, v in out.items() if v is not None}


def _props_paths(repo: str, base: str, ref: str) -> list[str]:
    listing = _api_json(f"repos/{repo}/contents/{base}?ref={ref}") or []
    paths = []
    for entry in listing:
        if entry.get("type") != "dir" or entry["name"].startswith("_"):
            continue
        inner = _api_json(f"repos/{repo}/contents/{entry['path']}?ref={ref}") or []
        paths += [f["path"] for f in inner
                  if f["name"].startswith("props") and f["name"].endswith(".json")]
    return paths


def _commits(repo: str, path: str, ref: str) -> list[dict]:
    out, page = [], 1
    while True:
        batch = _api_json(f"repos/{repo}/commits?path={path}&sha={ref}&per_page=100&page={page}") or []
        out += batch
        if len(batch) < 100:
            return out
        page += 1


def collect() -> dict[str, dict[str, dict]]:
    """backend_name -> {captured_at -> snapshot}, first source wins on ties."""
    devices: dict[str, dict[str, dict]] = {}
    for repo, base, ref in SOURCES:
        paths = _props_paths(repo, base, ref)
        print(f"{repo}@{ref}: {len(paths)} props files", file=sys.stderr)
        for path in paths:
            for c in _commits(repo, path, ref):
                sha = c["sha"]
                body = _get(f"{RAW}/{repo}/{sha}/{path}", api=False)
                if not body:
                    continue
                try:
                    props = json.loads(body)
                except ValueError:
                    continue
                name = (props.get("backend_name") or "").strip()
                stamp = props.get("last_update_date")
                if not stamp or not (name.startswith("ibm_") or name.startswith("ibmq")):
                    continue  # fake_* names don't identify a real machine
                dm = derive(props)
                if "median_2q_error" not in dm and "median_t1_us" not in dm:
                    continue
                captured = _utc_iso(stamp)
                snaps = devices.setdefault(name, {})
                if captured in snaps:
                    continue
                snaps[captured] = {
                    "captured_at": captured,
                    "derived_metrics": dm,
                    "raw_ref": f"https://github.com/{repo}/blob/{sha}/{path}",
                    "raw_sha256": hashlib.sha256(body).hexdigest(),
                    "commit_date": c["commit"]["committer"]["date"],
                }
    return devices


def main() -> None:
    devices = collect()
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.json"):
        old.unlink()
    for name, snaps in sorted(devices.items()):
        series = sorted(snaps.values(), key=lambda s: s["captured_at"])
        doc = {
            "schema": "qlprov/corpus-series/1.0",
            "provider": "ibm",
            "backend_id": name,
            "source": "ibm",
            "license_ref": LICENSE_REF,
            "redistributable_raw": True,
            "note": "Aggregates of real IBM Quantum calibration snapshots published in Qiskit's "
                    "fake backends (Apache-2.0). raw_ref is the exact source file at its commit.",
            "snapshots": series,
        }
        (OUT / f"{name}.json").write_text(json.dumps(doc, indent=1) + "\n")
        print(f"{name}: {len(series)} snapshots "
              f"({series[0]['captured_at'][:10]} → {series[-1]['captured_at'][:10]})")


if __name__ == "__main__":
    main()
