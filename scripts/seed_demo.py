"""Seed a demo dataset so the whole platform is walkable end-to-end.

Delegates the dataset build to ``app.services.demo_seed.seed_workspace`` (the
same code path the in-app "Load demo data" button uses), then mints a demo API
key for ``ql push`` and prints how to log in.

Run:  PYTHONPATH=server .venv/bin/python scripts/seed_demo.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import select

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "server"))

from app.db import SessionLocal, bootstrap, default_workspace  # noqa: E402
from app.security import generate_api_key  # noqa: E402
from app.services.demo_seed import seed_workspace  # noqa: E402

from provenova_core.models import Account, ApiKey, OrgMembership  # noqa: E402


def main() -> None:
    s = SessionLocal()
    bootstrap(s)
    ws = default_workspace(s)
    ws.retention_mode = "raw_shots"
    s.commit()
    print(f"workspace: {ws.slug} ({ws.id})")

    summary = seed_workspace(s, ws)
    s.commit()

    print(f"recorded runs: {summary['runs']}")
    if summary["reproduction"]:
        print(f"reproduce verdict: {summary['reproduction']['verdict']} "
              f"HF={summary['reproduction']['score']:.4f}")
    if summary["card"]:
        print(f"published card: /cards/{summary['card']}")
    print(f"corpus snapshots ingested: {summary['corpus']}")
    for fw in summary["frameworks"]:
        print(f"framework {fw['key']}: {fw['status']}")
    if summary["attestation"]:
        print(f"attestation: {summary['attestation']}  "
              f"verify: /api/v1/attestations/{summary['attestation']}/verify")

    # mint a demo API key for `ql push`
    admin = s.scalar(select(Account).where(Account.is_superadmin.is_(True)))
    mem = s.scalar(select(OrgMembership).where(OrgMembership.account_id == admin.id))
    full, prefix, key_hash = generate_api_key()
    s.add(ApiKey(org_id=mem.org_id, account_id=admin.id, name="demo", prefix=prefix, key_hash=key_hash))
    s.commit()
    print("\n=== DEMO READY ===")
    print(f"admin login: {admin.email}  (set a password via /register or the API for real login)")
    print(f"demo API key for `ql login --token`:\n  {full}")
    s.close()


if __name__ == "__main__":
    main()
