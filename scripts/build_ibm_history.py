"""Build the IBM fleet calibration history -> datasets/ibm/<backend>.json.

IBM device calibration snapshots ship in Qiskit's open-source fake backends
(Apache-2.0): every ``props_<device>.json`` is a real BackendProperties dump
from the named machine, stamped with the device's own ``last_update_date``.
The files have been refreshed over the years, so their **git history** is a
genuine longitudinal record of IBM's public fleet, from the 5-qubit
``ibmq`` era to Heron.

This script walks that history in three places (the fake backends moved
twice) and writes one ``qlprov/corpus-series/1.0`` file per device:

* ``Qiskit/qiskit-ibm-runtime``  ``qiskit_ibm_runtime/fake_provider/backends``
* ``Qiskit/qiskit`` @ 0.46.0       ``qiskit/providers/fake_provider/backends``
* ``Qiskit/qiskit`` @ 0.19.0       ``qiskit/test/mock/backends``

Each snapshot keeps only aggregate metrics plus a permalink to the exact raw
file at its commit and the file's SHA-256 (``app.services.ibm_history``, shared
with the weekly ``ibm`` collector), so every number can be re-derived from the
public source. Snapshots are deduplicated on (device, last_update_date).

Run (needs network; a token raises GitHub's API rate limit):

    GITHUB_TOKEN=... python scripts/build_ibm_history.py
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "server"))

from app.services import ibm_history  # noqa: E402

OUT = REPO_ROOT / "datasets" / "ibm"
API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"

# (repo, directory of per-device folders, ref to list the folders at)
SOURCES = [
    (ibm_history.REPO, ibm_history.BACKENDS_DIR, "main"),
    ("Qiskit/qiskit", "qiskit/providers/fake_provider/backends", "0.46.0"),
    ("Qiskit/qiskit", "qiskit/test/mock/backends", "0.19.0"),
]


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
                body = _get(f"{RAW}/{repo}/{c['sha']}/{path}", api=False)
                got = body and ibm_history.snapshot(body, repo=repo, commit=c["sha"], path=path,
                                                    commit_date=c["commit"]["committer"]["date"])
                if not got:
                    continue
                name, snap = got
                devices.setdefault(name, {}).setdefault(snap["captured_at"], snap)
    return devices


def main() -> None:
    devices = collect()
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.json"):
        old.unlink()
    for name, snaps in sorted(devices.items()):
        doc = ibm_history.series(name, list(snaps.values()))
        (OUT / f"{name}.json").write_text(json.dumps(doc, indent=1) + "\n")
        s = doc["snapshots"]
        print(f"{name}: {len(s)} snapshots ({s[0]['captured_at'][:10]} → {s[-1]['captured_at'][:10]})")


if __name__ == "__main__":
    main()
