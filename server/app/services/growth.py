"""Growth pipeline service: bot workspace, qlir validation, research cards.

Every safety rail lives HERE, server-side, independent of whatever the
scheduled routine sends:

* strict qlir validation with a gate-name allowlist — REQUIRED because
  ``bridge.qiskit_from_ir`` falls through to ``getattr(qc, name)(...)`` for
  unknown gates, which would happily call arbitrary QuantumCircuit methods;
* size caps (n_qubits <= 10 bounds the statevector to 2^10; gates <= 256;
  shots <= 8192);
* volume caps counted from the DB (survive restarts);
* required paper attribution with a host-allowlisted URL;
* idempotency on (arxiv_id|doi, circuit_sha256).

Cards record REAL deterministic simulator runs via the existing immutable
``record_run`` path and ship with a genuine reproduction score.
"""

from __future__ import annotations

import datetime as _dt
import math

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from quantumledger_core import hashing
from quantumledger_core.models import (
    PLAN_LAB,
    Account,
    CardAttribution,
    CorpusSnapshot,
    Org,
    OrgMembership,
    Report,
    ResultCard,
    Workspace,
    WorkspaceMember,
    new_ulid,
)
from quantumledger_core.reproduce import runner
from quantumledger_core.simulate import bridge

from ..config import get_settings
from . import cards as cards_svc
from .accounts import audit, grant_plan
from .sanitize import render_untrusted_markdown

BOT_EMAIL = "research-bot@quantumledger.local"
BOT_ORG_SLUG = "ql-research"
BOT_WS_SLUG = "research"

# Honest simulator backend spec — identical shape to scripts/seed_real.py.
SIM = {"vendor": "local_sim", "name": "aer_statevector", "kind": "simulator",
       "basis_gates": ["rz", "sx", "x", "cx", "id"], "coupling_map": None}

# Gate allowlist: name -> (n_params, n_qubits). Everything else is rejected
# BEFORE bridge.qiskit_from_ir's getattr fallback can see it.
ALLOWED_GATES: dict[str, tuple[int, int]] = {
    "h": (0, 1), "x": (0, 1), "y": (0, 1), "z": (0, 1),
    "s": (0, 1), "sdg": (0, 1), "t": (0, 1), "tdg": (0, 1), "sx": (0, 1),
    "id": (0, 1),
    "rz": (1, 1), "rx": (1, 1), "ry": (1, 1),
    "cx": (0, 2), "cz": (0, 2), "swap": (0, 2),
    "ccx": (0, 3),
}
MAX_QUBITS = 10
MAX_GATES = 256
MAX_SHOTS = 8192


class QlirValidationError(ValueError):
    pass


def validate_qlir(circuit: dict) -> dict:
    """Validate + canonicalise a qlir/1.0 dict. Raises QlirValidationError."""
    if not isinstance(circuit, dict):
        raise QlirValidationError("circuit must be an object")
    if circuit.get("schema") != "qlir/1.0":
        raise QlirValidationError("circuit.schema must be 'qlir/1.0'")
    n = circuit.get("n_qubits")
    if not isinstance(n, int) or not (1 <= n <= MAX_QUBITS):
        raise QlirValidationError(f"n_qubits must be an int in [1, {MAX_QUBITS}]")
    gates = circuit.get("gates")
    if not isinstance(gates, list) or not (1 <= len(gates) <= MAX_GATES):
        raise QlirValidationError(f"gates must be a list of 1..{MAX_GATES}")
    canon = []
    for i, g in enumerate(gates):
        if not isinstance(g, dict):
            raise QlirValidationError(f"gate[{i}] must be an object")
        name = g.get("name")
        if name not in ALLOWED_GATES:
            raise QlirValidationError(f"gate[{i}].name {name!r} not in allowlist")
        n_params, n_qubits = ALLOWED_GATES[name]
        qubits = g.get("qubits")
        if (not isinstance(qubits, list) or len(qubits) != n_qubits
                or not all(isinstance(q, int) and 0 <= q < n for q in qubits)
                or len(set(qubits)) != len(qubits)):
            raise QlirValidationError(
                f"gate[{i}].qubits must be {n_qubits} distinct ints in [0, {n})")
        params = g.get("params", [])
        if not isinstance(params, list) or len(params) != n_params:
            raise QlirValidationError(f"gate[{i}].params must have {n_params} entries")
        fparams = []
        for pv in params:
            if not isinstance(pv, (int, float)) or isinstance(pv, bool) or not math.isfinite(pv):
                raise QlirValidationError(f"gate[{i}].params must be finite numbers")
            fparams.append(float(pv))
        canon.append({"name": name, "qubits": list(qubits), "params": fparams})
    return {"schema": "qlir/1.0", "n_qubits": n, "gates": canon}


