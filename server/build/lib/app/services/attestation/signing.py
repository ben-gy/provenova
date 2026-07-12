"""Attestation statements: build, sign (Ed25519 over canonical JSON), verify.

Signing scheme (kept deliberately standard):

* The *statement* is a plain JSON object describing what was attested.
* We serialize it with RFC 8785-style canonical JSON (via ql-core's
  :func:`provenova_core.hashing.canonical_bytes`) so any verifier — on
  SQLite, Postgres, or offline — reconstructs byte-identical signing input.
* We take a detached Ed25519 signature over those bytes and wrap it in an
  envelope ``{"statement", "signature", "kid", "alg": "EdDSA"}``.

An attestation's evidence set is a Merkle root over the individual evidence
entries; each entry pins the *content hash* of the underlying record (a Run's
``run_hash``, a Result's ``counts_sha256``, ...). Verification therefore has two
layers: (1) the signature/expiry/revocation checks over the signed statement,
and (2) a tamper cross-check that the referenced records' *current* content
hashes still match what was signed. Mutating a referenced record breaks (2)
even though the signature over the old statement is still cryptographically
valid.
"""

from __future__ import annotations

import base64
import datetime as _dt
from typing import Any, Iterable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import ed25519

from provenova_core import hashing
from provenova_core.models import (
    Attestation,
    ComplianceFramework,
    EvidenceItem,
    Workspace,
)

from .keys import _b64url, _b64url_decode, kid_for_key, public_key_from_jwk

ALG = "EdDSA"
STATEMENT_TYPE = "qlattest/statement/1.0"


# ---------------------------------------------------------------------------
# Canonical JSON + Merkle helpers
# ---------------------------------------------------------------------------

def jcs_canonical(obj: Any) -> bytes:
    """RFC 8785-style canonical JSON bytes (sorted keys, no whitespace, UTF-8).

    Thin re-export of ql-core's :func:`canonical_bytes` so signing input is
    byte-identical to everything else hashed in the ledger.
    """
    return hashing.canonical_bytes(obj)


def evidence_merkle_root(evidence_entries: list[dict]) -> str:
    """Merkle root over a list of evidence entry dicts.

    Each entry is hashed with :func:`sha256_hex` (canonical JSON), then combined
    via :func:`merkle_root`. Order-preserving: entries are hashed in the order
    supplied, so callers must sort deterministically before signing.
    """
    leaves = [hashing.sha256_hex(entry) for entry in evidence_entries]
    return hashing.merkle_root(leaves)


def _entry_from_evidence_item(item: EvidenceItem) -> dict:
    """Canonical evidence entry derived from a stored EvidenceItem row."""
    return {
        "source_ref_type": item.source_ref_type,
        "source_ref_id": item.source_ref_id,
        "source_content_hash": item.source_content_hash,
        "value": item.value,
    }


def _sort_entries(entries: list[dict]) -> list[dict]:
    """Deterministic evidence ordering, independent of insertion order."""
    return sorted(
        entries,
        key=lambda e: (
            str(e.get("source_ref_type") or ""),
            str(e.get("source_ref_id") or ""),
        ),
    )


# ---------------------------------------------------------------------------
# Statement build / sign / verify
# ---------------------------------------------------------------------------

