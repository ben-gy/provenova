"""Role-based access control: Org -> Workspace -> Resource."""

from __future__ import annotations

# workspace role -> permitted actions
WS_ROLE_ACTIONS: dict[str, set[str]] = {
    "ws_admin": {"read", "create", "update", "publish", "sign", "invite", "manage"},
    "editor": {"read", "create", "update", "publish", "sign"},
    "contributor": {"read", "create", "update"},
    "viewer": {"read"},
}

ORG_ROLE_ACTIONS: dict[str, set[str]] = {
    "owner": {"read", "create", "update", "publish", "sign", "invite", "manage",
              "manage_billing", "manage_sso", "manage_residency", "delete"},
    "admin": {"read", "create", "update", "publish", "sign", "invite", "manage"},
    "member": {"read", "create"},
}


def can(*, org_role: str | None, ws_role: str | None, action: str, is_superadmin: bool = False) -> bool:
    if is_superadmin:
        return True
    if org_role and action in ORG_ROLE_ACTIONS.get(org_role, set()):
        return True
    if ws_role and action in WS_ROLE_ACTIONS.get(ws_role, set()):
        return True
    return False
