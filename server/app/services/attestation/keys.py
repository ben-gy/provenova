"""Ed25519 keypair management for attestation signing.

An attestation is a detached Ed25519 signature over the canonical JSON of a
compliance *statement*. The signing key lives on disk (0600) so a self-hosted
deployment owns its own trust root; verifiers only ever need the public JWKS.

The ``kid`` (key id) is a stable, content-derived label: the first 16 hex chars
of ``SHA-256(public_key_raw_bytes)``. Because it is derived from the key itself
it is reproducible offline and lets a verifier select the right public key from
a JWKS even after key rotation. We keep a small in-memory registry so multiple
retired public keys can still verify historical attestations.
"""

from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519


def _b64url(data: bytes) -> str:
    """Base64url without padding (JOSE / RFC 7515 style)."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def public_raw_bytes(private_key: ed25519.Ed25519PrivateKey) -> bytes:
    """Raw 32-byte Ed25519 public key."""
    return private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def compute_kid(public_raw: bytes) -> str:
    """Stable key id: first 16 hex chars of SHA-256 over the raw public key."""
    return hashlib.sha256(public_raw).hexdigest()[:16]


def kid_for_key(private_key: ed25519.Ed25519PrivateKey) -> str:
    return compute_kid(public_raw_bytes(private_key))


def generate_private_key() -> ed25519.Ed25519PrivateKey:
    return ed25519.Ed25519PrivateKey.generate()


def load_or_create_private_key(
    path: str | os.PathLike[str],
) -> tuple[ed25519.Ed25519PrivateKey, str]:
    """Load an Ed25519 private key from ``path`` or create one if missing.

    Creates the parent directory and writes the PEM with 0600 permissions when
    generating. Returns ``(private_key, kid)``.
    """
    p = Path(path)
    if p.exists():
        private_key = serialization.load_pem_private_key(
            p.read_bytes(), password=None
        )
        if not isinstance(private_key, ed25519.Ed25519PrivateKey):
            raise ValueError(f"{p} is not an Ed25519 private key")
    else:
        p.parent.mkdir(parents=True, exist_ok=True)
        private_key = generate_private_key()
        pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        # Create with restrictive perms atomically, then write.
        fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, pem)
        finally:
            os.close(fd)
        os.chmod(p, 0o600)
    return private_key, kid_for_key(private_key)


def public_jwk(private_key: ed25519.Ed25519PrivateKey, kid: str | None = None) -> dict:
    """A single JWK (OKP/Ed25519) for the given key's public half."""
    raw = public_raw_bytes(private_key)
    return {
        "kty": "OKP",
        "crv": "Ed25519",
        "kid": kid or compute_kid(raw),
        "x": _b64url(raw),
    }


def jwks_from_keys(private_keys: list[ed25519.Ed25519PrivateKey]) -> dict:
    """Build a ``{"keys":[...]}`` JWKS from a list of private keys."""
    return {"keys": [public_jwk(k) for k in private_keys]}


def public_key_from_jwk(jwk: dict) -> ed25519.Ed25519PublicKey:
    """Reconstruct an Ed25519 public key object from a JWK entry."""
    if jwk.get("kty") != "OKP" or jwk.get("crv") != "Ed25519":
        raise ValueError("JWK is not an Ed25519 OKP key")
    return ed25519.Ed25519PublicKey.from_public_bytes(_b64url_decode(jwk["x"]))


class KeyRegistry:
    """A small registry of public keys keyed by ``kid`` for verification.

    Supports multiple retained keys so historical attestations signed by a
    rotated-out key still verify. Also carries the current signing key.
    """

    def __init__(self) -> None:
        self._public: dict[str, ed25519.Ed25519PublicKey] = {}
        self._signing_kid: str | None = None
        self._signing_key: ed25519.Ed25519PrivateKey | None = None

    def add_private_key(
        self, private_key: ed25519.Ed25519PrivateKey, *, make_current: bool = True
    ) -> str:
        kid = kid_for_key(private_key)
        self._public[kid] = private_key.public_key()
        if make_current or self._signing_key is None:
            self._signing_key = private_key
            self._signing_kid = kid
        return kid

    def add_public_jwk(self, jwk: dict) -> str:
        kid = jwk["kid"]
        self._public[kid] = public_key_from_jwk(jwk)
        return kid

    def load_jwks(self, jwks: dict) -> None:
        for jwk in jwks.get("keys", []):
            self.add_public_jwk(jwk)

    @property
    def signing_key(self) -> ed25519.Ed25519PrivateKey:
        if self._signing_key is None:
            raise ValueError("no signing key registered")
        return self._signing_key

    @property
    def signing_kid(self) -> str:
        if self._signing_kid is None:
            raise ValueError("no signing key registered")
        return self._signing_kid

    def public_key(self, kid: str) -> ed25519.Ed25519PublicKey | None:
        return self._public.get(kid)

    def jwks(self) -> dict:
        keys = []
        for kid, pub in self._public.items():
            raw = pub.public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
            keys.append(
                {"kty": "OKP", "crv": "Ed25519", "kid": kid, "x": _b64url(raw)}
            )
        return {"keys": keys}


__all__ = [
    "compute_kid",
    "kid_for_key",
    "generate_private_key",
    "load_or_create_private_key",
    "public_raw_bytes",
    "public_jwk",
    "jwks_from_keys",
    "public_key_from_jwk",
    "KeyRegistry",
]
