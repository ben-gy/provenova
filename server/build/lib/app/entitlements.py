"""Tier entitlement model: plans -> features + quotas.

Entitlements are resolved from the org's plan (which is set by the highest active
grant — admin override, academic, purchase). Full open-format export is available
at EVERY tier per the NFR and is therefore never gated.

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

# feature_key -> set of plans that include it.
# Free competes with the OSS alternatives: private records (capped by volume),
# fleet comparison (for the Benchmarked badge), and a read-only FAIR compliance
# view are all available at $0. Paid tiers own the *trust artifacts*
# (attestation issuance, continuous monitoring, Trust Center).
FEATURES: dict[str, set[str]] = {
    "public_result_cards": {PLAN_FREE, PLAN_ACADEMIC, PLAN_PRO, PLAN_LAB, PLAN_ENTERPRISE},
    "badges": {PLAN_FREE, PLAN_ACADEMIC, PLAN_PRO, PLAN_LAB, PLAN_ENTERPRISE},
    "reproduce": {PLAN_FREE, PLAN_ACADEMIC, PLAN_PRO, PLAN_LAB, PLAN_ENTERPRISE},
    "private_records": {PLAN_FREE, PLAN_ACADEMIC, PLAN_PRO, PLAN_LAB, PLAN_ENTERPRISE},
    "compare_vs_fleet": {PLAN_FREE, PLAN_ACADEMIC, PLAN_PRO, PLAN_LAB, PLAN_ENTERPRISE},
    # Free gets a read-only FAIR view (see frameworks_allowed cap + FAIR-only rule);
    # attestation *issuance* stays paid.
    "compliance_frameworks": {PLAN_FREE, PLAN_ACADEMIC, PLAN_PRO, PLAN_LAB, PLAN_ENTERPRISE},
    "workspace_sharing": {PLAN_ACADEMIC, PLAN_PRO, PLAN_LAB, PLAN_ENTERPRISE},
    "analytics_depth": {PLAN_ACADEMIC, PLAN_PRO, PLAN_LAB, PLAN_ENTERPRISE},
    "attestation_signing": {PLAN_ACADEMIC, PLAN_PRO, PLAN_LAB, PLAN_ENTERPRISE},
    "continuous_monitoring": {PLAN_PRO, PLAN_LAB, PLAN_ENTERPRISE},
    "trust_center": {PLAN_LAB, PLAN_ENTERPRISE},
    "verified_keys": {PLAN_LAB, PLAN_ENTERPRISE},
    "sso_saml": {PLAN_LAB, PLAN_ENTERPRISE},
    "data_residency": {PLAN_ENTERPRISE},
    "sla": {PLAN_ENTERPRISE},
}

QUOTAS: dict[str, dict[str, int]] = {
    PLAN_FREE: {"seats": 1, "frameworks_allowed": 1, "private_run_cap": 250, "doi_monthly_cap": 5},
    PLAN_ACADEMIC: {"seats": 15, "frameworks_allowed": UNLIMITED, "private_run_cap": UNLIMITED,
                    "doi_monthly_cap": UNLIMITED},
    PLAN_PRO: {"seats": 10, "frameworks_allowed": 10, "private_run_cap": UNLIMITED,
               "doi_monthly_cap": UNLIMITED},
    PLAN_LAB: {"seats": 50, "frameworks_allowed": UNLIMITED, "private_run_cap": UNLIMITED,
               "doi_monthly_cap": UNLIMITED},
    PLAN_ENTERPRISE: {"seats": UNLIMITED, "frameworks_allowed": UNLIMITED,
                      "private_run_cap": UNLIMITED, "doi_monthly_cap": UNLIMITED},
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
