"""Volume-based plan limits (private-run cap, framework cap).

A "private run" is an original captured run in a workspace that (a) is not a
reproduction-generated run and (b) has no public Result Card. Publishing a run
publicly frees a private slot; nothing is ever deleted.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from provenova_core.models import (
    VIS_PUBLIC,
    ReproductionEvent,
    ResultCard,
    Run,
    WorkspaceFramework,
)

from ..entitlements import is_unlimited, quota_for


def private_run_count(session: Session, workspace_id: str) -> int:
    reproduced = select(ReproductionEvent.reproduced_run_id)
    public = select(ResultCard.run_id).where(ResultCard.visibility == VIS_PUBLIC)
    return session.scalar(
        select(func.count(Run.id)).where(
            Run.workspace_id == workspace_id,
            Run.id.not_in(reproduced),
            Run.id.not_in(public),
        )
    ) or 0


def private_run_usage(session: Session, plan: str, workspace_id: str) -> dict:
    cap = quota_for(plan, "private_run_cap")
    used = private_run_count(session, workspace_id)
    unlimited = is_unlimited(cap)
    return {
        "used": used,
        "cap": cap,
        "unlimited": unlimited,
        "at_cap": (not unlimited) and used >= cap,
        "pct": 0 if unlimited or cap == 0 else min(100, round(used * 100 / cap)),
    }


def doi_minted_this_month(session: Session, workspace_id: str) -> int:
    """Real DOIs minted this calendar month (local PIDs are free/unlimited).

    Counted from the append-only audit trail rather than ``ResultCard`` state:
    a card's ``published_at`` is nulled on unpublish and reset on republish, so
    counting cards would let publish/unpublish cycling bypass the cap and would
    miscount across months. Mint audit rows are immutable, so they can't.
    """
    from provenova_core.models import AuditLog

    from .doi import month_start

    rows = session.scalars(
        select(AuditLog).where(
            AuditLog.workspace_id == workspace_id,
            AuditLog.action == "card.doi.mint",
            AuditLog.created_at >= month_start(),
        )
    ).all()
    # Filter on the outcome in Python to stay portable across SQLite/Postgres
    # JSON; volume is tiny (at most a workspace's monthly mints + failures).
    return sum(1 for r in rows if (r.detail or {}).get("status") == "minted")


def doi_usage(session: Session, plan: str, workspace_id: str) -> dict:
    cap = quota_for(plan, "doi_monthly_cap")
    used = doi_minted_this_month(session, workspace_id)
    unlimited = is_unlimited(cap)
    return {
        "used": used,
        "cap": cap,
        "unlimited": unlimited,
        "at_cap": (not unlimited) and used >= cap,
        "pct": 0 if unlimited or cap == 0 else min(100, round(used * 100 / cap)),
    }


def frameworks_enabled_count(session: Session, workspace_id: str) -> int:
    return session.scalar(
        select(func.count(WorkspaceFramework.id)).where(
            WorkspaceFramework.workspace_id == workspace_id
        )
    ) or 0
