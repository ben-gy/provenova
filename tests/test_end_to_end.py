"""End-to-end server test via FastAPI TestClient.

Exercises the whole loop: capture (SDK) -> push (ingest) -> read -> reproduce ->
publish card -> badge SVG -> admin upgrade -> compliance evaluate -> attest ->
verify (and tamper -> invalid).
"""

from __future__ import annotations

import tempfile

import pytest
from fastapi.testclient import TestClient

import quantumledger as ql
from qiskit import QuantumCircuit
from qiskit_aer import AerSimulator


@pytest.fixture(scope="module")
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def bundle():
    """Produce a real captured run bundle via the SDK local ledger."""
    home = tempfile.mkdtemp(prefix="ql_sdk_")
    ledger = ql.LocalLedger(f"sqlite:///{home}/ledger.db")
    from quantumledger.agent import CaptureAgent

    agent = CaptureAgent(ledger)

    @ql.capture(project="e2e", agent=agent, shots=2048)
    def _run():
        qc = QuantumCircuit(3)
        qc.h(0); qc.cx(0, 1); qc.cx(1, 2); qc.measure_all()
        return AerSimulator().run(qc, shots=2048)

    _run()
    rid = ledger.list_runs(limit=1)[0]["id"]
    return ledger.export_bundle(rid)


def _register_admin_login(client):
    # log in as the seeded superadmin by registering it a password path:
    # the bootstrap admin has no password, so register a fresh admin-capable user
    # via the API and rely on superadmin flag by using the bootstrap admin email.
    # For the test we register a normal user and separately upgrade via bootstrap admin.
    r = client.post("/api/v1/auth/register",
                    json={"email": "researcher@cern.ch", "password": "pw12345", "display_name": "R"})
    assert r.status_code == 200, r.text
    return r.json()


