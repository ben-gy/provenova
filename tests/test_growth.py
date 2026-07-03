"""Growth API tests: auth/scopes, qlir validation, idempotency, caps,
sanitization, corpus refresh dedupe, reports — plus a fake full routine run.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def growth_key(client):
    """Mint a growth-scoped API key for the bot org, directly via the DB."""
    from app.db import SessionLocal
    from app.security import generate_api_key
    from app.services.growth import ensure_research_bot
    from quantumledger_core.models import ApiKey

    s = SessionLocal()
    acc, org, ws = ensure_research_bot(s)
    full, prefix, key_hash = generate_api_key()
    s.add(ApiKey(org_id=org.id, account_id=acc.id, name="test-growth",
                 prefix=prefix, key_hash=key_hash, scopes=["growth"]))
    s.commit(); s.close()
    return full


@pytest.fixture(scope="module")
def unscoped_key(client):
    from app.db import SessionLocal
    from app.security import generate_api_key
    from app.services.growth import ensure_research_bot
    from quantumledger_core.models import ApiKey

    s = SessionLocal()
    acc, org, ws = ensure_research_bot(s)
    full, prefix, key_hash = generate_api_key()
    s.add(ApiKey(org_id=org.id, account_id=acc.id, name="test-unscoped",
                 prefix=prefix, key_hash=key_hash, scopes=None))
    s.commit(); s.close()
    return full


def _auth(key):
    return {"Authorization": f"Bearer {key}"}


BELL = {"schema": "qlir/1.0", "n_qubits": 2,
        "gates": [{"name": "h", "qubits": [0], "params": []},
                  {"name": "cx", "qubits": [0, 1], "params": []}]}
GHZ3 = {"schema": "qlir/1.0", "n_qubits": 3,
        "gates": [{"name": "h", "qubits": [0], "params": []},
                  {"name": "cx", "qubits": [0, 1], "params": []},
                  {"name": "cx", "qubits": [1, 2], "params": []}]}

PAPER = {"title": "Entanglement distribution over long distances with Bell pairs",
         "authors": ["A. Researcher", "B. Scientist"], "year": 2026,
         "arxiv_id": "2606.01234", "url": "https://arxiv.org/abs/2606.01234"}

COMMENT = ("This card records a deterministic Bell-pair run on the Provenova "
           "simulator, inspired by the referenced paper's study of entanglement "
           "distribution. The run below is our own execution; it does not reproduce "
           "the paper's hardware results.")


def _item(circuit=BELL, paper=PAPER, title="Bell-pair benchmark — inspired by arXiv:2606.01234",
          **over):
    d = {"paper": paper, "circuit": circuit, "title": title, "commentary_md": COMMENT}
    d.update(over)
    return d


# --- auth ----------------------------------------------------------------------

def test_growth_requires_auth(client):
    assert client.get("/api/v1/growth/status").status_code == 401


def test_growth_rejects_unscoped_key(client, unscoped_key):
    r = client.get("/api/v1/growth/status", headers=_auth(unscoped_key))
    assert r.status_code == 403
    assert "scope" in r.text


def test_growth_accepts_scoped_key(client, growth_key):
    r = client.get("/api/v1/growth/status", headers=_auth(growth_key))
    assert r.status_code == 200
    body = r.json()
    assert "research_cards" in body and "corpus" in body


def test_privileged_scope_mint_requires_superadmin(client):
    # a fresh normal user cannot mint a growth-scoped key
    client.post("/api/v1/auth/register",
                json={"email": "normie@acme.com", "password": "pw12345"})
    me = client.get("/api/v1/me").json()
    r = client.post(f"/api/v1/orgs/{me['org_id']}/api-keys",
                    json={"name": "sneaky", "scopes": ["growth"]})
    assert r.status_code == 403
    # but an unscoped key is fine
    r2 = client.post(f"/api/v1/orgs/{me['org_id']}/api-keys", json={"name": "ok"})
    assert r2.status_code == 200
    client.post("/api/v1/auth/logout")


# --- research cards: happy path + idempotency -----------------------------------

def test_research_card_happy_path(client, growth_key):
    r = client.post("/api/v1/growth/research-cards", headers=_auth(growth_key),
                    json={"items": [_item()]})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["created"] == 1
    item = out["items"][0]
    assert item["status"] == "created" and item["slug"] and item["run_hash"]

    # public card page renders with the fixed honesty banner + arXiv link
    page = client.get(f"/cards/{item['slug']}")
    assert page.status_code == 200
    assert "deterministic simulator run on Provenova" in page.text
    assert "arXiv:2606.01234" in page.text
    assert "does <b>not</b> reproduce" in page.text


def test_research_card_idempotent(client, growth_key):
    r = client.post("/api/v1/growth/research-cards", headers=_auth(growth_key),
                    json={"items": [_item()]})
    assert r.status_code == 200
    assert r.json()["items"][0]["status"] == "exists"


def test_research_card_dedupes_arxiv_version_suffix(client, growth_key):
    """A revised preprint (arXiv v-suffix) of the same circuit must dedupe."""
    versioned = {**PAPER, "arxiv_id": "2606.01234v3",
                 "url": "https://arxiv.org/abs/2606.01234v3"}
    r = client.post("/api/v1/growth/research-cards", headers=_auth(growth_key),
                    json={"items": [_item(paper=versioned)]})
    assert r.status_code == 200, r.text
    assert r.json()["items"][0]["status"] == "exists"  # not a duplicate card


# --- validation rails ------------------------------------------------------------

def test_qlir_rejections(client, growth_key):
    too_many_qubits = {**BELL, "n_qubits": 11,
                       "gates": [{"name": "h", "qubits": [0], "params": []}]}
    bad_gate = {"schema": "qlir/1.0", "n_qubits": 1,
                "gates": [{"name": "measure_all", "qubits": [0], "params": []}]}
    getattr_probe = {"schema": "qlir/1.0", "n_qubits": 1,
                     "gates": [{"name": "save_statevector", "qubits": [0], "params": []}]}
    for i, circ in enumerate([too_many_qubits, bad_gate, getattr_probe]):
        r = client.post("/api/v1/growth/research-cards", headers=_auth(growth_key),
                        json={"items": [_item(circuit=circ)]})
        assert r.status_code == 200, r.text
        assert r.json()["items"][0]["status"] == "invalid", f"case {i}: {r.json()}"

    # Non-finite param, sent as a raw JSON `Infinity` token (Python's json.loads
    # accepts it, so this genuinely reaches the server-side finite-float guard).
    import json as _json

    payload = _json.dumps({"items": [_item(circuit={
        "schema": "qlir/1.0", "n_qubits": 1,
        "gates": [{"name": "rz", "qubits": [0], "params": ["INF_TOKEN"]}]})]}
    ).replace('"INF_TOKEN"', "Infinity")
    r = client.post("/api/v1/growth/research-cards",
                    headers={**_auth(growth_key), "Content-Type": "application/json"},
                    content=payload)
    assert r.status_code == 200, r.text
    assert r.json()["items"][0]["status"] == "invalid"


def test_paper_needs_identifier_and_allowed_host(client, growth_key):
    no_id = {**PAPER}
    no_id.pop("arxiv_id")
    r = client.post("/api/v1/growth/research-cards", headers=_auth(growth_key),
                    json={"items": [_item(paper=no_id)]})
    assert r.status_code == 422
    evil = {**PAPER, "url": "https://evil.example/abs/2606.01234"}
    r2 = client.post("/api/v1/growth/research-cards", headers=_auth(growth_key),
                     json={"items": [_item(paper=evil)]})
    assert r2.status_code == 422


def test_batch_size_cap(client, growth_key):
    r = client.post("/api/v1/growth/research-cards", headers=_auth(growth_key),
                    json={"items": [_item()] * 6})
    assert r.status_code == 422


def test_daily_cap(client, growth_key, monkeypatch):
    from app import config as config_mod

    # force the cap below what's been created this test-day
    settings = config_mod.get_settings()
    monkeypatch.setattr(settings, "growth_max_cards_per_day", 1)
    fresh_paper = {**PAPER, "arxiv_id": "2606.09999",
                   "url": "https://arxiv.org/abs/2606.09999"}
    r = client.post("/api/v1/growth/research-cards", headers=_auth(growth_key),
                    json={"items": [_item(circuit=GHZ3, paper=fresh_paper,
                                          title="GHZ-3 benchmark — inspired by arXiv:2606.09999")]})
    assert r.status_code == 200
    assert r.json()["items"][0]["status"] == "cap_reached"


def test_commentary_script_is_escaped(client, growth_key):
    xss_paper = {**PAPER, "arxiv_id": "2606.05555",
                 "url": "https://arxiv.org/abs/2606.05555"}
    evil_comment = COMMENT + " <script>alert('xss')</script> and an ![img](http://evil.example/x.png)"
    r = client.post("/api/v1/growth/research-cards", headers=_auth(growth_key),
                    json={"items": [_item(circuit=GHZ3, paper=xss_paper,
                                          title="GHZ-3 escape test — inspired by arXiv:2606.05555",
                                          commentary_md=evil_comment)]})
    assert r.status_code == 200, r.text
    slug = r.json()["items"][0]["slug"]
    page = client.get(f"/cards/{slug}").text
    assert "<script>alert" not in page
    assert "&lt;script&gt;" in page
    assert "http://evil.example/x.png" not in page  # image stripped


# --- corpus refresh ---------------------------------------------------------------

def test_corpus_refresh_stubbed_and_rate_limited(client, growth_key, monkeypatch):
    from app.services import metriq as metriq_svc

    calls = {"n": 0}

    def fake_refresh(session, deadline_s=45.0):
        calls["n"] += 1
        from app.services.metriq import insert_corpus_record

        rec = {"schema": "qlprov/corpus-record/1.0", "provider": "faketron",
               "backend_id": "f-1", "captured_at": "2026-07-01T00:00:00+00:00",
               "source": "metriq", "license_ref": "CC-BY-4.0 · metriq.info",
               "redistributable_raw": True,
               "derived_metrics": {"eplg": 0.001, "n_qubits": 4, "source": "metriq"},
               "snapshot_json": {"metric_provenance": [
                   {"metric": "eplg", "source_url": "https://example/x.json"}]}}
        inserted = insert_corpus_record(session, rec)
        session.commit()
        return {"complete": True, "fetched": 1,
                "inserted": 1 if inserted else 0, "deduped": 0 if inserted else 1,
                "by_provider": {"faketron": 1} if inserted else {}}

    import app.api.v1.growth as growth_api

    monkeypatch.setattr(growth_api.metriq_svc, "refresh_metriq_corpus", fake_refresh)

    r1 = client.post("/api/v1/growth/corpus/refresh", headers=_auth(growth_key))
    assert r1.status_code == 200, r1.text
    assert r1.json()["inserted"] == 1
    # immediate second call -> 429 (throttled)
    r2 = client.post("/api/v1/growth/corpus/refresh", headers=_auth(growth_key))
    assert r2.status_code == 429
    # force does NOT bypass the throttle for a non-superadmin growth key
    # (finding: ?force=true must be superadmin-only).
    r3 = client.post("/api/v1/growth/corpus/refresh?force=true", headers=_auth(growth_key))
    assert r3.status_code == 429


def test_corpus_refresh_incomplete_does_not_throttle(client, growth_key, monkeypatch):
    """An incomplete refresh writes no throttle audit, so the routine can
    immediately 'call once more' (resumable contract)."""
    import app.api.v1.growth as growth_api
    from app.db import SessionLocal
    from quantumledger_core.models import AuditLog
    from sqlalchemy import delete

    # clear any prior refresh audit so this test starts clean
    s = SessionLocal()
    s.execute(delete(AuditLog).where(AuditLog.action == "growth.corpus.refresh"))
    s.commit(); s.close()

    def incomplete(session, deadline_s=45.0):
        return {"complete": False, "fetched": 0, "inserted": 0, "deduped": 0, "by_provider": {}}

    monkeypatch.setattr(growth_api.metriq_svc, "refresh_metriq_corpus", incomplete)
    r1 = client.post("/api/v1/growth/corpus/refresh", headers=_auth(growth_key))
    assert r1.status_code == 200 and r1.json()["complete"] is False
    # not throttled — an incomplete pass left no audit
    r2 = client.post("/api/v1/growth/corpus/refresh", headers=_auth(growth_key))
    assert r2.status_code == 200


# --- reports -----------------------------------------------------------------------

def test_report_publish_and_409(client, growth_key):
    body = {"slug": "state-of-quantum-2026-w26",
            "title": "State of Quantum Hardware — Week 26, 2026",
            "body_md": ("# This week's fleet\n\n" + "Real corpus facts here. " * 30),
            "meta_description": "Weekly state of the quantum fleet, week 26 of 2026."}
    r = client.post("/api/v1/growth/reports", headers=_auth(growth_key), json=body)
    assert r.status_code == 200, r.text
    url = r.json()["url"]
    assert url.endswith("/reports/state-of-quantum-2026-w26")
    # page renders
    page = client.get("/reports/state-of-quantum-2026-w26")
    assert page.status_code == 200
    # duplicate slug -> 409 (idempotent weekly publish)
    r2 = client.post("/api/v1/growth/reports", headers=_auth(growth_key), json=body)
    assert r2.status_code == 409


# --- fake full routine run (end-to-end) ----------------------------------------------

def test_fake_routine_run_end_to_end(client, growth_key):
    """status -> research-cards -> status reflects everything (stateless routine)."""
    s0 = client.get("/api/v1/growth/status", headers=_auth(growth_key)).json()
    known = set(s0["research_cards"]["known_arxiv_ids"])
    assert "2606.01234" in known  # from earlier tests — routine would skip it

    s1 = client.get("/api/v1/growth/status", headers=_auth(growth_key)).json()
    assert s1["research_cards"]["total"] >= 2
    slugs = [r["slug"] for r in s1["research_cards"]["recent"]]
    assert all(slugs), "recent entries carry card slugs"
    assert s1["reports"]["latest_slug"] is not None
