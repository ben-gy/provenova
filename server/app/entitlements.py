"""Tier entitlement model: plans -> features + quotas.

Entitlements are resolved from the org's plan (which is set by the highest active
grant — admin override, academic, purchase). Full open-format export is available
at EVERY tier per the NFR and is therefore never gated.
"""

from __future__ import annotations

import datetime as _dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from quantumledger_core.models import (
    PLAN_ACADEMIC,
    PLAN_ENTERPRISE,
    PLAN_FREE,
    PLAN_LAB,
    PLAN_ORDER,
    PLAN_PRO,
    Grant,
    Org,
)

# feature_key -> set of plans that include it
FEATURES: dict[str, set[str]] = {
    "public_result_cards": {PLAN_FREE, PLAN_ACADEMIC, PLAN_PRO, PLAN_LAB, PLAN_ENTERPRISE},
    "badges": {PLAN_FREE, PLAN_ACADEMIC, PLAN_PRO, PLAN_LAB, PLAN_ENTERPRISE},
    "reproduce": {PLAN_FREE, PLAN_ACADEMIC, PLAN_PRO, PLAN_LAB, PLAN_ENTERPRISE},
    "private_records": {PLAN_ACADEMIC, PLAN_PRO, PLAN_LAB, PLAN_ENTERPRISE},
    "workspace_sharing": {PLAN_ACADEMIC, PLAN_PRO, PLAN_LAB, PLAN_ENTERPRISE},
    "analytics_depth": {PLAN_ACADEMIC, PLAN_PRO, PLAN_LAB, PLAN_ENTERPRISE},
    "compare_vs_fleet": {PLAN_ACADEMIC, PLAN_PRO, PLAN_LAB, PLAN_ENTERPRISE},
    "compliance_frameworks": {PLAN_PRO, PLAN_LAB, PLAN_ENTERPRISE},
    "continuous_monitoring": {PLAN_PRO, PLAN_LAB, PLAN_ENTERPRISE},
    "attestation_signing": {PLAN_PRO, PLAN_LAB, PLAN_ENTERPRISE},
    "trust_center": {PLAN_LAB, PLAN_ENTERPRISE},
    "self_host": {PLAN_LAB, PLAN_ENTERPRISE},
    "sso_saml": {PLAN_ENTERPRISE},
    "data_residency": {PLAN_ENTERPRISE},
    "sla": {PLAN_ENTERPRISE},
}

QUOTAS: dict[str, dict[str, int]] = {
    PLAN_FREE: {"seats": 1, "frameworks_enabled": 0},
    PLAN_ACADEMIC: {"seats": 5, "frameworks_enabled": 3},
    PLAN_PRO: {"seats": 10, "frameworks_enabled": 10},
    PLAN_LAB: {"seats": 50, "frameworks_enabled": 9999},
    PLAN_ENTERPRISE: {"seats": 9999, "frameworks_enabled": 9999},
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


def has_feature(plan: str, feature: str) -> bool:
    return plan in FEATURES.get(feature, set())
