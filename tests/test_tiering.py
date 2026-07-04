"""Unit tests for the pricing restructure: entitlements, quotas, academic
verification, the private-run cap and frameworks_allowed enforcement.
"""

from __future__ import annotations

import pytest

from app.entitlements import (
    FEATURES,
    QUOTAS,
    UNLIMITED,
    features_for,
    has_feature,
    is_unlimited,
    quota_for,
)
from app.services.accounts import is_academic_domain


# --------------------------------------------------------------------------- #
# Entitlements: Free competes with OSS; trust artifacts are paid.
# --------------------------------------------------------------------------- #

def test_free_includes_private_records_and_fleet_and_fair_view():
    free = features_for("free")
    assert "private_records" in free
    assert "compare_vs_fleet" in free          # Benchmarked badge reachable on Free
    assert "compliance_frameworks" in free     # read-only FAIR view


def test_free_excludes_trust_artifacts():
    assert not has_feature("free", "attestation_signing")   # issuance is paid
    assert not has_feature("free", "continuous_monitoring")
    assert not has_feature("free", "trust_center")
    assert not has_feature("free", "sso_saml")


def test_attestation_signing_starts_at_academic():
    for plan in ("academic", "pro", "lab", "enterprise"):
        assert has_feature(plan, "attestation_signing"), plan


def test_sso_moved_to_lab():
    assert not has_feature("pro", "sso_saml")
    assert has_feature("lab", "sso_saml")
    assert has_feature("enterprise", "sso_saml")


# --------------------------------------------------------------------------- #
# Quotas
# --------------------------------------------------------------------------- #

def test_private_run_cap_free_is_finite_others_unlimited():
    assert quota_for("free", "private_run_cap") == 250
    assert not is_unlimited(quota_for("free", "private_run_cap"))
    for plan in ("academic", "pro", "lab", "enterprise"):
        assert is_unlimited(quota_for(plan, "private_run_cap")), plan


def test_frameworks_allowed_caps():
    assert quota_for("free", "frameworks_allowed") == 1     # FAIR only
    assert quota_for("pro", "frameworks_allowed") == 10
    for plan in ("academic", "lab", "enterprise"):
        assert is_unlimited(quota_for(plan, "frameworks_allowed")), plan


def test_seats_ladder():
    assert quota_for("free", "seats") == 1
    assert quota_for("academic", "seats") == 15
    assert quota_for("pro", "seats") == 10
    assert quota_for("lab", "seats") == 50
    assert is_unlimited(quota_for("enterprise", "seats"))


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


def test_private_run_cap_enforced_and_publish_frees_slot(client, monkeypatch):
    # A fresh Free workspace.
    reg = client.post("/api/v1/auth/register",
                      json={"email": "capuser@acme.com", "password": "pw12345"})
    assert reg.status_code == 200, reg.text
    me = client.get("/api/v1/me").json()
    assert me["plan"] == "free"
    ws_id = me["workspace_id"]

    # Force the cap down to 0 so the very next private run is blocked, without
    # having to push 250 runs. Enforcement reads quota_for(plan, 'private_run_cap').
    import app.api.v1.ingest as ingest_mod
    orig = ingest_mod.private_run_usage

    def _tiny_usage(session, plan, workspace_id):
        u = orig(session, plan, workspace_id)
        cap = 1
        used = u["used"]
        return {**u, "cap": cap, "unlimited": False,
                "at_cap": used >= cap, "pct": min(100, round(100 * used / cap))}
    monkeypatch.setattr(ingest_mod, "private_run_usage", _tiny_usage)

    b1 = _capture_bundle()
    r1 = client.post("/api/v1/ingest/runs", json=b1)
    assert r1.status_code == 200, r1.text            # first private run fits (used 0 < cap 1)
    run_id = r1.json()["run_id"]

    # Second distinct private run is over the cap -> 402 cap_reached, data untouched.
    b2 = _capture_bundle()
    r2 = client.post("/api/v1/ingest/runs", json=b2)
    assert r2.status_code == 402, r2.text
    detail = r2.json()["detail"]
    assert detail["error"] == "cap_reached"
    assert detail["can_publish"] is True

    # Publishing the first run to a public card frees its private slot...
    pub = client.post(f"/api/v1/runs/{run_id}/card/publish")
    assert pub.status_code == 200, pub.text
    # ...so the second run can now be ingested.
    r2b = client.post("/api/v1/ingest/runs", json=b2)
    assert r2b.status_code == 200, r2b.text
