"""Vendor the "Awesome Quantum Computing Experiments" dataset -> datasets/experiments/.

François-Marie Le Régent's curated database of landmark quantum computing
experiments (https://github.com/francois-marie/awesome-quantum-computing-experiments,
MIT licence; paper: arXiv:2507.03678). Every row is one published experiment with
its year, hardware platform, headline number and a link to the paper. It powers
the long-run charts on /tracker (entangling-gate error, qubit counts, coherence,
error-correction experiments).

This copies the CSVs verbatim at a pinned commit, plus the licence and a
SOURCE.json recording the commit, so the tracker always cites an exact version.
Re-run to pick up new rows; corrections belong upstream (open a PR there).

Usage:
    python scripts/fetch_experiments.py            # GITHUB_TOKEN raises the API rate limit
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "datasets" / "experiments"
UPSTREAM = "francois-marie/awesome-quantum-computing-experiments"
FILES = ["entangled_state_error_exp.csv", "qubit_count.csv", "physical_qubits.csv",
         "qec_exp.csv", "research_groups.csv"]


def _get(url: str) -> bytes:
    headers = {"User-Agent": "provenova-fetch/1.0"}
    if os.environ.get("GITHUB_TOKEN") and url.startswith("https://api.github.com"):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=40) as r:  # noqa: S310
        return r.read()


def main() -> None:
    head = json.loads(_get(f"https://api.github.com/repos/{UPSTREAM}/commits/main"))
    sha = head["sha"]
    OUT.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        (OUT / name).write_bytes(_get(f"https://raw.githubusercontent.com/{UPSTREAM}/{sha}/data/{name}"))
    (OUT / "LICENSE").write_bytes(_get(f"https://raw.githubusercontent.com/{UPSTREAM}/{sha}/LICENSE"))
    (OUT / "SOURCE.json").write_text(json.dumps({
        "name": "Awesome Quantum Computing Experiments",
        "author": "François-Marie Le Régent",
        "repository": f"https://github.com/{UPSTREAM}",
        "commit": sha,
        "commit_date": head["commit"]["committer"]["date"],
        "license": "MIT",
        "paper": "https://arxiv.org/abs/2507.03678",
        "fetched": _dt.date.today().isoformat(),
        "files": FILES,
    }, indent=2) + "\n")
    print(f"vendored {len(FILES)} files at {sha[:12]}")


if __name__ == "__main__":
    main()
