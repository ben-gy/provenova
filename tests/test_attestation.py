"""Attestation subsystem tests: sign->verify, tamper detection, expiry, revoke,
JWKS round-trip, and key persistence (0600)."""

from __future__ import annotations

import datetime as _dt
import stat

import pytest

import quantumledger_core as qc
from quantumledger_core import hashing
from quantumledger_core.models import (
    Backend,
    CalibrationSnapshot,
    Circuit,
    EvidenceItem,
    Run,
    bootstrap_local,
)

from app.services.attestation import (
    KeyRegistry,
    build_statement,
    create_attestation,
    evidence_merkle_root,
    generate_private_key,
    jcs_canonical,
    jwks_from_keys,
    kid_for_key,
    load_or_create_private_key,
    public_jwk,
    public_key_from_jwk,
    revoke_attestation,
    sign_statement,
    verify_attestation,
    verify_envelope,
)


UTC = _dt.timezone.utc


@pytest.fixture()
def session():
    engine = qc.init_db("sqlite://")  # in-memory
    Session = qc.session_factory(engine)
    with Session() as s:
        yield s


@pytest.fixture()
def key():
    return generate_private_key()


@pytest.fixture()
def kid(key):
    return kid_for_key(key)


@pytest.fixture()
def jwks(key):
    return jwks_from_keys([key])


def _seed_run(session):
    """Create a minimal Run with a run_hash + a Control to attach evidence to."""
    from quantumledger_core.models import Control, ComplianceFramework

    ws = bootstrap_local(session)

    backend = Backend(vendor="sim", name="aer", identity_hash="b" * 64)
    session.add(backend)
    session.flush()
    calib = CalibrationSnapshot(
        backend_id=backend.id,
        content_sha256="c" * 64,
        captured_at=_dt.datetime.now(UTC),
        payload={"t1": 100.0},
    )
    circuit = Circuit(content_sha256="d" * 64, source="OPENQASM 3;", n_qubits=1)
    session.add_all([calib, circuit])
    session.flush()

    run = Run(
        workspace_id=ws.id,
        circuit_id=circuit.id,
        backend_id=backend.id,
        calibration_snapshot_id=calib.id,
        shots=1024,
        status="completed",
        run_hash="a" * 64,
    )
    session.add(run)

    fw = ComplianceFramework(key="soc2", name="SOC 2", version="2017")
    session.add(fw)
    session.flush()
    control = Control(framework_id=fw.id, key="CC1.1", title="Control env")
    session.add(control)
    session.flush()

    return ws, fw, control, run


def _evidence(session, ws, control, run):
    item = EvidenceItem(
        control_id=control.id,
        workspace_id=ws.id,
        rule_id="run-has-hash",
        source_ref_type="run",
        source_ref_id=run.id,
        source_content_hash=run.run_hash,
        value={"status": run.status},
    )
    session.add(item)
    session.flush()
    return item


# ---------------------------------------------------------------------------


def test_jwks_round_trip(key, kid):
    jwks = jwks_from_keys([key])
    assert jwks["keys"][0]["kty"] == "OKP"
    assert jwks["keys"][0]["crv"] == "Ed25519"
    assert jwks["keys"][0]["kid"] == kid

    # Reconstructed public key verifies a signature made by the private key.
    pub = public_key_from_jwk(jwks["keys"][0])
    msg = b"hello canonical world"
    sig = key.sign(msg)
    pub.verify(sig, msg)  # raises if it doesn't round-trip


def test_jcs_canonical_stable():
    a = {"b": 1, "a": [3, 2, 1], "z": {"y": 2, "x": 1}}
    b = {"z": {"x": 1, "y": 2}, "a": [3, 2, 1], "b": 1}
    assert jcs_canonical(a) == jcs_canonical(b)
    assert b" " not in jcs_canonical(a)


def test_sign_then_verify(key, kid, jwks):
    now = _dt.datetime(2026, 1, 1, tzinfo=UTC)
    statement = build_statement(
        subject={"workspace_id": "W", "subject_type": "workspace", "subject_id": None},
        evidence_root=evidence_merkle_root([{"x": 1}]),
        framework_ids=["F"],
        issued_at=now,
        expires_at=now + _dt.timedelta(days=30),
        issuer_org="org",
        nonce="nonce-1",
        revocation_url=None,
    )
    envelope = sign_statement(statement, key, kid)
    assert envelope["alg"] == "EdDSA"
    assert envelope["kid"] == kid

    res = verify_envelope(envelope, jwks, now=now)
    assert res["valid"] is True
    assert res["checks"]["signature"] is True
    assert res["checks"]["expired"] is False
    assert res["checks"]["revoked"] is False
    assert res["reason"] is None


def test_verify_fails_on_wrong_key(kid):
    # Sign with one key, publish a JWKS for a different key -> signature invalid.
    signer = generate_private_key()
    other = generate_private_key()
    now = _dt.datetime(2026, 1, 1, tzinfo=UTC)
    statement = build_statement(
        subject={}, evidence_root="r", framework_ids=[],
        issued_at=now, expires_at=None, issuer_org=None, nonce="n", revocation_url=None,
    )
    env = sign_statement(statement, signer)
    # JWKS lists 'other' but under the signer's kid so lookup succeeds but verify fails.
    fake_jwk = public_jwk(other, kid=env["kid"])
    res = verify_envelope(env, {"keys": [fake_jwk]}, now=now)
    assert res["valid"] is False
    assert res["checks"]["signature"] is False


