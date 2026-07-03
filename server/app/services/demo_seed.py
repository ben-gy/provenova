"""Populate a workspace with a walkable demo dataset.

Records runs on realistic-noise devices, reproduces one under drift, publishes a
Result Card, ingests the public corpus from fixtures, enables + evaluates FAIR
and IEEE P7131, and (optionally) issues an attestation. This is the shared core
used both by the ``scripts/seed_demo.py`` CLI and the in-app "Load demo data"
button, so a user can see value without touching the CLI.

Heavy/optional imports (qiskit, the crawler) are done lazily so importing this
module never slows server startup.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from quantumledger_core.models import ComplianceFramework, Control, EvidenceItem, Workspace

_REPO = Path(__file__).resolve().parents[3]
_FIXTURES = _REPO / "fixtures"


def realistic_calibration(n: int, vendor: str, name: str, captured_at: str, base: float = 1.0) -> dict:
    return {
        "schema": "qlprov/calibration/1.0",
        "backend": {"vendor": vendor, "name": name, "n_qubits": n, "kind": "qpu"},
        "captured_at": captured_at,
        "qubits": [
            {"index": i, "T1_us": 120.0 * base, "T2_us": 90.0 * base,
             "frequency_ghz": 5.0 + i * 0.02, "readout_error": 0.015 * base,
             "prob_meas0_prep1": 0.02 * base, "prob_meas1_prep0": 0.01 * base}
            for i in range(n)
        ],
        "gates": [{"gate": "cx", "qubits": [i, i + 1], "error": 0.009 * base, "length_ns": 400}
                  for i in range(n - 1)]
        + [{"gate": "sx", "qubits": [i], "error": 0.0004 * base, "length_ns": 35} for i in range(n)],
        "general": {"units": {"time": "ns", "coherence": "us"}},
        "provenance": {"source": "vendor_api"},
    }


def _ghz(n: int):
    from qiskit import QuantumCircuit

    c = QuantumCircuit(n)
    c.h(0)
    for i in range(n - 1):
        c.cx(i, i + 1)
    return c


def is_empty(session: Session, workspace: Workspace) -> bool:
    """True if the workspace has no runs yet (safe to seed)."""
    from quantumledger_core.models import Run

    return session.scalar(select(Run).where(Run.workspace_id == workspace.id)) is None


def seed_workspace(session: Session, workspace: Workspace, *, attest: bool = True,
                   account_id: str | None = None) -> dict:
    """Seed ``workspace`` with the full demo dataset. Returns a summary dict."""
    from app.db import attestation_key
    from app.services import cards as cards_svc
    from app.services import compliance as comp
    from app.services.attestation import create_attestation
    from quantumledger_core.reproduce import runner

    summary: dict = {"runs": [], "reproduction": None, "card": None,
                     "corpus": 0, "frameworks": [], "attestation": None}

    # 1) record runs on realistic-noise QPU-like devices
    ibm_cal = realistic_calibration(3, "ibm", "ibm_kyiv", "2026-06-01T09:00:00Z")
    spec = {"vendor": "ibm", "name": "ibm_kyiv", "kind": "qpu",
            "basis_gates": ["rz", "sx", "x", "cx", "id"], "coupling_map": [[0, 1], [1, 2]]}
    r1 = runner.record_run(session, workspace=workspace, qc=_ghz(3), backend_spec=spec,
                           calibration_payload=ibm_cal, shots=4096, seed=1337, project="chem-benchmark")
    ionq_cal = realistic_calibration(3, "ionq", "ionq_forte", "2026-06-01T09:00:00Z", base=0.6)
    ispec = {"vendor": "ionq", "name": "ionq_forte", "kind": "qpu",
             "basis_gates": ["rz", "sx", "x", "cx", "id"], "coupling_map": None}
    r2 = runner.record_run(session, workspace=workspace, qc=_ghz(3), backend_spec=ispec,
                           calibration_payload=ionq_cal, shots=4096, seed=1337, project="chem-benchmark")
    session.flush()
    summary["runs"] = [r1.id, r2.id]

    # 2) reproduce r1 with a bad-day drift
    _new_run, ev = runner.reproduce_run(session, r1, workspace=workspace, days=60,
                                        profile="bad_day", drift_seed=5,
                                        account_id=account_id)
    summary["reproduction"] = {"verdict": ev.verdict, "score": ev.reproducibility_score}

    # 3) publish a Result Card for r1 (identifier minted honestly — the local
    # PID by default, a real DOI when DataCite is configured)
    card = cards_svc.get_or_create_card(session, r1, title="GHZ-3 on IBM Kyiv — chem benchmark")
    card.license = card.license or "CC-BY-4.0"
    cards_svc.publish_card(session, card)
    summary["card"] = card.slug

    # 4) populate the public corpus from fixtures (optional)
    try:
        from quantumledger_crawler.corpus import crawl_all
        from quantumledger_crawler.sources.fixture_source import FixtureSource

        total = 0
        for prov in ("ibm", "ionq", "braket"):
            src = FixtureSource(prov, str(_FIXTURES))
            total += len(crawl_all(session, src))
        summary["corpus"] = total
    except Exception:  # noqa: BLE001 — corpus is a nice-to-have; never block the seed
        summary["corpus"] = 0

    # 5) enable + evaluate FAIR and IEEE P7131
    for key in ("fair", "ieee-p7131"):
        fw = session.scalar(select(ComplianceFramework).where(ComplianceFramework.key.like(f"{key}%")))
        if fw is None:
            continue
        comp.enable_framework(session, workspace, fw)
        result = comp.evaluate_framework(session, workspace, fw)
        summary["frameworks"].append({"key": fw.key, "status": result.get("status")})

    # 6) attest FAIR if evidence exists
    if attest:
        fw = session.scalar(select(ComplianceFramework).where(ComplianceFramework.key.like("fair%")))
        if fw is not None:
            control_ids = [c.id for c in session.scalars(select(Control).where(Control.framework_id == fw.id))]
            items = session.scalars(select(EvidenceItem).where(
                EvidenceItem.workspace_id == workspace.id,
                EvidenceItem.control_id.in_(control_ids))).all()
            if items:
                priv, kid, _ = attestation_key()
                att = create_attestation(session, workspace=workspace, framework=fw,
                                         subject_type="workspace", subject_id=workspace.id,
                                         evidence_items=items, private_key=priv, kid=kid,
                                         issuer_org=workspace.org_id)
                summary["attestation"] = att.id

    session.flush()
    return summary
