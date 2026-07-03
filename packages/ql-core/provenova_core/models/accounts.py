"""Accounts zone: Account, Org, OrgMembership, Workspace + entitlement grants.

In local OSS mode a bootstrap seeds one Account, one Org ("local") and one
Workspace ("default") so the same FK graph holds — no schema fork between the
offline SDK store and the hosted store.
"""

from __future__ import annotations

import datetime as _dt

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, Timestamped, ULIDPk, new_ulid

# Plan tiers (ordered weakest -> strongest for "highest grant wins").
PLAN_FREE = "free"
PLAN_ACADEMIC = "academic"
PLAN_PRO = "pro"
PLAN_LAB = "lab"
PLAN_ENTERPRISE = "enterprise"
PLAN_ORDER = [PLAN_FREE, PLAN_ACADEMIC, PLAN_PRO, PLAN_LAB, PLAN_ENTERPRISE]

# Display names for the UI. The internal key for the paid team tier stays "pro"
# (grants + existing data reference it) but it is presented as "Team".
PLAN_DISPLAY = {
    PLAN_FREE: "Free",
    PLAN_ACADEMIC: "Academic",
    PLAN_PRO: "Team",
    PLAN_LAB: "Lab",
    PLAN_ENTERPRISE: "Enterprise",
}

ROLE_OWNER = "owner"
ROLE_ADMIN = "admin"
ROLE_MEMBER = "member"


class Account(ULIDPk, Timestamped, Base):
    __tablename__ = "accounts"

    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(200))
    password_hash: Mapped[str | None] = mapped_column(String(255))
    auth_provider: Mapped[str] = mapped_column(String(40), default="local")
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    academic_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    is_superadmin: Mapped[bool] = mapped_column(Boolean, default=False)

    memberships: Mapped[list["OrgMembership"]] = relationship(back_populates="account")


class Org(ULIDPk, Timestamped, Base):
    __tablename__ = "orgs"

    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    # Effective plan is resolved from grants; this is a denormalized cache.
    plan: Mapped[str] = mapped_column(String(20), default=PLAN_FREE)
    allowed_regions: Mapped[list | None] = mapped_column(default=None)

    memberships: Mapped[list["OrgMembership"]] = relationship(back_populates="org")
    workspaces: Mapped[list["Workspace"]] = relationship(back_populates="org")
    grants: Mapped[list["Grant"]] = relationship(back_populates="org")


class OrgMembership(ULIDPk, Timestamped, Base):
    __tablename__ = "org_memberships"
    __table_args__ = (UniqueConstraint("account_id", "org_id", name="uq_membership"),)

    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id"), index=True)
    role: Mapped[str] = mapped_column(String(20), default=ROLE_MEMBER)

    account: Mapped[Account] = relationship(back_populates="memberships")
    org: Mapped[Org] = relationship(back_populates="memberships")


class Workspace(ULIDPk, Timestamped, Base):
    __tablename__ = "workspaces"

    org_id: Mapped[str | None] = mapped_column(ForeignKey("orgs.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(80), index=True)
    store_mode: Mapped[str] = mapped_column(String(10), default="hosted")  # local|hosted
    retention_mode: Mapped[str] = mapped_column(String(20), default="raw_shots")
    retention_ttl_days: Mapped[int | None] = mapped_column(default=None)

    org: Mapped[Org | None] = relationship(back_populates="workspaces")
    # head of the per-workspace hash chain (run_hash of the latest sealed run)
    chain_head: Mapped[str | None] = mapped_column(String(64), default=None)


class WorkspaceMember(ULIDPk, Timestamped, Base):
    __tablename__ = "workspace_members"
    __table_args__ = (UniqueConstraint("account_id", "workspace_id", name="uq_ws_member"),)

    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    role: Mapped[str] = mapped_column(String(20), default="viewer")  # ws_admin|editor|contributor|viewer


class Grant(ULIDPk, Timestamped, Base):
    """Assigns a plan to an org. Source can be admin override, academic, etc.

    The effective entitlement is the highest-ranked active grant per feature.
    """

    __tablename__ = "grants"

    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id"), index=True)
    plan: Mapped[str] = mapped_column(String(20))
    source: Mapped[str] = mapped_column(String(30), default="admin_override")
    granted_by: Mapped[str | None] = mapped_column(String(26))
    expires_at: Mapped[_dt.datetime | None] = mapped_column(DateTime(timezone=True))
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    org: Mapped[Org] = relationship(back_populates="grants")


class ApiKey(ULIDPk, Timestamped, Base):
    __tablename__ = "api_keys"

    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id"), index=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    name: Mapped[str] = mapped_column(String(120), default="default")
    prefix: Mapped[str] = mapped_column(String(20), index=True)
    key_hash: Mapped[str] = mapped_column(String(255))
    scopes: Mapped[list | None] = mapped_column(default=None)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class MfaCredential(ULIDPk, Timestamped, Base):
    """TOTP multi-factor credential for an account (one per account)."""

    __tablename__ = "mfa_credentials"
    __table_args__ = (UniqueConstraint("account_id", name="uq_mfa_account"),)

    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    secret: Mapped[str] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)


def bootstrap_local(session) -> "Workspace":
    """Seed the single-user local account/org/workspace used by the OSS SDK."""
    from sqlalchemy import select

    ws = session.scalar(select(Workspace).where(Workspace.slug == "default"))
    if ws is not None:
        return ws
    acc = Account(id=new_ulid(), email="local@quantumledger.local", display_name="Local User")
    org = Org(id=new_ulid(), name="Local", slug="local", plan=PLAN_LAB)
    session.add_all([acc, org])
    session.flush()
    session.add(OrgMembership(account_id=acc.id, org_id=org.id, role=ROLE_OWNER))
    ws = Workspace(
        id=new_ulid(), org_id=org.id, name="Default", slug="default", store_mode="local"
    )
    session.add(ws)
    session.flush()
    return ws
