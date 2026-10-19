"""Scheduled collectors: due-ness, isolation, upstream parsing, and the endpoint.

Upstreams are faked with httpx.MockTransport; each test gets its own database.
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

import httpx
import pytest

import provenova_core as qc
from provenova_core.models import CollectorState, CorpusSnapshot, PublicDataset

REPO = Path(__file__).resolve().parents[1]
DATASETS = REPO / "datasets"
NOW = _dt.datetime(2026, 10, 21, 3, 17, tzinfo=_dt.timezone.utc)


@pytest.fixture()
def db(tmp_path):
    engine = qc.init_db(f"sqlite:///{tmp_path / 'collect.db'}")
    s = qc.session_factory(engine)()
    yield s
    s.close()


def _props(name: str, stamp: str, cz_error: float = 0.002) -> bytes:
    return json.dumps({
        "backend_name": name, "last_update_date": stamp,
        "qubits": [[{"name": "T1", "unit": "us", "value": 200.0},
                    {"name": "T2", "unit": "us", "value": 150.0},
                    {"name": "readout_error", "unit": "", "value": 0.01}]] * 2,
        "gates": [{"gate": "cz", "qubits": [0, 1],
                   "parameters": [{"name": "gate_error", "value": cz_error}]}],
    }).encode()


def _client(routes: dict) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        for prefix, body in routes.items():
            if url.startswith(prefix):
                if isinstance(body, (dict, list)):
                    return httpx.Response(200, json=body)
                return httpx.Response(200, content=body if isinstance(body, bytes) else body.encode())
        return httpx.Response(404)
    return httpx.Client(transport=httpx.MockTransport(handler))


IBM_DIR = "qiskit_ibm_runtime/fake_provider/backends"


def _ibm_routes(commit: str, files: dict[str, bytes]) -> dict:
    tree = [{"path": f"{IBM_DIR}/{p}", "type": "blob", "sha": f"blob-{commit}-{p}"} for p in files]
    routes = {
        "https://api.github.com/repos/Qiskit/qiskit-ibm-runtime/commits/main":
            {"sha": commit, "commit": {"committer": {"date": "2026-10-20T00:00:00Z"}}},
        f"https://api.github.com/repos/Qiskit/qiskit-ibm-runtime/git/trees/{commit}":
            {"tree": tree, "truncated": False},
    }
    for p, body in files.items():
        routes[f"https://raw.githubusercontent.com/Qiskit/qiskit-ibm-runtime/{commit}/{IBM_DIR}/{p}"] = body
    return routes


def test_ibm_collector_keeps_real_devices_and_only_fetches_changes(db):
    from app.services import collectors

    c1 = "a" * 40
    files = {"newchip/props_newchip.json": _props("ibm_newchip", "2026-10-19T09:00:00-04:00"),
             "fakechip/props_fakechip.json": _props("fake_chip", "2026-10-19T09:00:00-04:00"),
             "conf_only/conf_x.json": b"{}"}
    out = collectors.run_due(db, now=NOW, client=_client(_ibm_routes(c1, files)),
                             collectors=[collectors.COLLECTORS[1]])
    assert out["ibm"]["status"] == "ok" and out["ibm"]["inserted"] == 1
    row = db.query(CorpusSnapshot).one()
    assert row.backend_id == "ibm_newchip"
    assert row.captured_at.isoformat().startswith("2026-10-19T13:00:00")
    assert row.snapshot_json["origin"] == "collector:ibm"
    assert row.raw_ref == f"https://github.com/Qiskit/qiskit-ibm-runtime/blob/{c1}/{IBM_DIR}/newchip/props_newchip.json"
    assert row.derived_metrics["median_2q_error"] == 0.002

    # same upstream commit next week: nothing fetched
    st = db.get(CollectorState, "ibm")
    assert st.state["commit"] == c1
    later = NOW + _dt.timedelta(days=7)
    out = collectors.run_due(db, now=later, client=_client(_ibm_routes(c1, {})),
                             collectors=[collectors.COLLECTORS[1]])
    assert out["ibm"]["unchanged"] is True


def test_collector_rows_survive_the_startup_sync(db):
    from app.services import collectors
    from app.services.corpus_sync import sync_bundled

    files = {"newchip/props_newchip.json": _props("ibm_newchip", "2026-10-19T09:00:00-04:00")}
    collectors.run_due(db, now=NOW, client=_client(_ibm_routes("b" * 40, files)),
                       collectors=[collectors.COLLECTORS[1]])
    sync_bundled(db, DATASETS)
    assert db.query(CorpusSnapshot).filter_by(backend_id="ibm_newchip").count() == 1


def test_due_skip_force_and_failure_isolation(db):
    from app.services import collectors

    calls = []

    def ok(session, client, state):
        calls.append("ok")
        state["n"] = state.get("n", 0) + 1
        return {"inserted": 0}

    def boom(session, client, state):
        calls.append("boom")
        raise RuntimeError("upstream down")

    cs = [collectors.Collector("good", _dt.timedelta(hours=20), ok),
          collectors.Collector("bad", _dt.timedelta(hours=20), boom)]
    out = collectors.run_due(db, now=NOW, client=_client({}), collectors=cs)
    assert out["good"]["status"] == "ok"
    assert out["bad"]["status"] == "error" and "upstream down" in out["bad"]["error"]
    bad = db.get(CollectorState, "bad")
    assert bad.last_ok_at is None and bad.last_run_at is not None

    # an hour later: good isn't due, bad is retried
    calls.clear()
    out = collectors.run_due(db, now=NOW + _dt.timedelta(hours=1), client=_client({}), collectors=cs)
    assert out["good"]["status"] == "skipped" and calls == ["boom"]
    # forcing runs it anyway, and state carries over between runs
    out = collectors.run_due(db, force={"good"}, now=NOW + _dt.timedelta(hours=2),
                             client=_client({}), collectors=cs)
    assert out["good"]["status"] == "ok"
    assert db.get(CollectorState, "good").state == {"n": 2}


def _experiment_routes(commit: str, licence: str, date: str = "2026-10-15T00:00:00Z") -> dict:
    from app.services.tracker import EXPERIMENTS_FILES, EXPERIMENTS_REPO

    routes = {f"https://api.github.com/repos/{EXPERIMENTS_REPO}/commits/main":
              {"sha": commit, "commit": {"committer": {"date": date}}},
              f"https://raw.githubusercontent.com/{EXPERIMENTS_REPO}/{commit}/LICENSE": licence}
    for name in EXPERIMENTS_FILES:
        text = (DATASETS / "experiments" / name).read_text()
        if name == "qubit_count.csv":
            text += '"99999","A newer record","Newman","https://example.org/new","2026","Neutral atoms",""\n'
        routes[f"https://raw.githubusercontent.com/{EXPERIMENTS_REPO}/{commit}/data/{name}"] = text
    return routes


def test_experiments_collector_publishes_a_newer_version(db):
    from app.services import collectors, tracker

    exp = collectors.COLLECTORS[2]
    out = collectors.run_due(db, now=NOW, client=_client(_experiment_routes("c" * 40, "MIT License\n...")),
                             collectors=[exp])
    assert out["experiments"]["stored"] is True
    assert db.query(PublicDataset).filter_by(name="experiments").count() == 1
    m = tracker.experiments(DATASETS, db)
    assert m["source"]["commit"] == "c" * 40
    assert max(p["value"] for p in m["qubit_count"]) == 99999
    # without the database, the committed copy is used
    assert tracker.experiments(DATASETS)["source"]["commit"] != "c" * 40


def test_experiments_collector_refuses_a_licence_change(db):
    from app.services import collectors

    out = collectors.run_due(db, now=NOW,
                             client=_client(_experiment_routes("d" * 40, "All rights reserved")),
                             collectors=[collectors.COLLECTORS[2]])
    assert out["experiments"]["status"] == "error" and "licence" in out["experiments"]["error"]
    assert db.query(PublicDataset).count() == 0


def test_metriq_same_day_rows_are_replaced_not_duplicated(db):
    from app.services.metriq import insert_series

    def series(metrics: dict, ts: str) -> dict:
        return {"provider": "ibm", "backend_id": "ibm_x", "source": "metriq",
                "license_ref": "CC-BY-4.0 · metriq.info", "redistributable_raw": True,
                "snapshots": [{"captured_at": ts, "derived_metrics": {**metrics, "source": "metriq"},
                               "metric_provenance": []}]}

    insert_series(db, series({"eplg": 0.003}, "2026-10-20T01:00:00+00:00"))
    db.commit()
    # tomorrow's collection sees more runs from the same day
    insert_series(db, series({"eplg": 0.003, "clops": 3e5}, "2026-10-20T09:00:00+00:00"))
    db.commit()
    rows = db.query(CorpusSnapshot).filter_by(backend_id="ibm_x").all()
    assert len(rows) == 1 and set(rows[0].derived_metrics) == {"eplg", "clops", "source"}


# --- the endpoint ---------------------------------------------------------------

@pytest.fixture()
def client(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


def test_collect_endpoint_is_hidden_without_a_valid_token(client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "collect_token", "")
    assert client.post("/internal/collect").status_code == 404
    monkeypatch.setattr(get_settings(), "collect_token", "s3cret-token")
    assert client.post("/internal/collect").status_code == 404
    assert client.post("/internal/collect", headers={"Authorization": "Bearer nope"}).status_code == 404


def test_collect_endpoint_runs_collectors(client, monkeypatch):
    from app.api import internal
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "collect_token", "s3cret-token")
    seen = {}

    def fake_run_due(session, force=frozenset(), **kw):
        seen["force"] = force
        return {"metriq": {"status": "ok"}}

    monkeypatch.setattr(internal.collectors, "run_due", fake_run_due)
    r = client.post("/internal/collect?force=ibm,metriq",
                    headers={"Authorization": "Bearer s3cret-token"})
    assert r.status_code == 200 and r.json() == {"results": {"metriq": {"status": "ok"}}}
    assert seen["force"] == {"ibm", "metriq"}
    r = client.post("/internal/collect?force=all", headers={"Authorization": "Bearer s3cret-token"})
    assert seen["force"] == {"metriq", "ibm", "experiments"}
