"""Fetch the Metriq benchmark history into datasets/metriq/.

Metriq (Unitary Foundation) publishes every ``metriq-gym`` run as a timestamped
JSON file in ``unitaryfoundation/metriq-data`` under a **CC-BY-4.0** licence.
This script lists that repo, downloads every run of the benchmarks Provenova
surfaces (EPLG, CLOPS, BSEQ, QFT accuracy, QAOA ratio) and writes one
``qlprov/corpus-series/1.0`` file per device to
``datasets/metriq/<maker>_<device>.json``: one snapshot per day a benchmark ran,
holding only that day's measurements, each citing its source file.

The builder is shared with the server's Growth API refresh
(``app.services.metriq.build_series``), so both produce identical content
hashes. Deterministic — no model in the loop, so numbers are copied exactly.
The server loads the files on its next deploy (``app.services.corpus_sync``).

Usage:
    python scripts/fetch_metriq.py          # GITHUB_TOKEN raises the API rate limit
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "server"))

from app.services.metriq import TREE_API, build_series  # noqa: E402

OUT_DIR = REPO / "datasets" / "metriq"


def _get_json(url: str):
    headers = {"User-Agent": "provenova-fetch/1.0", "Accept": "application/json"}
    if os.environ.get("GITHUB_TOKEN") and url.startswith("https://api.github.com"):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=40) as resp:  # noqa: S310 (trusted host)
        return json.loads(resp.read().decode())


def main() -> None:
    tree = _get_json(TREE_API)
    if tree.get("truncated"):
        sys.exit("metriq-data tree listing was truncated; refusing to write a partial history")
    series, _ = build_series([o["path"] for o in tree.get("tree", [])], _get_json)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for old in OUT_DIR.glob("*.json"):
        old.unlink()
    for s in series:
        name = f"{s['provider']}_{s['backend_id'].lower().replace(' ', '_').replace('/', '_')}"
        path = OUT_DIR / f"{name}.json"
        path.write_text(json.dumps(s, indent=1) + "\n")
        days = [x["captured_at"][:10] for x in s["snapshots"]]
        print(f"  wrote {path.relative_to(REPO)}  {len(days)} days ({days[0]} → {days[-1]})")
    print(f"metriq devices written: {len(series)}")


if __name__ == "__main__":
    main()
