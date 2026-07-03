"""End-to-end tests for the compliance rule engine.

Builds an in-memory DB, seeds a workspace with two runs, publishes a ResultCard,
records a verified reproduction, loads FAIR + IEEE P7131 and asserts the expected
gap -> pass transitions and that EvidenceItems were auto-collected pointing back
into the immutable core records.
"""

from __future__ import annotations

import datetime as _dt

import pytest
from qiskit import QuantumCircuit
from sqlalchemy import select

import provenova_core as qc
from provenova_core.models import (
    ComplianceAlert,
    EvidenceItem,
    ReproductionEvent,
    ResultCard,
    WorkspaceFramework,
    bootstrap_local,
)
from provenova_core.reproduce import runner

from app.services import compliance
from app.services.compliance import loader, rule_engine

BACKEND_SPEC = {
    "vendor": "aer",
    "name": "aer_simulator",
    "kind": "simulator",
    "basis_gates": ["rz", "sx", "x", "cx", "id"],
    "coupling_map": None,
}


def _fresh_calibration(n_qubits: int, name: str) -> dict:
    from provenova_core.simulate import engine

    cal = engine.default_simulator_calibration(n_qubits, name)
    # Capture "now" so the calibration passes P7131-CAL / metrology freshness.
    cal["captured_at"] = _dt.datetime.now(_dt.timezone.utc).isoformat()
    return cal


def _bell() -> QuantumCircuit:
    circ = QuantumCircuit(2)
    circ.h(0)
    circ.cx(0, 1)
    circ.measure_all()
    return circ


@pytest.fixture()
def seeded():
    """In-memory DB with a workspace, two runs, a card, and one verified repro."""
    engine = qc.init_db("sqlite:///:memory:")
    Session = qc.session_factory(engine)
    session = Session()
    ws = bootstrap_local(session)
    session.commit()

    cal = _fresh_calibration(2, BACKEND_SPEC["name"])
    run1 = runner.record_run(
        session,
        workspace=ws,
        qc=_bell(),
        backend_spec=BACKEND_SPEC,
        calibration_payload=dict(cal),
        shots=2048,
        seed=1337,
    )
    # A second, distinct run (different seed -> different run_hash).
    run2 = runner.record_run(
        session,
        workspace=ws,
        qc=_bell(),
        backend_spec=BACKEND_SPEC,
        calibration_payload=dict(cal),
        shots=2048,
        seed=4242,
    )
    session.commit()

    # Record a verified reproduction of run1 (uses a fresh drifted calibration).
    _new_run, event = runner.reproduce_run(
        session, run1, workspace=ws, days=1.0
    )
    assert event.status == "verified"
    session.commit()

    # Publish a ResultCard for run1 but WITHOUT a doi/license yet.
    card = ResultCard(
        run_id=run1.id,
        workspace_id=ws.id,
        slug="bell-state-2q",
        title="Bell state on 2 qubits",
        visibility="public",
        summary={"backend": BACKEND_SPEC["name"]},
        card_sha256=qc.hashing.sha256_hex({"slug": "bell-state-2q"}),
        doi=None,
        # NB: the license column has a DB-level default of "CC-BY-4.0" that fires
        # on None, so use an explicit empty string to model an unlicensed card.
        license="",
        published_at=_dt.datetime.now(_dt.timezone.utc),
    )
    session.add(card)
    session.commit()

    return {"session": session, "ws": ws, "run1": run1, "run2": run2, "card": card}


def test_public_api_reexports():
    for name in (
        "load_framework",
        "evaluate_framework",
        "evaluate_all",
        "enable_framework",
        "evaluate_predicate",
        "resolve_path",
    ):
        assert hasattr(compliance, name)


def test_fair_gaps_without_doi_then_passes(seeded):
    session, ws = seeded["session"], seeded["ws"]

    fair = loader.load_framework("fair", session)
    session.commit()

    # Initially: card is published + licensed? No — no doi and no license -> gap.
    summary = rule_engine.evaluate_framework(session, ws, fair)
    session.commit()
    assert summary["framework_key"] == "fair"
    assert summary["status"] == "gap"

    by_key = {c["key"]: c for c in summary["controls"]}
    assert by_key["FAIR-F1"]["status"] == "gap"  # no persistent identifier
    assert by_key["FAIR-R1.1"]["status"] == "gap"  # no licence
    assert by_key["FAIR-A1"]["status"] == "pass"  # card is published
    assert by_key["FAIR-I1"]["status"] == "pass"  # qlir/1.0 circuit fmt

    # A gap alert should have been raised for the failing controls.
    gaps = session.scalars(
        select(ComplianceAlert).where(
            ComplianceAlert.framework_id == fair.id,
            ComplianceAlert.kind == "gap",
            ComplianceAlert.resolved.is_(False),
        )
    ).all()
    assert len(gaps) >= 2

    # WorkspaceFramework row was written with detail.
    wf = session.scalar(
        select(WorkspaceFramework).where(
            WorkspaceFramework.workspace_id == ws.id,
            WorkspaceFramework.framework_id == fair.id,
        )
    )
    assert wf.status == "gap"
    assert wf.last_evaluated_at is not None
    assert wf.status_detail["FAIR-F1"]["status"] == "gap"

    # Now mint a DOI + licence on the card and re-evaluate.
    card = seeded["card"]
    card.doi = "10.5281/zenodo.1234567"
    card.license = "CC-BY-4.0"
    session.commit()

    summary2 = rule_engine.evaluate_framework(session, ws, fair)
    session.commit()
    assert summary2["status"] == "pass"
    assert all(c["status"] == "pass" for c in summary2["controls"])

    # Gap alerts are resolved after passing.
    open_gaps = session.scalars(
        select(ComplianceAlert).where(
            ComplianceAlert.framework_id == fair.id,
            ComplianceAlert.kind == "gap",
            ComplianceAlert.resolved.is_(False),
        )
    ).all()
    assert open_gaps == []

    # Evidence was auto-collected and points back into the core records.
    f1_items = session.scalars(
        select(EvidenceItem).where(EvidenceItem.rule_id == "FAIR-F1-pid")
    ).all()
    assert len(f1_items) == 1
    item = f1_items[0]
    assert item.source_ref_type == "result_card"
    assert item.source_ref_id == card.id
    assert item.source_content_hash == card.card_sha256
    assert item.value["value"] == "10.5281/zenodo.1234567"

    lic_items = session.scalars(
        select(EvidenceItem).where(EvidenceItem.rule_id == "FAIR-R1.1-license")
    ).all()
    assert len(lic_items) == 1
    assert lic_items[0].value["value"] == "CC-BY-4.0"