def _iso(dt: _dt.datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt.astimezone(_dt.timezone.utc).isoformat()


def build_statement(
    subject: dict,
    evidence_root: str,
    framework_ids: list[str],
    issued_at: _dt.datetime,
    expires_at: _dt.datetime | None,
    issuer_org: str | None,
    nonce: str,
    revocation_url: str | None,
) -> dict:
    """Assemble the (unsigned) attestation statement."""
    return {
        "type": STATEMENT_TYPE,
        "subject": subject,
        "evidence_root": evidence_root,
        "framework_ids": sorted(framework_ids),
        "issued_at": _iso(issued_at),
        "expires_at": _iso(expires_at),
        "issuer_org": issuer_org,
        "nonce": nonce,
        "revocation_url": revocation_url,
    }


def sign_statement(
    statement: dict,
    private_key: ed25519.Ed25519PrivateKey,
    kid: str | None = None,
) -> dict:
    """Detached Ed25519 signature over ``jcs_canonical(statement)``.

    Returns a signed envelope. ``kid`` defaults to the content-derived id for
    the given key.
    """
    sig = private_key.sign(jcs_canonical(statement))
    return {
        "statement": statement,
        "signature": _b64url(sig),
        "kid": kid or kid_for_key(private_key),
        "alg": ALG,
    }


def _find_jwk(jwks: dict, kid: str) -> dict | None:
    for jwk in jwks.get("keys", []):
        if jwk.get("kid") == kid:
            return jwk
    return None


def verify_envelope(
    envelope: dict,
    jwks: dict,
    *,
    revoked_kids_or_ids: set[str] | None = None,
    now: _dt.datetime | None = None,
) -> dict:
    """Verify a signed envelope: signature, expiry, revocation.

    Returns ``{"valid", "checks": {"signature", "expired", "revoked"}, "reason"}``.
    ``valid`` is True only when the signature verifies, the statement is not
    expired, and neither the ``kid`` nor the statement ``nonce`` appears in
    ``revoked_kids_or_ids``.
    """
    revoked_kids_or_ids = revoked_kids_or_ids or set()
    now = now or _dt.datetime.now(_dt.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=_dt.timezone.utc)

    checks = {"signature": False, "expired": False, "revoked": False}
    reason: str | None = None

    statement = envelope.get("statement")
    kid = envelope.get("kid")
    sig_b64 = envelope.get("signature")

    # --- signature ---
    if envelope.get("alg") != ALG:
        return {"valid": False, "checks": checks, "reason": f"unsupported alg {envelope.get('alg')!r}"}
    jwk = _find_jwk(jwks, kid)
    if jwk is None:
        return {"valid": False, "checks": checks, "reason": f"no public key for kid {kid!r}"}
    try:
        pub = public_key_from_jwk(jwk)
        pub.verify(_b64url_decode(sig_b64), jcs_canonical(statement))
        checks["signature"] = True
    except (InvalidSignature, ValueError, TypeError, base64.binascii.Error) as exc:  # type: ignore[attr-defined]
        return {"valid": False, "checks": checks, "reason": f"signature invalid: {exc}"}

    # --- expiry ---
    expires_at = statement.get("expires_at")
    if expires_at:
        exp_dt = _dt.datetime.fromisoformat(expires_at)
        if exp_dt.tzinfo is None:
            exp_dt = exp_dt.replace(tzinfo=_dt.timezone.utc)
        if now > exp_dt:
            checks["expired"] = True
            reason = "attestation expired"

    # --- revocation (by kid or by statement nonce/id) ---
    nonce = statement.get("nonce")
    if kid in revoked_kids_or_ids or (nonce is not None and nonce in revoked_kids_or_ids):
        checks["revoked"] = True
        reason = reason or "attestation revoked"

    valid = checks["signature"] and not checks["expired"] and not checks["revoked"]
    if valid:
        reason = None
    return {"valid": valid, "checks": checks, "reason": reason}


# ---------------------------------------------------------------------------
# ORM-level create / verify / revoke
# ---------------------------------------------------------------------------

def create_attestation(
    session,
    *,
    workspace: Workspace,
    framework: ComplianceFramework | None,
    subject_type: str,
    subject_id: str | None,
    evidence_items: Iterable[EvidenceItem],
    private_key: ed25519.Ed25519PrivateKey,
    kid: str,
    ttl_days: int = 365,
    issuer_org: str | None = None,
    revocation_url: str | None = None,
    now: _dt.datetime | None = None,
) -> Attestation:
    """Build the evidence entries, Merkle root, signed statement and persist it."""
    now = now or _dt.datetime.now(_dt.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=_dt.timezone.utc)
    expires_at = now + _dt.timedelta(days=ttl_days)

    entries = _sort_entries([_entry_from_evidence_item(i) for i in evidence_items])
    evidence_root = evidence_merkle_root(entries)

    subject = {
        "workspace_id": workspace.id,
        "subject_type": subject_type,
        "subject_id": subject_id,
    }
    framework_ids = [framework.id] if framework is not None else []
    nonce = _gen_nonce()

    statement = build_statement(
        subject=subject,
        evidence_root=evidence_root,
        framework_ids=framework_ids,
        issued_at=now,
        expires_at=expires_at,
        issuer_org=issuer_org or (workspace.org_id if workspace else None),
        nonce=nonce,
        revocation_url=revocation_url,
    )
    envelope = sign_statement(statement, private_key, kid)

    satisfied_state = {"evidence_entries": entries, "framework_ids": framework_ids}
    attestation = Attestation(
        workspace_id=workspace.id,
        framework_id=framework.id if framework is not None else None,
        subject_type=subject_type,
        subject_id=subject_id,
        satisfied_state=satisfied_state,
        evidence_root=evidence_root,
        statement=statement,
        signature=envelope["signature"],
        kid=kid,
        point_in_time=now,
        expires_at=expires_at,
        revoked=False,
        attestation_sha256=hashing.sha256_hex(envelope),
    )
    session.add(attestation)
    session.flush()
    return attestation


def _gen_nonce() -> str:
    import secrets

    return secrets.token_hex(16)


def envelope_from_attestation(attestation: Attestation) -> dict:
    """Reconstruct the signed envelope from a stored Attestation row."""
    return {
        "statement": attestation.statement,
        "signature": attestation.signature,
        "kid": attestation.kid,
        "alg": ALG,
    }


def verify_attestation(
    session,
    attestation: Attestation,
    jwks: dict,
    *,
    revoked_ids: set[str] | None = None,
    live_content_hashes: dict[str, str] | None = None,
    now: _dt.datetime | None = None,
) -> dict:
    """Verify a stored attestation end to end.

    Layer 1: re-derive the envelope from the stored statement/signature and run
    :func:`verify_envelope` (signature, expiry, revocation). The row's own
    ``revoked`` flag and its ``id`` are folded into the revocation set.

    Layer 2 (tamper): recompute the evidence Merkle root from the stored
    evidence entries and confirm it matches the signed ``evidence_root``; then
    cross-check each entry's ``source_content_hash`` against the *current* hash
    of the referenced record. ``live_content_hashes`` maps ``source_ref_id`` ->
    current content hash (as read live from the DB). Any mismatch, or a Merkle
    root that no longer matches, marks the attestation invalid.

    Returns the ``verify_envelope`` shape extended with a ``"tampered"`` check.
    """
    revoked_ids = set(revoked_ids or set())
    if attestation.revoked:
        revoked_ids.add(attestation.statement.get("nonce"))
        revoked_ids.add(attestation.id)

    envelope = envelope_from_attestation(attestation)
    result = verify_envelope(
        envelope, jwks, revoked_kids_or_ids=revoked_ids, now=now
    )
    result["checks"]["tampered"] = False

    entries = (attestation.satisfied_state or {}).get("evidence_entries", [])
    # (a) the signed evidence_root must still describe these entries
    recomputed_root = evidence_merkle_root(entries)
    if recomputed_root != attestation.statement.get("evidence_root"):
        result["checks"]["tampered"] = True
        result["reason"] = result["reason"] or "evidence root mismatch"

    # (b) each referenced record's live content hash must match what was signed
    if live_content_hashes is not None:
        for entry in entries:
            ref_id = entry.get("source_ref_id")
            signed_hash = entry.get("source_content_hash")
            live_hash = live_content_hashes.get(ref_id)
            if live_hash is not None and live_hash != signed_hash:
                result["checks"]["tampered"] = True
                result["reason"] = result["reason"] or (
                    f"referenced record {ref_id} content hash changed"
                )
                break

    if result["checks"]["tampered"]:
        result["valid"] = False
    return result


def revoke_attestation(session, attestation: Attestation) -> Attestation:
    """Mark an attestation revoked and persist."""
    attestation.revoked = True
    session.add(attestation)
    session.flush()
    return attestation


__all__ = [
    "ALG",
    "STATEMENT_TYPE",
    "jcs_canonical",
    "evidence_merkle_root",
    "build_statement",
    "sign_statement",
    "verify_envelope",
    "create_attestation",
    "verify_attestation",
    "revoke_attestation",
    "envelope_from_attestation",
]