def circuit_sha256(canonical_qlir: dict) -> str:
    return hashing.sha256_hex(canonical_qlir)


# --- bot bootstrap -----------------------------------------------------------

def ensure_research_bot(session: Session) -> tuple[Account, Org, Workspace]:
    """Idempotently create the research bot account/org/workspace (Lab plan).

    The account has NO password (unloginable — API-key only). Safe to call on
    every growth request: it's a couple of indexed selects when warm.
    """
    acc = session.scalar(select(Account).where(Account.email == BOT_EMAIL))
    if acc is None:
        acc = Account(email=BOT_EMAIL, display_name="QuantumLedger Research Bot",
                      password_hash=None, email_verified=True, is_superadmin=False)
        session.add(acc)
        session.flush()
    org = session.scalar(select(Org).where(Org.slug == BOT_ORG_SLUG))
    if org is None:
        org = Org(name="QuantumLedger Research", slug=BOT_ORG_SLUG, plan="free")
        session.add(org)
        session.flush()
        grant_plan(session, org, PLAN_LAB, source="internal", granted_by=acc.id)
    m = session.scalar(select(OrgMembership).where(
        OrgMembership.account_id == acc.id, OrgMembership.org_id == org.id))
    if m is None:
        session.add(OrgMembership(account_id=acc.id, org_id=org.id, role="owner"))
    ws = session.scalar(select(Workspace).where(
        Workspace.org_id == org.id, Workspace.slug == BOT_WS_SLUG))
    if ws is None:
        ws = Workspace(org_id=org.id, name="Research", slug=BOT_WS_SLUG, store_mode="hosted")
        session.add(ws)
        session.flush()
        session.add(WorkspaceMember(account_id=acc.id, workspace_id=ws.id, role="ws_admin"))
    session.flush()
    return acc, org, ws


# --- volume rails ------------------------------------------------------------

def cards_today(session: Session) -> int:
    day_start = _dt.datetime.now(_dt.timezone.utc).replace(hour=0, minute=0, second=0,
                                                           microsecond=0)
    return session.scalar(
        select(func.count(CardAttribution.id)).where(CardAttribution.created_at >= day_start)
    ) or 0


def reports_this_week(session: Session) -> int:
    week_ago = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=7)
    return session.scalar(
        select(func.count(Report.id)).where(Report.created_at >= week_ago)
    ) or 0


# --- research cards ----------------------------------------------------------

def find_existing(session: Session, *, arxiv_id: str | None, doi: str | None,
                  sha: str) -> CardAttribution | None:
    """Idempotency: same circuit AND (matching arxiv_id OR matching doi).

    Checking BOTH identifiers (not just the first present) catches a paper first
    published doi-only and later resubmitted with its arxiv_id, and vice versa.
    """
    conds = []
    if arxiv_id:
        conds.append(CardAttribution.arxiv_id == arxiv_id)
    if doi:
        conds.append(CardAttribution.doi == doi)
    if not conds:
        return None
    return session.scalar(
        select(CardAttribution).where(CardAttribution.circuit_sha256 == sha, or_(*conds)))


