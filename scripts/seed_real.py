"""Replace fabricated demo data with real, openly-licensed content.

- Wipes the fake seed content (runs/cards/reproductions/attestations/corpus),
  keeping accounts, workspaces and the compliance frameworks.
- Loads the public hardware corpus from the committed datasets/ (IBM
  calibration history from Qiskit's fake backends, Apache-2.0; Metriq
  benchmarks, CC-BY-4.0; labelled vendor claims).
- Records REAL, deterministic simulator runs of canonical open-source benchmark
  circuits (Bell, GHZ, QFT, Grover, Deutsch–Jozsa, Bernstein–Vazirani) on the
  ideal Qiskit Aer statevector — honestly labelled as a simulator.
- Publishes a few Result Cards, records genuine reproductions, evaluates FAIR +
  IEEE P7131 against the real evidence, and issues a signed attestation.

Run against a database via QL_DATABASE_URL (for production, run it inside the
container: deploy/HOSTING-cloudflare.md, "Running scripts against production"):
    QL_DATABASE_URL=postgresql+psycopg://... PYTHONPATH=server python scripts/seed_real.py
"""

from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path

from qiskit import QuantumCircuit
from qiskit.circuit.library import QFT
from sqlalchemy import select, text

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "server"))

from app.db import SessionLocal, attestation_key, bootstrap, default_workspace, engine  # noqa: E402
from app.services import cards as cards_svc  # noqa: E402
from app.services import compliance as comp  # noqa: E402
from app.services.attestation import create_attestation  # noqa: E402

from provenova_core.immutability import drop_immutability, install_immutability  # noqa: E402
from provenova_core.models import (  # noqa: E402
    ComplianceFramework,
    Control,
    EvidenceItem,
    Workspace,
)
from provenova_core.reproduce import runner  # noqa: E402

SIM = {"vendor": "local_sim", "name": "aer_statevector", "kind": "simulator",
       "basis_gates": ["rz", "sx", "x", "cx", "id"], "coupling_map": None}

# ---- open-source benchmark circuits (textbook algorithms, public domain) ----

def _bell():
    c = QuantumCircuit(2); c.h(0); c.cx(0, 1); return c

def _ghz(n):
    c = QuantumCircuit(n); c.h(0)
    for i in range(n - 1): c.cx(i, i + 1)
    return c

def _qft(n):
    c = QuantumCircuit(n); c.x(0); c.compose(QFT(n), inplace=True); return c

def _grover2():
    c = QuantumCircuit(2); c.h([0, 1]); c.cz(0, 1)
    c.h([0, 1]); c.x([0, 1]); c.cz(0, 1); c.x([0, 1]); c.h([0, 1]); return c

def _dj(n=3):
    c = QuantumCircuit(n + 1); c.x(n); c.h(range(n + 1))
    for i in range(n): c.cx(i, n)
    c.h(range(n)); return c

def _bv(secret="101"):
    n = len(secret); c = QuantumCircuit(n + 1); c.x(n); c.h(range(n + 1))
    for i, bit in enumerate(reversed(secret)):
        if bit == "1": c.cx(i, n)
    c.h(range(n)); return c

CIRCUITS = [
    ("Bell state (Φ⁺)", "Maximally-entangled two-qubit Bell pair.", _bell(), True),
    ("GHZ state (3 qubits)", "Three-qubit Greenberger–Horne–Zeilinger state.", _ghz(3), False),
    ("GHZ state (5 qubits)", "Five-qubit GHZ state — a classic entanglement benchmark.", _ghz(5), True),
    ("Quantum Fourier Transform (3 qubits)", "QFT applied to a computational basis input.", _qft(3), False),
    ("Quantum Fourier Transform (4 qubits)", "Four-qubit QFT.", _qft(4), True),
    ("Grover search (2 qubits)", "Grover amplitude amplification marking |11⟩.", _grover2(), True),
    ("Deutsch–Jozsa (balanced, 3 qubits)", "Detects a balanced oracle in a single query.", _dj(3), False),
    ("Bernstein–Vazirani (secret 101)", "Recovers a hidden bitstring in one query.", _bv("101"), False),
]


