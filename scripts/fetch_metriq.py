"""Fetch openly-licensed cross-vendor benchmarks from Metriq into datasets/metriq/.

Metriq (Unitary Foundation) retired its live REST API; the real, versioned data
now lives in the git repo ``unitaryfoundation/metriq-data`` under a **CC-BY-4.0**
licence (verified: repo LICENSE is the Creative Commons Attribution 4.0 text).
Records are machine-generated ``metriq-gym`` benchmark runs committed as JSON.

This script (deterministic — no model in the loop, so numbers are exact):

  1. lists the repo tree via the GitHub API,
  2. keeps the LATEST ``metriq-gym`` record per (vendor, device, benchmark) for
     the benchmarks we surface (EPLG, CLOPS, BSEQ, QFT accuracy, QAOA ratio),
  3. downloads each and extracts its headline metric,
  4. MERGES every benchmark for a device into ONE ``qlprov/corpus-record/1.0``
     file (so the leaderboard's latest-per-device snapshot carries all metrics),
  5. writes ``datasets/metriq/<vendor>_<device>.json`` citing each source file.

Every value is copied from the fetched JSON; each metric keeps its own source
URL + timestamp under ``snapshot_json.metric_provenance``.

Usage:
    python scripts/fetch_metriq.py
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT_DIR = REPO / "datasets" / "metriq"
RAW_BASE = "https://raw.githubusercontent.com/unitaryfoundation/metriq-data/main"
TREE_API = "https://api.github.com/repos/unitaryfoundation/metriq-data/git/trees/main?recursive=1"
LICENSE_URL = "https://github.com/unitaryfoundation/metriq-data/blob/main/LICENSE"
LICENSE_REF = "CC-BY-4.0 · metriq.info"

# benchmark dir-name -> (metric_key, extractor(results)->value|None, human label)
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

_HASH = re.compile(r"[0-9a-f]{6,}$")


def _get_json(url: str) -> dict | list:
    req = urllib.request.Request(url, headers={"User-Agent": "provenova-fetch/1.0",
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=40) as resp:  # noqa: S310 (trusted host)
        return json.loads(resp.read().decode())


def _parse_path(path: str) -> tuple[str, str, str, str] | None:
    """metriq-gym/<ver>/<provider_dir>/<device>/<date>_<time>_<bench>_<hash>.json."""
    parts = path.split("/")
    if len(parts) < 5 or not path.endswith(".json"):
        return None
    provider_dir, device = parts[2], parts[3]
    toks = parts[-1][:-5].split("_")
    if len(toks) < 3:
        return None
    mid = toks[2:]
    if _HASH.fullmatch(mid[-1]):
        mid = mid[:-1]
    bench = "_".join(mid)
    return provider_dir, device, bench, path


def _real_vendor(provider_dir: str, device: str) -> tuple[str, str]:
    """Map the storage dir + device to the true vendor; keep the raw device id.

    Braket-hosted devices (provider_dir 'aws') carry their true maker in the
    device name (e.g. ``iqm_garnet`` -> vendor ``iqm``). The backend id is kept
    verbatim so these Metriq rows never collide with vendor-reported rows.
    """
    d = device.lower()
    if "iqm" in d:
        bid = "iqm_garnet" if "garnet" in d else ("iqm_emerald" if "emerald" in d else device)
        return "iqm", bid
    return provider_dir, device


def main() -> None:
    tree = _get_json(TREE_API)
    entries = [e for e in (_parse_path(o["path"]) for o in tree.get("tree", []))
               if e and e[2] in BENCHMARKS]

    # latest file per (provider_dir, device, bench) — timestamp is in the filename prefix
    latest: dict[tuple[str, str, str], str] = {}
    for provider_dir, device, bench, path in entries:
        key = (provider_dir, device, bench)
        if key not in latest or path.split("/")[-1] > latest[key].split("/")[-1]:
            latest[key] = path

    # merge benchmarks per (vendor, backend_id)
    devices: dict[tuple[str, str], dict] = {}
    for (provider_dir, device, bench), path in sorted(latest.items()):
        metric_key, extract = BENCHMARKS[bench]
        try:
            doc = _get_json(f"{RAW_BASE}/{path}")
        except Exception as e:  # noqa: BLE001
            print(f"  skip {path}: {e}", file=sys.stderr)
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

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    written = 0
    for (vendor, bid), d in sorted(devices.items()):
        dm = dict(d["metrics"])
        if d["nq"]:
            dm["n_qubits"] = d["nq"]
        dm["source"] = "metriq"
        captured = (d["ts"] or "2026-01-01T00:00:00")
        if "+" not in captured and "Z" not in captured:
            captured += "+00:00"
        record = {
            "schema": "qlprov/corpus-record/1.0",
            "provider": vendor,
            "backend_id": bid,
            "captured_at": captured,
            "source": "metriq",
            "license_ref": LICENSE_REF,
            "raw_ref": LICENSE_URL,
            "redistributable_raw": True,
            "note": "Metriq metriq-gym community benchmark records (CC-BY-4.0).",
            "derived_metrics": dm,
            "snapshot_json": {
                "schema": "qlprov/corpus-record/1.0",
                "backend": {"vendor": vendor, "name": bid, "n_qubits": d["nq"]},
                "captured_at": captured,
                "source": "metriq",
                "provenance": {"source": "metriq", "license_ref": LICENSE_REF,
                               "repo": "unitaryfoundation/metriq-data"},
                "metric_provenance": d["prov"],
            },
        }
        path = OUT_DIR / f"{vendor}_{bid.lower().replace(' ', '_').replace('/', '_')}.json"
        path.write_text(json.dumps(record, indent=2))
        written += 1
        print(f"  wrote {path.relative_to(REPO)}  metrics={list(dm)}")
    print(f"metriq devices written: {written}")


if __name__ == "__main__":
    main()