def test_ieee_p7131_passes_with_repro_calib_hashes(seeded):
    session, ws = seeded["session"], seeded["ws"]

    p7131 = loader.load_framework("ieee-p7131", session)
    session.commit()

    summary = rule_engine.evaluate_framework(session, ws, p7131)
    session.commit()

    assert summary["framework_key"] == "ieee-p7131"
    assert summary["status"] == "pass", summary
    by_key = {c["key"]: c for c in summary["controls"]}
    assert by_key["P7131-ENV"]["status"] == "pass"
    assert by_key["P7131-CAL"]["status"] == "pass"
    assert by_key["P7131-REPRO"]["status"] == "pass"
    assert by_key["P7131-HASH"]["status"] == "pass"

    # Env / hash evidence collected for every run in the workspace.
    env_items = session.scalars(
        select(EvidenceItem).where(EvidenceItem.rule_id == "P7131-ENV-present")
    ).all()
    # 2 seeded runs + 1 reproduced run = 3 runs in the workspace.
    assert len(env_items) == 3
    assert {i.source_ref_type for i in env_items} == {"run"}

    hash_items = session.scalars(
        select(EvidenceItem).where(EvidenceItem.rule_id == "P7131-HASH-present")
    ).all()
    assert len(hash_items) == 3
    run1 = seeded["run1"]
    run1_ev = next(i for i in hash_items if i.source_ref_id == run1.id)
    assert run1_ev.source_content_hash == run1.run_hash
    assert run1_ev.value["value"] == run1.run_hash

    # The reproduction control's evidence is the workspace itself.
    repro_items = session.scalars(
        select(EvidenceItem).where(EvidenceItem.rule_id == "P7131-REPRO-count")
    ).all()
    assert len(repro_items) == 1
    assert repro_items[0].source_ref_type == "workspace"
    assert repro_items[0].source_ref_id == ws.id

    # No open gap alerts once the framework passes.
    open_alerts = session.scalars(
        select(ComplianceAlert).where(
            ComplianceAlert.framework_id == p7131.id,
            ComplianceAlert.resolved.is_(False),
        )
    ).all()
    assert open_alerts == []


def test_ieee_p7131_repro_gap_when_no_verified_reproduction():
    """Without a verified reproduction, P7131-REPRO must gap."""
    engine = qc.init_db("sqlite:///:memory:")
    Session = qc.session_factory(engine)
    session = Session()
    ws = bootstrap_local(session)
    session.commit()

    runner.record_run(
        session,
        workspace=ws,
        qc=_bell(),
        backend_spec=BACKEND_SPEC,
        calibration_payload=_fresh_calibration(2, BACKEND_SPEC["name"]),
        shots=1024,
        seed=1,
    )
    session.commit()

    p7131 = loader.load_framework("ieee-p7131", session)
    session.commit()
    summary = rule_engine.evaluate_framework(session, ws, p7131)
    session.commit()

    by_key = {c["key"]: c for c in summary["controls"]}
    assert summary["status"] == "gap"
    assert by_key["P7131-REPRO"]["status"] == "gap"
    # Env/cal/hash still pass on the single seeded run.
    assert by_key["P7131-ENV"]["status"] == "pass"
    assert by_key["P7131-HASH"]["status"] == "pass"


def test_evaluate_all_covers_enabled_frameworks(seeded):
    session, ws = seeded["session"], seeded["ws"]
    fair = loader.load_framework("fair", session)
    p7131 = loader.load_framework("ieee-p7131", session)
    session.commit()

    rule_engine.enable_framework(session, ws, fair)
    rule_engine.enable_framework(session, ws, p7131)
    session.commit()

    summaries = rule_engine.evaluate_all(session, ws)
    session.commit()
    keys = {s["framework_key"] for s in summaries}
    assert keys == {"fair", "ieee-p7131"}


def test_all_bundled_frameworks_load(seeded):
    session = seeded["session"]
    frameworks = loader.load_all_frameworks(session)
    session.commit()
    keys = {f.key for f in frameworks}
    assert {"fair", "ieee-p7131", "metrology", "reproducibility-policy"} <= keys
