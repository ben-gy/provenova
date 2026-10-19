"""Scheduled data collectors: keep the public record fresh between deploys.

A Cloudflare Worker cron (``cloudflare/src/index.ts``) calls
``POST /internal/collect`` once a day. Each collector decides whether it is
due, fetches from its upstream and stores what's new; nothing is redeployed.

* ``metriq`` (daily) — every new metriq-gym run (CC-BY-4.0).
* ``ibm`` (weekly) — new IBM calibration snapshots in Qiskit's fake backends
  (Apache-2.0); only files whose git blob changed are downloaded.
* ``experiments`` (weekly) — a newer version of the Awesome Quantum Computing
  Experiments dataset (MIT), kept as a ``PublicDataset`` for /tracker.

Collected corpus rows are tagged ``origin: collector:<name>`` so the startup
sync of ``datasets/`` never deletes them, and they share the bundle's content
hashes, so the two never duplicate. Run state (last success, upstream
versions seen) lives in ``collector_state`` and is shown on /tracker.
"""

from __future__ import annotations

import csv
import datetime as _dt
import io
import json
import logging
from dataclasses import dataclass
from typing import Callable

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from provenova_core.models import CollectorState, PublicDataset

from ..config import get_settings
from . import ibm_history
from . import metriq as metriq_svc
from .corpus_sync import add_rows, series_rows
from .tracker import EXPERIMENTS_COLUMNS, EXPERIMENTS_FILES, EXPERIMENTS_REPO

_log = logging.getLogger("provenova.collectors")

API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"


@dataclass(frozen=True)
class Collector:
    name: str
    every: _dt.timedelta
    run: Callable[[Session, httpx.Client, dict], dict]


def _github_json(client: httpx.Client, path: str):
    headers = {"Accept": "application/vnd.github+json"}
    token = get_settings().github_token
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return client.get(f"{API}/{path}", headers=headers).raise_for_status().json()


def _head(client: httpx.Client, repo: str) -> tuple[str, str]:
    """(commit sha, committer date) of a repo's main branch."""
    c = _github_json(client, f"repos/{repo}/commits/main")
    return c["sha"], c["commit"]["committer"]["date"]


# --- collectors ----------------------------------------------------------------

def collect_metriq(session: Session, client: httpx.Client, state: dict) -> dict:
    result = metriq_svc.refresh_metriq_corpus(session, deadline_s=150.0)
    if not result["complete"]:
        raise RuntimeError(f"metriq refresh cut short by its deadline: {result}")
    return result


def collect_ibm(session: Session, client: httpx.Client, state: dict) -> dict:
    sha, date = _head(client, ibm_history.REPO)
    if sha == state.get("commit"):
        return {"commit": sha[:12], "unchanged": True, "inserted": 0}
    tree = _github_json(client, f"repos/{ibm_history.REPO}/git/trees/{sha}?recursive=1")
    if tree.get("truncated"):
        raise RuntimeError("Qiskit tree listing was truncated")
    blobs = {e["path"]: e["sha"] for e in tree.get("tree", [])
             if e.get("type") == "blob" and e["path"].startswith(ibm_history.BACKENDS_DIR + "/")
             and e["path"].rsplit("/", 1)[-1].startswith("props") and e["path"].endswith(".json")}
    seen = state.get("blobs") or {}
    changed = [p for p, b in blobs.items() if seen.get(p) != b]
    rows = []
    for path in changed:
        body = client.get(f"{RAW}/{ibm_history.REPO}/{sha}/{path}").raise_for_status().content
        got = ibm_history.snapshot(body, repo=ibm_history.REPO, commit=sha, path=path, commit_date=date)
        if got:
            name, snap = got
            rows += series_rows(ibm_history.series(name, [snap]), origin="collector:ibm")
    inserted = add_rows(session, rows)
    state.update(commit=sha, blobs=blobs)
    return {"commit": sha[:12], "changed_files": len(changed), "inserted": inserted}


