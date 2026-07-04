"""Surgically refresh ONLY the public corpus (leaderboard data).

Unlike ``seed_real.py`` (which wipes runs/cards/attestations and reseeds), this
touches just ``corpus_snapshots`` — safe to run against production without
disturbing the live ledger, result cards or attestations. ``corpus_snapshots``
is not an immutable table, so a plain DELETE needs no trigger surgery.

Run against a DB via QL_DATABASE_URL (e.g. a `fly proxy` tunnel to prod):
    QL_DATABASE_URL=postgresql+psycopg://... python scripts/refresh_corpus.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import text

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "server"))
sys.path.insert(0, str(REPO / "scripts"))

from app.db import SessionLocal, engine  # noqa: E402
import seed_real as sr  # noqa: E402


def main() -> None:
    eng = engine()
    with eng.begin() as conn:
        before = conn.execute(text("SELECT count(*) FROM corpus_snapshots")).scalar()
        conn.execute(text("DELETE FROM corpus_snapshots"))
        print(f"deleted {before} existing corpus_snapshots")
    s = SessionLocal()
    counts = sr.load_corpus(s)
    total = s.execute(text("SELECT count(*) FROM corpus_snapshots")).scalar()
    providers = s.execute(text(
        "SELECT provider, count(*) FROM corpus_snapshots GROUP BY provider ORDER BY provider")).all()
    s.close()
    print(f"loaded: {counts}")
    print(f"corpus_snapshots now: {total}")
    print("by provider:", {p: c for p, c in providers})


if __name__ == "__main__":
    main()
