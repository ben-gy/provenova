"""scripts/migrate_db.py: a full copy keeps every hash, chain link, attestation
and timestamp verifiable, and re-arms the immutability triggers."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DatabaseError

import provenova_core as qc
from provenova_core.models import Attestation, Run
from provenova_core.provenance import build_run_doc

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "migrate_db.py"
_spec = importlib.util.spec_from_file_location("migrate_db", _SCRIPT)
migrate_db = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migrate_db)


@pytest.fixture(scope="module")
def migrated(tmp_path_factory):
    from app.db import bootstrap, default_workspace
    from app.services.demo_seed import seed_workspace

    tmp = tmp_path_factory.mktemp("migrate")
    src_url = f"sqlite:///{tmp / 'source.db'}"
    src_engine = qc.init_db(src_url)
    s = qc.session_factory(src_engine)()
    bootstrap(s)
    summary = seed_workspace(s, default_workspace(s))
    s.commit()
    s.close()
    assert summary["runs"] and summary["attestation"]

    dest = tmp / "dest.db"
    counts = migrate_db.migrate(src_url, dest, log=lambda *_: None)
    dst_engine = qc.make_engine(f"sqlite:///{dest}")
    yield src_engine, dst_engine, counts
    src_engine.dispose()
    dst_engine.dispose()


def test_every_row_copied(migrated):
    _src, _dst, counts = migrated
    assert counts["runs"] >= 2
    assert counts["attestations"] >= 1


def test_run_docs_identical_and_hashes_verify(migrated):
    src, dst, _ = migrated
    S, D = qc.session_factory(src)(), qc.session_factory(dst)()
    for run in D.scalars(select(Run)):
        doc = build_run_doc(run)
        assert qc.verify_run_hash(doc), run.id
        assert doc == build_run_doc(S.get(Run, run.id)), run.id
        assert doc["created_at"].endswith("+00:00")  # UTC survives SQLite
    S.close()
    D.close()


def test_attestations_still_verify(migrated):
    from app.api.v1.public import live_hashes_for
    from app.db import attestation_key
    from app.services.attestation import verify_attestation

    _src, dst, _ = migrated
    D = qc.session_factory(dst)()
    _priv, _kid, jwks = attestation_key()
    for att in D.scalars(select(Attestation)):
        entries = (att.satisfied_state or {}).get("evidence_entries", [])
        result = verify_attestation(D, att, jwks, live_content_hashes=live_hashes_for(D, entries))
        assert result["valid"], result
    D.close()


def test_immutability_rearmed(migrated):
    _src, dst, _ = migrated
    with dst.connect() as c, pytest.raises(DatabaseError, match="immutable"):
        c.execute(text("UPDATE results SET shots = shots + 1"))


def test_refuses_to_overwrite(migrated, tmp_path):
    existing = tmp_path / "x.db"
    existing.write_bytes(b"")
    with pytest.raises(SystemExit, match="refusing"):
        migrate_db.migrate("sqlite://", existing)
