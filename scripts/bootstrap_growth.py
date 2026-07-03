"""One-time growth-pipeline bootstrap: research bot + scoped API key.

Creates (idempotently) the research bot account/org/workspace and mints a
``growth``-scoped ``ql_live_*`` API key for the scheduled routine. The full
key is printed ONCE — store it as the routine's secret immediately.

Run against a database via QL_DATABASE_URL (e.g. a `fly proxy` tunnel to prod):
    QL_DATABASE_URL=postgresql+psycopg://... python scripts/bootstrap_growth.py

Re-running mints an ADDITIONAL key (rotate by revoking the old one in the DB:
UPDATE api_keys SET revoked = true WHERE name = 'growth-routine' AND ...).
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "server"))

from app.db import SessionLocal, engine  # noqa: E402
from app.security import generate_api_key  # noqa: E402
from app.services.growth import ensure_research_bot  # noqa: E402

from provenova_core.models import ApiKey  # noqa: E402


def main() -> None:
    engine()
    s = SessionLocal()
    acc, org, ws = ensure_research_bot(s)
    s.commit()
    print(f"bot account : {acc.email} ({acc.id})")
    print(f"bot org     : {org.slug} ({org.id}) plan={org.plan}")
    print(f"bot ws      : {ws.slug} ({ws.id})")

    full, prefix, key_hash = generate_api_key()
    s.add(ApiKey(org_id=org.id, account_id=acc.id, name="growth-routine",
                 prefix=prefix, key_hash=key_hash, scopes=["growth"]))
    s.commit()
    s.close()
    print("\ngrowth API key (store NOW — never shown again):")
    print(f"  {full}")
    print("\nUse as:  Authorization: Bearer <key>   against /api/v1/growth/*")


if __name__ == "__main__":
    main()
