"""Attestation / signing subsystem.

Ed25519 detached signatures over RFC 8785-style canonical JSON statements that
bind a workspace's compliance evidence (a Merkle root over evidence entries) to
a point in time. Public verification material is published as a JWKS.
"""

from __future__ import annotations

from .keys import (
    KeyRegistry,
    compute_kid,
    generate_private_key,
    jwks_from_keys,
    kid_for_key,
    load_or_create_private_key,
    public_jwk,
    public_key_from_jwk,
)
from .signing import (
    ALG,
    STATEMENT_TYPE,
    build_statement,
    create_attestation,
    envelope_from_attestation,
    evidence_merkle_root,
    jcs_canonical,
    revoke_attestation,
    sign_statement,
    verify_attestation,
    verify_envelope,
)

__all__ = [
    # keys
    "KeyRegistry",
    "compute_kid",
    "kid_for_key",
    "generate_private_key",
    "load_or_create_private_key",
    "public_jwk",
    "jwks_from_keys",
    "public_key_from_jwk",
    # signing
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