def sim_calibration(n, now):
    return {
        "schema": "qlprov/calibration/1.0",
        "backend": {"vendor": "local_sim", "name": "aer_statevector", "n_qubits": n, "kind": "simulator"},
        "captured_at": now.isoformat(),
        "qubits": [{"index": i, "readout_error": 0.0} for i in range(n)],
        "gates": [],
        "general": {"note": "Ideal Qiskit Aer statevector — no hardware noise."},
        "provenance": {"source": "simulated", "engine": "aer_statevector",
                       "note": "Deterministic, noiseless reference simulator (Qiskit, Apache-2.0)."},
    }


# ---- wipe --------------------------------------------------------------------

_CONTENT_TABLES = [
    "evidence_items", "attestations", "compliance_alerts", "workspace_frameworks",
    "badges", "benchmark_entries", "benchmarks", "result_cards", "reproduction_events",
    "results", "runs", "compilations", "calibration_snapshots", "circuits", "backends",
    "corpus_snapshots", "collector_state", "public_datasets", "checkpoint_anchors", "audit_logs",
]


def wipe_content(session, eng):
    drop_immutability(eng)
    try:
        with eng.begin() as conn:
            for tbl in _CONTENT_TABLES:
                conn.execute(text(f"DELETE FROM {tbl}"))
            conn.execute(text("UPDATE workspaces SET chain_head = NULL"))
    finally:
        install_immutability(eng)
    session.expire_all()


# ---- real corpus (the committed datasets/, see app.services.corpus_sync) ------

def load_corpus(session):
    """Reconcile corpus_snapshots with datasets/ (IBM history, Metriq, vendor specs)."""
    from app.db import datasets_dir
    from app.services.corpus_sync import sync_bundled

    return sync_bundled(session, datasets_dir())


# ---- main --------------------------------------------------------------------

def main():
    eng = engine()
    s = SessionLocal()
    bootstrap(s)
    print("wiping fabricated content ...")
    wipe_content(s, eng)

    print(f"real corpus synced: {load_corpus(s)}")

    ws = default_workspace(s)
    now = _dt.datetime.now(_dt.timezone.utc)
    runs = []
    for name, desc, qc, publish in CIRCUITS:
        run = runner.record_run(
            s, workspace=ws, qc=qc, backend_spec=SIM,
            calibration_payload=sim_calibration(qc.num_qubits, now),
            shots=4096, seed=1729, project=name)
        runs.append((run, name, publish))
    s.commit()
    print(f"real benchmark runs recorded: {len(runs)}")

    # publish cards + record genuine reproductions (deterministic -> reproducible)
    published = []
    for run, name, publish in runs:
        if not publish:
            continue
        card = cards_svc.get_or_create_card(s, run, title=f"{name} — Aer statevector")
        cards_svc.publish_card(s, card)
        published.append(card.slug)
    for run, name, publish in runs:
        if name in ("GHZ state (5 qubits)", "Grover search (2 qubits)"):
            runner.reproduce_run(s, run, workspace=ws, days=30, profile="typical")
    s.commit()
    print(f"published cards: {len(published)}  reproductions recorded: 2")

    # compliance on the real evidence
    for key in ("fair", "ieee-p7131"):
        fw = s.scalar(select(ComplianceFramework).where(ComplianceFramework.key.like(f"{key}%")))
        if fw is None:
            continue
        comp.enable_framework(s, ws, fw)
        summary = comp.evaluate_framework(s, ws, fw)
        print(f"framework {fw.key}: {summary.get('status')}")
    s.commit()

    # attest FAIR
    fw = s.scalar(select(ComplianceFramework).where(ComplianceFramework.key.like("fair%")))
    if fw is not None:
        cids = [c.id for c in s.scalars(select(Control).where(Control.framework_id == fw.id))]
        items = s.scalars(select(EvidenceItem).where(
            EvidenceItem.workspace_id == ws.id, EvidenceItem.control_id.in_(cids))).all()
        if items:
            priv, kid, _ = attestation_key()
            att = create_attestation(s, workspace=ws, framework=fw, subject_type="workspace",
                                     subject_id=ws.id, evidence_items=items, private_key=priv,
                                     kid=kid, issuer_org=ws.org_id)
            s.commit()
            print(f"attestation: {att.id}")

    print("REAL DATA SEEDED")
    s.close()


if __name__ == "__main__":
    main()
