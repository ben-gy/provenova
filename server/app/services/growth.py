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

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from provenova_core import hashing
from provenova_core.models import (
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
from provenova_core.reproduce import runner
from provenova_core.simulate import bridge
from provenova_core.simulate.safety import (  # shared allowlist + caps (single source of truth)
    ALLOWED_GATES,
    MAX_GATES,
    MAX_QUBITS,
    MAX_SHOTS,
    UnsafeCircuitError,
    assert_safe_circuit,
)

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

# The gate allowlist + caps now live in provenova_core.simulate.safety (imported
# above) so ingest, growth and the bridge share one definition. QlirValidationError
# stays as an alias for backward-compatible except-clauses in the growth API.
QlirValidationError = UnsafeCircuitError


def validate_qlir(circuit: dict) -> dict:
    """Validate + canonicalise a qlir/1.0 dict. Raises QlirValidationError.

    Growth payloads must additionally carry the explicit schema tag; the gate
    allowlist and size caps are enforced by the shared ``assert_safe_circuit``.
    """
    if not isinstance(circuit, dict):
        raise QlirValidationError("circuit must be an object")
    if circuit.get("schema") != "qlir/1.0":
        raise QlirValidationError("circuit.schema must be 'qlir/1.0'")
    return assert_safe_circuit(circuit)


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
        acc = Account(email=BOT_EMAIL, display_name="Provenova Research Bot",
                      password_hash=None, email_verified=True, is_superadmin=False)
        session.add(acc)
        session.flush()
    org = session.scalar(select(Org).where(Org.slug == BOT_ORG_SLUG))
    if org is None:
        org = Org(name="Provenova Research", slug=BOT_ORG_SLUG, plan="free")
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
