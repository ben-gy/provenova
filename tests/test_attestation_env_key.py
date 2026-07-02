"""Env-loaded attestation signing key: base64 round-trip + sign/verify cycle.

Covers QL_ATTESTATION_KEY_B64 support: a stable Ed25519 signing key carried in
an env var (base64 of a PKCS8 PEM) instead of the on-disk key file, so redeploys
on ephemeral filesystems don't regenerate the key and invalidate prior
attestations.

Tests the keys.py helpers + signing directly (no reliance on the get_settings
lru_cache); the one test that exercises app.db.attestation_key() clears the
relevant caches and restores the env afterward. Fixed timestamps keep it clock-
independent.
"""

from __future__ import annotations

import base64
import datetime as _dt

import pytest
from cryptography.hazmat.primitives.asymmetric import ec, ed25519

from app.services.attestation import (
    build_statement,
    evidence_merkle_root,
    generate_private_key,
    jwks_from_keys,
    kid_for_key,
    private_key_from_b64,
    private_key_to_b64,
    sign_statement,
    verify_envelope,
)

UTC = _dt.timezone.utc


def test_b64_round_trip_preserves_kid():
    """to_b64 -> from_b64 yields the same key material and a stable kid."""
    original = generate_private_key()
    original_kid = kid_for_key(original)

    b64 = private_key_to_b64(original)
    assert isinstance(b64, str)
    # It really is base64 (decodes cleanly to PEM).
    pem = base64.b64decode(b64)
    assert pem.startswith(b"-----BEGIN PRIVATE KEY-----")

    loaded, loaded_kid = private_key_from_b64(b64)
    assert isinstance(loaded, ed25519.Ed25519PrivateKey)

    # kid is content-derived, so it must be stable across the round-trip and
    # match kid_for_key of the original key.
    assert loaded_kid == original_kid
    assert kid_for_key(loaded) == original_kid

    # Same private scalar -> identical raw private bytes.
    from cryptography.hazmat.primitives import serialization

    def raw_priv(k: ed25519.Ed25519PrivateKey) -> bytes:
        return k.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )

    assert raw_priv(loaded) == raw_priv(original)


def test_sign_verify_cycle_with_env_loaded_key():
    """A key round-tripped through base64 signs statements that verify."""
    b64 = private_key_to_b64(generate_private_key())
    priv, kid = private_key_from_b64(b64)
    jwks = jwks_from_keys([priv])

    issued = _dt.datetime(2026, 1, 1, tzinfo=UTC)
    statement = build_statement(
        subject={"workspace_id": "W", "subject_type": "workspace", "subject_id": None},
        evidence_root=evidence_merkle_root([{"x": 1}]),
        framework_ids=["soc2"],
        issued_at=issued,
        expires_at=issued + _dt.timedelta(days=30),
        issuer_org="org",
        nonce="nonce-env-1",
        revocation_url=None,
    )
    envelope = sign_statement(statement, priv, kid)
    assert envelope["kid"] == kid
    assert envelope["alg"] == "EdDSA"

    res = verify_envelope(envelope, jwks, now=issued + _dt.timedelta(hours=1))
    assert res["valid"] is True, res
    assert res["checks"]["signature"] is True
    assert res["checks"]["expired"] is False
    assert res["checks"]["revoked"] is False
    assert res["reason"] is None


def test_from_b64_rejects_non_ed25519_key():
    """A non-Ed25519 PEM (base64) is rejected with a clear error."""
    from cryptography.hazmat.primitives import serialization

    ec_key = ec.generate_private_key(ec.SECP256R1())
    pem = ec_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    b64 = base64.b64encode(pem).decode("ascii")
    with pytest.raises(ValueError):
        private_key_from_b64(b64)


def test_attestation_key_prefers_env_b64(monkeypatch):
    """app.db.attestation_key() uses QL_ATTESTATION_KEY_B64 when set.

    Exercises the real cached accessor: clears both the settings and the
    attestation-key lru_caches, sets the env var, and asserts the returned
    (priv, kid, jwks) match the env-supplied key. Caches are cleared again on
    teardown so other tests see a fresh accessor.
    """
    from app import db
    from app.config import get_settings

    expected_key = generate_private_key()
    expected_b64 = private_key_to_b64(expected_key)
    expected_kid = kid_for_key(expected_key)

    monkeypatch.setenv("QL_ATTESTATION_KEY_B64", expected_b64)
    get_settings.cache_clear()
    db.attestation_key.cache_clear()

    try:
        priv, kid, jwks = db.attestation_key()
        assert kid == expected_kid
        assert jwks["keys"][0]["kid"] == expected_kid

        # The returned key actually verifies a signature it produces, via the
        # returned JWKS -> full env-key sign/verify path.
        issued = _dt.datetime(2026, 1, 1, tzinfo=UTC)
        statement = build_statement(
            subject={},
            evidence_root="r",
            framework_ids=[],
            issued_at=issued,
            expires_at=None,
            issuer_org=None,
            nonce="n-db",
            revocation_url=None,
        )
        envelope = sign_statement(statement, priv, kid)
        res = verify_envelope(envelope, jwks, now=issued)
        assert res["valid"] is True, res
    finally:
        # Restore a clean accessor for the rest of the suite.
        db.attestation_key.cache_clear()
        get_settings.cache_clear()
