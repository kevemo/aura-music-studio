from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Dict, Set, Tuple

from .audit import AuditLogger


class AccessDeniedError(PermissionError):
    """Raised when a role attempts an action it is not authorised to perform."""


class ESPRole(str, Enum):
    """Platform roles ordered by responsibility, not by raw power."""

    OWNER = "owner"
    ADMIN = "admin"
    AGENT = "agent"
    CREATOR = "creator"
    ARTIST = "artist"
    USER = "user"


class ESPAction(str, Enum):
    """Sensitive actions which require an explicit grant."""

    MANAGE_STAFF = "manage_staff"
    MANAGE_ROLES = "manage_roles"
    VIEW_FINANCES = "view_finances"
    MANAGE_FINANCES = "manage_finances"
    MANAGE_RELEASES = "manage_releases"


ADR_OWNER_AUTHORITY = "ADR-002-owner-authority"
ADR_PERMISSION_MATRIX = "ADR-003-role-permission-matrix"


DEFAULT_ROLE_PERMISSIONS: Dict[ESPRole, Set[ESPAction]] = {
    ESPRole.OWNER: set(ESPAction),
    ESPRole.ADMIN: {ESPAction.MANAGE_STAFF, ESPAction.MANAGE_ROLES},
    ESPRole.AGENT: set(),
    ESPRole.CREATOR: set(),
    ESPRole.ARTIST: set(),
    ESPRole.USER: set(),
}


class AccessControl:
    """Role/action policy with audit hooks for privileged operations."""

    def __init__(
        self,
        *,
        workspace: str | Path = ".",
        role_permissions: Dict[ESPRole, Set[ESPAction]] | None = None,
    ) -> None:
        self.role_permissions = role_permissions or DEFAULT_ROLE_PERMISSIONS
        self.audit = AuditLogger(workspace)

    def is_allowed(self, role: ESPRole, action: ESPAction) -> bool:
        return action in self.role_permissions.get(role, set())

    def require(self, role: ESPRole, action: ESPAction) -> None:
        if not self.is_allowed(role, action):
            raise AccessDeniedError(
                f"{role.value!r} is not authorised for {action.value!r}"
            )

    def require_and_audit(
        self,
        *,
        actor_id: str,
        role: ESPRole,
        action: ESPAction,
        target_type: str,
        target_id: str,
        details: dict | None = None,
    ) -> None:
        self.require(role, action)
        self.audit.append(
            actor_id=actor_id,
            role=role.value,
            action=action.value,
            target_type=target_type,
            target_id=target_id,
            details=details or {},
        )

    @staticmethod
    def decision_contract(role: ESPRole, action: ESPAction) -> Tuple[bool, str]:
        """Return (allowed, policy_reference) for tests and service integrations."""

        allowed = action in DEFAULT_ROLE_PERMISSIONS.get(role, set())
        return allowed, ADR_PERMISSION_MATRIX
