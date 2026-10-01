"""Entitlement model: plans -> features + quotas.

Provenova is currently free: every plan resolves to every feature with no volume
caps (see FEATURES/QUOTAS). Plans are still resolved from the org's stored plan
and the highest active grant so tiers could be reintroduced without a schema
change. Full open-format export is never gated.

Self-hosting is never an entitlement: running the server is governed by the
server license (BUSL-1.1, production use included), and plans only bind on the
hosted service — a self-hosted operator administers their own grants.
"""

from __future__ import annotations

import datetime as _dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from provenova_core.models import (
    PLAN_ACADEMIC,
    PLAN_ENTERPRISE,
    PLAN_FREE,
    PLAN_LAB,
    PLAN_ORDER,
    PLAN_PRO,
    Grant,
    Org,
)

# Sentinel for "no cap". Any quota >= UNLIMITED is displayed as "Unlimited".
UNLIMITED = 9_999_999

# Provenova is free for now: every plan gets every feature and no volume caps.
# The plan/grant machinery is kept (dormant) so tiers can return later without a
# schema change; until then the plan an org holds changes nothing.
_ALL_PLANS = {PLAN_FREE, PLAN_ACADEMIC, PLAN_PRO, PLAN_LAB, PLAN_ENTERPRISE}

# feature_key -> set of plans that include it.
FEATURES: dict[str, set[str]] = {
    key: set(_ALL_PLANS)
    for key in (
        "public_result_cards", "badges", "reproduce", "private_records",
        "compare_vs_fleet", "compliance_frameworks", "workspace_sharing",
        "analytics_depth", "attestation_signing", "continuous_monitoring",
        "trust_center", "verified_keys",
    )
}

# Real (Zenodo/DataCite) DOIs are permanent and can never be deleted, so they
# keep a per-workspace fair-use cap even while everything else is unlimited.
# This is abuse protection, not a paid tier; local PIDs are never capped.
DOI_FAIR_USE_MONTHLY = 20

QUOTAS: dict[str, dict[str, int]] = {
    plan: {"seats": UNLIMITED, "frameworks_allowed": UNLIMITED,
           "private_run_cap": UNLIMITED, "doi_monthly_cap": DOI_FAIR_USE_MONTHLY}
    for plan in _ALL_PLANS
}


def effective_plan(session: Session, org: Org) -> str:
    """Highest-ranked active grant, else the org's stored plan, else free."""
    now = _dt.datetime.now(_dt.timezone.utc)
    grants = session.scalars(select(Grant).where(Grant.org_id == org.id, Grant.active.is_(True))).all()
    plans = [org.plan or PLAN_FREE]
    for g in grants:
        if g.expires_at is not None:
            exp = g.expires_at if g.expires_at.tzinfo else g.expires_at.replace(tzinfo=_dt.timezone.utc)
            if exp < now:
                continue
        plans.append(g.plan)
    return max(plans, key=lambda p: PLAN_ORDER.index(p) if p in PLAN_ORDER else -1)


def features_for(plan: str) -> set[str]:
    return {f for f, plans in FEATURES.items() if plan in plans}


def quota_for(plan: str, key: str) -> int:
    return QUOTAS.get(plan, QUOTAS[PLAN_FREE]).get(key, 0)


def is_unlimited(value: int) -> bool:
    return value >= UNLIMITED


def has_feature(plan: str, feature: str) -> bool:
    return plan in FEATURES.get(feature, set())
