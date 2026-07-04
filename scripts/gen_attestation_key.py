"""Generate a fresh Ed25519 attestation signing key for Provenova.

Prints a ready-to-paste ``QL_ATTESTATION_KEY_B64=<base64>`` line plus the
derived ``kid``. When set in the environment (or the deploy ``.env``), the
server loads its attestation signing key from this value instead of reading /
creating the on-disk key file. That keeps the signing key — and therefore the
attestation trust root — stable across redeploys on ephemeral filesystems; a
regenerated key would invalidate every previously issued attestation.

The base64 is standard base64 of a PKCS8 PEM (unencrypted) Ed25519 private key.

Treat the output as a secret: it is the private signing key. Only its public
half (published as JWKS) is ever needed by verifiers.

Run:  PYTHONPATH=server .venv/bin/python scripts/gen_attestation_key.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "server"))

from app.services.attestation import (  # noqa: E402
    generate_private_key,
    kid_for_key,
    private_key_to_b64,
)


def main() -> None:
    key = generate_private_key()
    b64 = private_key_to_b64(key)
    kid = kid_for_key(key)

    print(f"QL_ATTESTATION_KEY_B64={b64}")
    print(f"# kid={kid}")
    print(
        "# Store this as a secret. Set QL_ATTESTATION_KEY_B64 in your deploy env "
        "to pin the attestation signing key across redeploys.",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