def test_expiry(key, jwks):
    issued = _dt.datetime(2026, 1, 1, tzinfo=UTC)
    statement = build_statement(
        subject={}, evidence_root="r", framework_ids=[],
        issued_at=issued, expires_at=issued + _dt.timedelta(days=1),
        issuer_org=None, nonce="n", revocation_url=None,
    )
    env = sign_statement(statement, key)

    # Before expiry: valid.
    ok = verify_envelope(env, jwks, now=issued + _dt.timedelta(hours=1))
    assert ok["valid"] is True and ok["checks"]["expired"] is False

    # After expiry: invalid, signature still fine.
    bad = verify_envelope(env, jwks, now=issued + _dt.timedelta(days=2))
    assert bad["valid"] is False
    assert bad["checks"]["signature"] is True
    assert bad["checks"]["expired"] is True
    assert bad["reason"] == "attestation expired"


def test_revocation_by_nonce(key, jwks):
    now = _dt.datetime(2026, 1, 1, tzinfo=UTC)
    statement = build_statement(
        subject={}, evidence_root="r", framework_ids=[],
        issued_at=now, expires_at=None, issuer_org=None, nonce="the-nonce", revocation_url=None,
    )
    env = sign_statement(statement, key)
    res = verify_envelope(env, jwks, revoked_kids_or_ids={"the-nonce"}, now=now)
    assert res["valid"] is False
    assert res["checks"]["revoked"] is True


def test_create_and_verify_attestation(session, key, kid, jwks):
    now = _dt.datetime(2026, 1, 1, tzinfo=UTC)
    ws, fw, control, run = _seed_run(session)
    item = _evidence(session, ws, control, run)

    att = create_attestation(
        session,
        workspace=ws,
        framework=fw,
        subject_type="workspace",
        subject_id=None,
        evidence_items=[item],
        private_key=key,
        kid=kid,
        ttl_days=365,
        now=now,
    )
    assert att.id
    assert att.evidence_root
    assert att.kid == kid

    # Live hash matches the signed one -> valid, no tamper.
    live = {run.id: run.run_hash}
    res = verify_attestation(session, att, jwks, revoked_ids=set(), live_content_hashes=live, now=now)
    assert res["valid"] is True, res
    assert res["checks"]["signature"] is True
    assert res["checks"]["tampered"] is False


def test_tamper_referenced_record(session, key, kid, jwks):
    now = _dt.datetime(2026, 1, 1, tzinfo=UTC)
    ws, fw, control, run = _seed_run(session)
    item = _evidence(session, ws, control, run)

    att = create_attestation(
        session,
        workspace=ws,
        framework=fw,
        subject_type="workspace",
        subject_id=None,
        evidence_items=[item],
        private_key=key,
        kid=kid,
        now=now,
    )

    # Simulate the referenced record mutating: its live content hash now differs
    # from the one captured in the signed evidence set.
    tampered_live = {run.id: "f" * 64}
    res = verify_attestation(
        session, att, jwks, revoked_ids=set(), live_content_hashes=tampered_live, now=now
    )
    assert res["valid"] is False
    # Signature over the *old* statement is still cryptographically valid...
    assert res["checks"]["signature"] is True
    # ...but the cross-check against live records flags tampering.
    assert res["checks"]["tampered"] is True
    assert "content hash changed" in (res["reason"] or "")


def test_revoke_attestation(session, key, kid, jwks):
    now = _dt.datetime(2026, 1, 1, tzinfo=UTC)
    ws, fw, control, run = _seed_run(session)
    item = _evidence(session, ws, control, run)
    att = create_attestation(
        session, workspace=ws, framework=fw, subject_type="workspace",
        subject_id=None, evidence_items=[item], private_key=key, kid=kid, now=now,
    )
    live = {run.id: run.run_hash}

    # Valid before revoke.
    assert verify_attestation(session, att, jwks, revoked_ids=set(), live_content_hashes=live, now=now)["valid"]

    revoke_attestation(session, att)
    assert att.revoked is True

    res = verify_attestation(session, att, jwks, revoked_ids=set(), live_content_hashes=live, now=now)
    assert res["valid"] is False
    assert res["checks"]["revoked"] is True


def test_evidence_merkle_root_matches_core():
    entries = [{"a": 1}, {"b": 2}]
    expected = hashing.merkle_root([hashing.sha256_hex(e) for e in entries])
    assert evidence_merkle_root(entries) == expected


def test_load_or_create_private_key_perms(tmp_path):
    p = tmp_path / "keys" / "signing.pem"
    key1, kid1 = load_or_create_private_key(p)
    assert p.exists()
    mode = stat.S_IMODE(p.stat().st_mode)
    assert mode == 0o600, oct(mode)

    # Reloading yields the same key (same kid).
    key2, kid2 = load_or_create_private_key(p)
    assert kid1 == kid2

    # Registry accepts multiple retained keys for verification.
    reg = KeyRegistry()
    reg.add_private_key(key1)
    other = generate_private_key()
    reg.add_private_key(other, make_current=False)
    assert reg.signing_kid == kid1
    assert reg.public_key(kid1) is not None
    assert reg.public_key(kid_for_key(other)) is not None
    assert len(reg.jwks()["keys"]) == 2
