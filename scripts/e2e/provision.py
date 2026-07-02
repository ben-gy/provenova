"""E2E provisioning step (runs before the server starts).

Bootstraps a fresh, isolated database, sets a known password on the bootstrap
superadmin (whose org is on the Enterprise plan), and seeds a fully walkable
dataset (runs, a reproduction, a published card, the public corpus, evaluated
frameworks + a signed attestation) so the harness can exercise every layer.

Reads the same ``QL_*`` env as the server (QL_DATABASE_URL,
QL_ATTESTATION_KEY_PATH, QL_ADMIN_EMAIL, QL_E2E_ADMIN_PASSWORD).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "server"))

from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal, bootstrap, default_workspace  # noqa: E402
from app.security import hash_password  # noqa: E402
from app.services.demo_seed import is_empty, seed_workspace  # noqa: E402
from quantumledger_core.models import Account  # noqa: E402

PASSWORD = os.environ.get("QL_E2E_ADMIN_PASSWORD", "e2e-pass-123456")


def main() -> None:
    s = SessionLocal()
    bootstrap(s)
    admin = s.scalar(select(Account).where(Account.is_superadmin.is_(True)))
    if admin is None:
        raise SystemExit("provision: bootstrap did not create a superadmin")
    admin.password_hash = hash_password(PASSWORD)
    s.commit()

    ws = default_workspace(s)
    if ws is not None and is_empty(s, ws):
        summary = seed_workspace(s, ws)
        s.commit()
        print(f"provision: seeded workspace '{ws.slug}' — runs={summary.get('runs')} "
              f"card={summary.get('card')} frameworks={[f['key'] for f in summary.get('frameworks', [])]} "
              f"attestation={summary.get('attestation')}")
    print(f"provision: admin ready — {admin.email}")
    s.close()


if __name__ == "__main__":
    main()
