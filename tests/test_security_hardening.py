"""Regression tests for the security-audit remediation.

Grouped by the plan's priority tiers. Each test pins a specific fix so a future
change that reopens the hole fails loudly.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


def _register(client, email, password="password123"):
    r = client.post("/api/v1/auth/register", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


# --- P0: secret-key fail-fast --------------------------------------------------

def test_secret_key_fail_fast_when_hosted():
    from app.config import Settings

    for weak in ("", "dev-insecure-change-me", "change-me-in-production"):
        with pytest.raises(Exception):
            Settings(secret_key=weak, deployment="hosted")


def test_secret_key_ephemeral_for_selfhost():
    from app.config import Settings

    s = Settings(secret_key="", deployment="selfhost")
    assert len(s.secret_key) == 64 and s.secret_key not in ("", "dev-insecure-change-me")


# --- P0: JWT algorithm pinned to HS256 ----------------------------------------

def test_jwt_roundtrip_hs256():
    from app import security

    tok = security.create_access_token("acc", "org", "free")
    claims = security.decode_access_token(tok)
    assert claims and claims["sub"] == "acc"


# --- P1: /api/v1/runs no longer leaks across tenants --------------------------

def test_runs_requires_authentication(client):
    anon = TestClient(client.app)
    assert anon.get("/api/v1/runs").status_code == 401


# --- P1: academic entitlement requires a proven email -------------------------

def test_academic_plan_requires_verified_email(client):
    reg = _register(client, "prof@cern.ch")
    # Registered but unverified: still on the free plan, no academic grant.
    assert client.get("/api/v1/me").json()["plan"] == "free"
    # A bogus/again-unsigned token is rejected.
    assert client.post("/api/v1/auth/verify-email", json={"token": "nope"}).status_code == 400
    # The real signed token grants academic.
    from app.security import create_email_verification_token

    tok = create_email_verification_token(reg["account_id"], "prof@cern.ch")
    r = client.post("/api/v1/auth/verify-email", json={"token": tok})
    assert r.status_code == 200 and r.json()["academic_verified"] is True
    assert client.get("/api/v1/me").json()["plan"] == "academic"


# --- P1: API-key minting requires the manage role -----------------------------

def test_api_key_mint_requires_manage_role():
    from app.rbac import can

    assert can(org_role="member", ws_role="viewer", action="manage") is False
    assert can(org_role="owner", ws_role=None, action="manage") is True
