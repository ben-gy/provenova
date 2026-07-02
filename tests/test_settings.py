"""Account settings: profile, password, API keys, and TOTP MFA login flow."""

from __future__ import annotations

import re

import pyotp
import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


def _register(client, email, password):
    r = client.post("/api/v1/auth/register", json={"email": email, "password": password})
    assert r.status_code == 200, r.text


def test_settings_page_requires_login(client):
    fresh = TestClient(client.app)
    r = fresh.get("/app/settings", follow_redirects=False)
    assert r.status_code in (302, 303)
    assert r.headers["location"] == "/login"


def test_profile_and_password(client):
    _register(client, "settings-user@lab.example", "origpass1")
    # change display name + email
    r = client.post("/app/settings/profile",
                    data={"display_name": "Renamed", "email": "settings-user2@lab.example"})
    assert r.status_code == 200 and "Profile updated" in r.text
    # wrong current password rejected
    r = client.post("/app/settings/password",
                    data={"current_password": "nope", "new_password": "newpass12"})
    assert "incorrect" in r.text
    # correct change
    r = client.post("/app/settings/password",
                    data={"current_password": "origpass1", "new_password": "newpass12"})
    assert "Password changed" in r.text
    # new password works, old doesn't (via API login on a fresh client)
    fresh = TestClient(client.app)
    assert fresh.post("/api/v1/auth/login",
                      json={"email": "settings-user2@lab.example", "password": "origpass1"}).status_code == 401
    assert fresh.post("/api/v1/auth/login",
                      json={"email": "settings-user2@lab.example", "password": "newpass12"}).status_code == 200


def test_api_key_lifecycle(client):
    r = client.post("/app/settings/api-keys", data={"name": "ci"})
    assert r.status_code == 200
    m = re.search(r"ql_live_[A-Za-z0-9_\-]+", r.text)
    assert m, "new key not shown"
    # it appears in the list (by prefix) and can be revoked
    assert "Revoke" in r.text


def test_totp_mfa_enrollment_and_login(client):
    mfa_client = TestClient(client.app)
    _register(mfa_client, "mfa-user@lab.example", "mfapass12")

    # begin setup -> the secret is shown for manual entry
    r = mfa_client.get("/app/settings/mfa/setup")
    assert r.status_code == 200
    secret = re.search(r"[A-Z2-7]{32}", r.text)
    assert secret, "no TOTP secret rendered"
    secret = secret.group(0)

    # wrong code rejected
    bad = mfa_client.post("/app/settings/mfa/enable", data={"code": "000000"})
    assert "didn't match" in bad.text or "didn" in bad.text

    # correct code enables MFA
    code = pyotp.TOTP(secret).now()
    ok = mfa_client.post("/app/settings/mfa/enable", data={"code": code})
    assert "enabled" in ok.text.lower()

    # now a fresh login is challenged for a code
    login_client = TestClient(client.app)
    r = login_client.post("/login", data={"email": "mfa-user@lab.example", "password": "mfapass12"},
                          follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login/mfa"
    # not yet authenticated
    assert login_client.get("/api/v1/me").json()["authenticated"] is False
    # submit a valid code -> authenticated
    code2 = pyotp.TOTP(secret).now()
    r = login_client.post("/login/mfa", data={"code": code2}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert login_client.get("/api/v1/me").json()["authenticated"] is True

    # disable requires the account password
    assert "incorrect" in mfa_client.post("/app/settings/mfa/disable", data={"password": "wrong"}).text.lower() \
        or "Password incorrect" in mfa_client.post("/app/settings/mfa/disable", data={"password": "wrong"}).text
    off = mfa_client.post("/app/settings/mfa/disable", data={"password": "mfapass12"})
    assert "disabled" in off.text.lower()
