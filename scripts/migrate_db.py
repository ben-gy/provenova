"""Copy a Provenova database into a fresh SQLite file.

Built for the Fly Postgres -> Cloudflare (SQLite + Litestream) move, but the
source can be any SQLAlchemy URL the models run on. Every row of every mapped
table is copied verbatim (ULIDs, hashes, signatures, timestamps), so run hashes,
the per-workspace chain, attestations, card slugs and DOIs all survive intact.

    python scripts/migrate_db.py SOURCE_URL DEST.db

The destination must not exist. Immutability triggers are installed only after
the copy (they would otherwise reject nothing here, but the order keeps the copy
a plain bulk insert), then SQLite's integrity and foreign-key checks run and
per-table row counts are compared against the source. Exits non-zero on any
mismatch.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import create_engine, func, inspect, select

import provenova_core as qc
from provenova_core.immutability import install_immutability
from provenova_core.models import Base, Run
from provenova_core.provenance import build_run_doc

BATCH = 500


def migrate(source_url: str, dest_path: Path, *, log=print) -> dict[str, int]:
    if dest_path.exists():
        raise SystemExit(f"refusing to overwrite existing {dest_path}")
    src = create_engine(source_url, future=True)
    dst = qc.init_db(f"sqlite:///{dest_path}", immutability=False)

    src_insp = inspect(src)
    src_tables = set(src_insp.get_table_names())
    mapped = {t.name for t in Base.metadata.sorted_tables}
    unmapped = sorted(src_tables - mapped - {"alembic_version"})
    if unmapped:
        log(f"warning: source tables not in the model (not copied): {', '.join(unmapped)}")

    counts: dict[str, int] = {}
    with src.connect() as sc, dst.connect() as dc:
        # Enforced again by the foreign_key_check below; off during the bulk copy
        # so a reference cycle can't block insertion order. Must run outside a
        # transaction for SQLite to honour it.
        dc.exec_driver_sql("PRAGMA foreign_keys=OFF")
        for table in Base.metadata.sorted_tables:
            if table.name not in src_tables:
                log(f"  {table.name:<28} (absent in source, left empty)")
                counts[table.name] = 0
                continue
            src_cols = {c["name"] for c in src_insp.get_columns(table.name)}
            cols = [c for c in table.columns if c.name in src_cols]
            missing = [c.name for c in table.columns if c.name not in src_cols]
            result = sc.execution_options(stream_results=True).execute(select(*cols))
            n = 0
            while batch := result.fetchmany(BATCH):
                dc.execute(table.insert(), [dict(row._mapping) for row in batch])
                n += len(batch)
            counts[table.name] = n
            note = f" (defaults for: {', '.join(missing)})" if missing else ""
            log(f"  {table.name:<28} {n:>7} rows{note}")
        dc.commit()

    install_immutability(dst)

    problems: list[str] = []
    with src.connect() as sc, dst.connect() as dc:
        for table in Base.metadata.sorted_tables:
            if table.name not in src_tables:
                continue
            want = sc.scalar(select(func.count()).select_from(table))
            got = dc.scalar(select(func.count()).select_from(table))
            if want != got:
                problems.append(f"{table.name}: source {want} rows, copy {got}")
        integrity = dc.exec_driver_sql("PRAGMA integrity_check").scalar()
        if integrity != "ok":
            problems.append(f"integrity_check: {integrity}")
        fk = dc.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
        if fk:
            problems.append(f"foreign_key_check: {len(fk)} dangling reference(s), e.g. {fk[0]}")

    # Every run's exported provenance doc must be byte-for-byte the same on both
    # sides (incl. timestamps) and its run_hash must still recompute offline.
    if "runs" in src_tables:
        S, D = qc.session_factory(src)(), qc.session_factory(dst)()
        checked = 0
        for run in D.scalars(select(Run)):
            doc = build_run_doc(run)
            if doc != build_run_doc(S.get(Run, run.id)):
                problems.append(f"run {run.id}: provenance doc differs after copy")
            elif not qc.verify_run_hash(doc):
                problems.append(f"run {run.id}: run_hash no longer verifies")
            checked += 1
        S.close()
        D.close()
        log(f"  verified {checked} run provenance docs + hashes")
    src.dispose()
    dst.dispose()
    if problems:
        raise SystemExit("migration check FAILED:\n  " + "\n  ".join(problems))
    log(f"ok: {sum(counts.values())} rows across {len(counts)} tables -> {dest_path}")
    return counts


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("source_url", help="SQLAlchemy URL, e.g. postgresql+psycopg://u:p@host:5432/db")
    ap.add_argument("dest", type=Path, help="new SQLite file to create")
    args = ap.parse_args()
    migrate(args.source_url, args.dest)


if __name__ == "__main__":
    sys.exit(main())