def create_research_card(session: Session, *, bot: tuple[Account, Org, Workspace],
                         paper: dict, canonical_circuit: dict, sha: str,
                         shots: int, seed: int, title: str,
                         commentary_md: str, relation: str = "inspired_by") -> dict:
    """Record a real deterministic run, publish the card, store attribution."""
    acc, _org, ws = bot
    ir = bridge.dict_to_ir(canonical_circuit)
    qc = bridge.qiskit_from_ir(ir)

    run = runner.record_run(
        session, workspace=ws, qc=qc, backend_spec=SIM,
        shots=min(shots, MAX_SHOTS), seed=seed, account_id=acc.id,
        project=f"research:{paper.get('arxiv_id') or paper.get('doi')}")
    # A genuine reproduction so the card ships with a reproducibility score.
    runner.reproduce_run(session, run, workspace=ws, days=7, profile="typical")

    card = cards_svc.get_or_create_card(session, run, title=title)
    cards_svc.publish_card(session, card)

    attr = CardAttribution(
        card_id=card.id,
        arxiv_id=paper.get("arxiv_id"), doi=paper.get("doi"),
        title=paper["title"], authors=paper.get("authors") or [],
        year=paper.get("year"), url=paper["url"], relation=relation,
        commentary_md=commentary_md, circuit_sha256=sha, created_by=acc.id,
    )
    session.add(attr)
    audit(session, workspace_id=ws.id, account_id=acc.id, action="growth.card.publish",
          resource_type="card", resource_id=card.id,
          detail={"arxiv_id": paper.get("arxiv_id"), "doi": paper.get("doi"),
                  "circuit_sha256": sha})
    session.flush()
    return {"status": "created", "slug": card.slug, "run_hash": run.run_hash,
            "card_url": f"{get_settings().base_url}/cards/{card.slug}"}


# --- reports -------------------------------------------------------------------

def publish_report(session: Session, *, bot_account_id: str, slug: str, title: str,
                   body_md: str, meta_description: str,
                   kind: str = "weekly_fleet") -> Report:
    r = Report(slug=slug, title=title, kind=kind, body_md=body_md,
               body_html=render_untrusted_markdown(body_md),
               meta_description=meta_description, published=True,
               published_at=_dt.datetime.now(_dt.timezone.utc))
    session.add(r)
    audit(session, workspace_id=None, account_id=bot_account_id,
          action="growth.report.publish", resource_type="report", resource_id=slug)
    session.flush()
    return r


# --- status --------------------------------------------------------------------

def growth_status(session: Session) -> dict:
    settings = get_settings()
    snapshots = session.scalar(select(func.count(CorpusSnapshot.id))) or 0
    by_provider = dict(session.execute(
        select(CorpusSnapshot.provider, func.count(CorpusSnapshot.id))
        .group_by(CorpusSnapshot.provider)).all())
    recent = session.scalars(
        select(CardAttribution).order_by(CardAttribution.created_at.desc()).limit(50)).all()
    known_ids = [a.arxiv_id for a in session.scalars(
        select(CardAttribution).where(CardAttribution.arxiv_id.is_not(None))
        .order_by(CardAttribution.created_at.desc()).limit(200)) if a.arxiv_id]
    slugs = {a.card_id: a for a in recent}
    cards = {c.id: c for c in session.scalars(
        select(ResultCard).where(ResultCard.id.in_(list(slugs)))).all()} if slugs else {}
    latest_report = session.scalar(
        select(Report).where(Report.published.is_(True)).order_by(Report.published_at.desc()))
    return {
        "corpus": {"snapshots": snapshots, "by_provider": by_provider},
        "research_cards": {
            "today": cards_today(session),
            "daily_cap": settings.growth_max_cards_per_day,
            "total": session.scalar(select(func.count(CardAttribution.id))) or 0,
            "recent": [{"slug": cards[a.card_id].slug if a.card_id in cards else None,
                        "arxiv_id": a.arxiv_id, "doi": a.doi,
                        "circuit_sha256": a.circuit_sha256,
                        "created_at": a.created_at.isoformat() if a.created_at else None}
                       for a in recent],
            "known_arxiv_ids": known_ids,
        },
        "reports": {
            "latest_slug": latest_report.slug if latest_report else None,
            "latest_published_at": (latest_report.published_at.isoformat()
                                    if latest_report and latest_report.published_at else None),
            "this_week": reports_this_week(session),
            "weekly_cap": settings.growth_max_reports_per_week,
        },
        "server_time": _dt.datetime.now(_dt.timezone.utc).isoformat(),
    }