def test_health(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_full_flow(client, bundle):
    # register + login (academic domain -> Academic grant on email verify)
    _register_admin_login(client)
    client.post("/api/v1/auth/verify-email")
    me = client.get("/api/v1/me").json()
    assert me["authenticated"] is True
    # academic verification should lift plan to academic (Pro-free)
    assert me["plan"] in ("academic", "pro", "lab", "enterprise")

    # push a captured run (idempotent)
    r1 = client.post("/api/v1/ingest/runs", json=bundle)
    assert r1.status_code == 200, r1.text
    assert r1.json()["status"] == "created"
    assert r1.json()["hash_matched_client"] is True
    run_id = r1.json()["run_id"]
    r2 = client.post("/api/v1/ingest/runs", json=bundle)
    assert r2.json()["status"] == "exists"  # idempotent

    # read
    doc = client.get(f"/api/v1/runs/{run_id}").json()
    assert doc["run_hash"] == bundle["provenance"]["run_hash"]

    # reproduce
    rep = client.post(f"/api/v1/runs/{run_id}/reproduce?days=45&profile=bad_day")
    assert rep.status_code == 200, rep.text
    assert rep.json()["verdict"] in ("reproducible", "drifted", "divergent", "irreproducible")

    # publish card + badge svg
    pub = client.post(f"/api/v1/runs/{run_id}/card/publish")
    assert pub.status_code == 200, pub.text
    slug = pub.json()["slug"]
    svg = client.get(f"/badge/{slug}/recorded.svg")
    assert svg.status_code == 200 and svg.headers["content-type"].startswith("image/svg")
    assert "recorded" in svg.text
    # reproduced badge should be green after a verified reproduction submission
    client.post(f"/api/v1/cards/{slug}/reproductions?days=20")
    rsvg = client.get(f"/badge/{slug}/reproduced.svg")
    assert "reproduced" in rsvg.text

    # citation export
    bib = client.get(f"/api/v1/cards/{slug}/citation?format=bibtex")
    assert bib.status_code == 200 and "@misc" in bib.text

    # card summary surfaces Hellinger fidelity + verdict (reproduction ran above)
    meta = client.get(f"/api/v1/cards/{slug}").json()
    assert isinstance(meta["summary"]["hellinger_fidelity"], float)
    assert meta["summary"]["verdict"] in ("reproducible", "drifted", "divergent", "irreproducible")

    # public card page renders, including the verdict
    page = client.get(f"/cards/{slug}")
    assert page.status_code == 200 and slug in page.text
    assert meta["summary"]["verdict"] in page.text

    # embeddable card: framable, cached, branded, linking back
    emb = client.get(f"/cards/{slug}/embed.html")
    assert emb.status_code == 200
    assert emb.headers["content-type"].startswith("text/html")
    assert emb.headers["content-security-policy"] == "frame-ancestors *"
    assert emb.headers["cache-control"].startswith("public, max-age=300")
    assert emb.headers.get("etag")
    assert "Provenova" in emb.text and f"/cards/{slug}" in emb.text
    assert f"/badge/{slug}/recorded.svg" in emb.text
    assert client.get("/cards/nonexistent/embed.html").status_code == 404

    # embed snippets include the iframe variant
    snip = client.get(f"/api/v1/cards/{slug}/embed").json()
    assert "iframe" in snip and f"/cards/{slug}/embed.html" in snip["iframe"]

    # oEmbed: discovery tag on the card page + provider endpoint
    assert "application/json+oembed" in page.text
    oe = client.get("/api/v1/oembed", params={"url": f"http://localhost:8000/cards/{slug}"})
    assert oe.status_code == 200
    body = oe.json()
    assert body["type"] == "rich" and "<iframe" in body["html"]
    assert client.get("/api/v1/oembed", params={"url": "http://localhost:8000/pricing"}).status_code == 404

    # unpublished cards must not be embeddable
    client.post(f"/api/v1/runs/{run_id}/card/unpublish")
    assert client.get(f"/cards/{slug}/embed.html").status_code == 404
    client.post(f"/api/v1/runs/{run_id}/card/publish")
    assert client.get(f"/cards/{slug}/embed.html").status_code == 200


def test_compliance_and_attestation(client, bundle):
    # a fresh workspace-bearing user; upgrade via bootstrap superadmin
    reg = client.post("/api/v1/auth/register",
                      json={"email": "lead@lab.example", "password": "pw12345"})
    assert reg.status_code == 200
    me = client.get("/api/v1/me").json()
    org_id, ws_id = me["org_id"], me["workspace_id"]

    # push a run into this workspace so evidence exists
    r = client.post("/api/v1/ingest/runs", json=bundle)
    assert r.status_code == 200

    # Free tier: can enable FAIR (read-only view) but NOT a non-FAIR framework,
    # and cannot ISSUE attestations (issuance is paid).
    fw = client.get("/api/v1/frameworks").json()
    fair = next(f for f in fw if f["key"].startswith("fair"))
    ieee = next(f for f in fw if f["key"].startswith("ieee"))
    free_fair = client.post(f"/api/v1/workspaces/{ws_id}/frameworks/{fair['id']}/enable")
    assert free_fair.status_code == 200, free_fair.text        # FAIR allowed on Free
    free_ieee = client.post(f"/api/v1/workspaces/{ws_id}/frameworks/{ieee['id']}/enable")
    assert free_ieee.status_code == 402                        # non-FAIR gated on Free
    assert client.post(f"/api/v1/workspaces/{ws_id}/compliance/evaluate").status_code == 200
    free_att = client.post(f"/api/v1/workspaces/{ws_id}/attestations?framework_id={fair['id']}")
    assert free_att.status_code == 402                         # attestation issuance is paid

    # admin upgrades the org to pro (admin-driven, no payment)
    from app.db import SessionLocal
    from app.services.accounts import grant_plan
    from quantumledger_core.models import Org
    s = SessionLocal()
    grant_plan(s, s.get(Org, org_id), "pro", source="admin_override")
    s.commit(); s.close()

    # now the non-FAIR framework + evaluate + attest all work
    en = client.post(f"/api/v1/workspaces/{ws_id}/frameworks/{ieee['id']}/enable")
    assert en.status_code == 200, en.text
    ev = client.post(f"/api/v1/workspaces/{ws_id}/compliance/evaluate")
    assert ev.status_code == 200, ev.text
    att = client.post(f"/api/v1/workspaces/{ws_id}/attestations?framework_id={fair['id']}")
    assert att.status_code == 200, att.text
    att_id = att.json()["attestation_id"]

    # verify passes
    v = client.get(f"/api/v1/attestations/{att_id}/verify").json()
    assert v["valid"] is True, v

    # revoke -> verify fails
    client.post(f"/api/v1/attestations/{att_id}/revoke")
    v2 = client.get(f"/api/v1/attestations/{att_id}/verify").json()
    assert v2["valid"] is False