def collect_experiments(session: Session, client: httpx.Client, state: dict) -> dict:
    from ..db import datasets_dir

    sha, date = _head(client, EXPERIMENTS_REPO)
    bundled = datasets_dir() / "experiments" / "SOURCE.json"
    bundled_commit = None
    if bundled.exists():
        bundled_commit = json.loads(bundled.read_text()).get("commit")
    stored = session.scalar(select(PublicDataset.id).where(
        PublicDataset.name == "experiments", PublicDataset.version == sha))
    if sha in (bundled_commit, state.get("commit")) or stored:
        state["commit"] = sha
        return {"commit": sha[:12], "unchanged": True}

    files = {name: client.get(f"{RAW}/{EXPERIMENTS_REPO}/{sha}/data/{name}").raise_for_status().text
             for name in EXPERIMENTS_FILES}
    licence = client.get(f"{RAW}/{EXPERIMENTS_REPO}/{sha}/LICENSE").raise_for_status().text
    # Publish only what we can still legally and safely use.
    if "MIT License" not in licence:
        raise RuntimeError("upstream licence is no longer MIT; not publishing this version")
    for name, columns in EXPERIMENTS_COLUMNS.items():
        header = set(next(csv.reader(io.StringIO(files[name])), []))
        if not columns <= {h.strip() for h in header}:
            raise RuntimeError(f"{name} lost expected columns {columns - header}")
    commit_date = _dt.datetime.fromisoformat(date.replace("Z", "+00:00"))
    session.add(PublicDataset(
        name="experiments", version=sha, version_date=commit_date, files=files,
        meta={"name": "Awesome Quantum Computing Experiments", "author": "François-Marie Le Régent",
              "repository": f"https://github.com/{EXPERIMENTS_REPO}", "commit": sha,
              "commit_date": date, "license": "MIT", "paper": "https://arxiv.org/abs/2507.03678",
              "fetched": _dt.date.today().isoformat(), "files": EXPERIMENTS_FILES}))
    state["commit"] = sha
    return {"commit": sha[:12], "stored": True}


# Slightly under a day/week, so a daily cron that fires a few minutes early
# still counts as due.
COLLECTORS = [
    Collector("metriq", _dt.timedelta(hours=20), collect_metriq),
    Collector("ibm", _dt.timedelta(days=6, hours=20), collect_ibm),
    Collector("experiments", _dt.timedelta(days=6, hours=20), collect_experiments),
]


def _aware(dt: _dt.datetime | None) -> _dt.datetime | None:
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=_dt.timezone.utc)


def run_due(session: Session, force: set[str] | frozenset[str] = frozenset(),
            now: _dt.datetime | None = None, client: httpx.Client | None = None,
            collectors: list[Collector] | None = None) -> dict:
    """Run every collector that is due (or named in ``force``); one failing
    collector never stops the others. Returns a per-collector summary."""
    now = now or _dt.datetime.now(_dt.timezone.utc)
    out: dict[str, dict] = {}
    client = client or httpx.Client(timeout=30.0, follow_redirects=True,
                                    headers={"User-Agent": "provenova-collector/1.0"})
    with client:
        for c in collectors if collectors is not None else COLLECTORS:
            st = session.get(CollectorState, c.name)
            last_ok = _aware(st.last_ok_at) if st else None
            if c.name not in force and last_ok is not None and now - last_ok < c.every:
                out[c.name] = {"status": "skipped", "next_due": (last_ok + c.every).isoformat()}
                continue
            state = dict((st.state if st else None) or {})
            try:
                detail = c.run(session, client, state)
                status = "ok"
            except Exception as e:  # noqa: BLE001 — recorded, retried on the next run
                session.rollback()
                _log.exception("collector %s failed", c.name)
                detail, status = {"error": f"{type(e).__name__}: {e}"[:500]}, "error"
            st = session.get(CollectorState, c.name) or CollectorState(name=c.name)
            st.last_run_at, st.last_status, st.last_detail = now, status, detail
            if status == "ok":
                st.last_ok_at, st.state = now, state
            session.add(st)
            session.commit()
            out[c.name] = {"status": status, **detail}
    return out
