"""Unit tests for entitlements while Provenova is free: every plan gets every
feature with no volume caps, a fair-use cap on permanent DOIs only, academic
verification, and no 402s from the formerly-gated endpoints.
"""

from __future__ import annotations

import pytest

from app.entitlements import (
    DOI_FAIR_USE_MONTHLY,
    FEATURES,
    QUOTAS,
    features_for,
    has_feature,
    is_unlimited,
    quota_for,
)
from app.services.accounts import is_academic_domain

PLANS = ("free", "academic", "pro", "lab", "enterprise")


# --------------------------------------------------------------------------- #
# Entitlements: everything is free.
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("plan", PLANS)
def test_every_plan_has_every_feature(plan):
    assert features_for(plan) == set(FEATURES)
    for feature in ("attestation_signing", "trust_center", "compliance_frameworks",
                    "continuous_monitoring", "private_records", "compare_vs_fleet"):
        assert has_feature(plan, feature), (plan, feature)


@pytest.mark.parametrize("plan", PLANS)
def test_no_volume_caps(plan):
    for key in ("seats", "frameworks_allowed", "private_run_cap"):
        assert is_unlimited(quota_for(plan, key)), (plan, key)


def test_dois_keep_a_fair_use_cap_not_a_tier():
    # DOIs are permanent, so they stay rate-limited — identically on every plan.
    caps = {quota_for(plan, "doi_monthly_cap") for plan in PLANS}
    assert caps == {DOI_FAIR_USE_MONTHLY}
    assert not is_unlimited(DOI_FAIR_USE_MONTHLY)
    assert set(QUOTAS) == set(PLANS)


# --------------------------------------------------------------------------- #
# Academic verification — international coverage without false positives.
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("email", [
    "a@mit.edu", "b@stanford.edu",
    "c@ox.ac.uk", "d@cam.ac.uk",           # .ac.uk
    "e@unsw.edu.au", "f@sydney.edu.au",    # .edu.au
    "g@auckland.ac.nz",                    # .ac.nz
    "h@u-tokyo.ac.jp",                     # .ac.jp (also allowlisted)
    "i@uni-heidelberg.de",                 # uni-*
    "j@cern.ch",                           # allowlist
])
def test_academic_domains_accepted(email):
    assert is_academic_domain(email) is True, email


@pytest.mark.parametrize("email", [
    "a@gmail.com", "b@acme.com",
    "c@universitypizza.com",     # "university" only as a full label, not substring
    "d@communi-cations.com",     # must not match uni- prefix mid-label
    "e@education.io",
])
def test_non_academic_domains_rejected(email):
    assert is_academic_domain(email) is False, email


# --------------------------------------------------------------------------- #
# Enforcement wiring (private-run cap + frameworks_allowed) via TestClient.
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="module")
def client():
    from app.main import app
    from fastapi.testclient import TestClient
    with TestClient(app) as c:
        yield c


def _capture_bundle():
    import tempfile
    import provenova as ql
    from provenova.agent import CaptureAgent
    from qiskit import QuantumCircuit
    from qiskit_aer import AerSimulator

    home = tempfile.mkdtemp(prefix="ql_tier_")
    ledger = ql.LocalLedger(f"sqlite:///{home}/ledger.db")
    agent = CaptureAgent(ledger)

    @ql.capture(project="tier", agent=agent, shots=512)
    def _run():
        qc = QuantumCircuit(2)
        qc.h(0); qc.cx(0, 1); qc.measure_all()
        return AerSimulator().run(qc, shots=512)

    _run()
    rid = ledger.list_runs(limit=1)[0]["id"]
    return ledger.export_bundle(rid)


def test_free_account_is_never_capped_or_gated(client):
    reg = client.post("/api/v1/auth/register",
                      json={"email": "capuser@acme.com", "password": "pw123456"})
    assert reg.status_code == 200, reg.text
    me = client.get("/api/v1/me").json()
    assert me["plan"] == "free"
    ws_id = me["workspace_id"]

    # Distinct private runs are all accepted (no private-run cap).
    for _ in range(2):
        r = client.post("/api/v1/ingest/runs", json=_capture_bundle())
        assert r.status_code == 200, r.text

    # Every framework can be enabled, and attestations issued, on Free.
    fws = client.get("/api/v1/frameworks").json()
    for fw in fws:
        en = client.post(f"/api/v1/workspaces/{ws_id}/frameworks/{fw['id']}/enable")
        assert en.status_code == 200, (fw["key"], en.text)
    assert client.post(f"/api/v1/workspaces/{ws_id}/compliance/evaluate").status_code == 200
    att = client.post(f"/api/v1/workspaces/{ws_id}/attestations?framework_id={fws[0]['id']}")
    assert att.status_code != 402, att.text


def test_pricing_pages_redirect(client):
    for path, target in (("/pricing", "/"), ("/app/plans", "/app/settings"),
                         ("/docs/pricing-faq", "/docs")):
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 301, path
        assert r.headers["location"] == target, path


def test_trust_center_active_for_every_org(client):
    client.post("/api/v1/auth/register",
                json={"email": "trust@acme.com", "password": "pw123456"})
    from app.db import SessionLocal
    from provenova_core.models import Org, OrgMembership, Account
    from sqlalchemy import select
    s = SessionLocal()
    acc = s.scalar(select(Account).where(Account.email == "trust@acme.com"))
    org = s.get(Org, s.scalar(select(OrgMembership.org_id).where(OrgMembership.account_id == acc.id)))
    slug = org.slug
    s.close()
    body = client.get(f"/api/v1/trust/{slug}").json()
    assert "trust_center" not in body  # no "inactive" marker
    assert client.get(f"/trust/{slug}").status_code == 200
