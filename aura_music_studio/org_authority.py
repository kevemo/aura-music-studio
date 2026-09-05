from __future__ import annotations

from enum import Enum
from typing import Iterable, Mapping


class OrgAuthorityDeniedError(PermissionError):
    """Raised when ESP organisational authority is not explicitly granted."""


class OrgRole(str, Enum):
    OWNER = "owner"
    ADMIN = "admin"
    AGENT = "agent"
    CREATOR = "creator"
    ARTIST = "artist"
    USER = "user"


class OrgAction(str, Enum):
    MANAGE_STAFF = "manage_staff"
    MANAGE_ROLES = "manage_roles"
    VIEW_FINANCES = "view_finances"
    MANAGE_FINANCES = "manage_finances"
    MANAGE_RELEASES = "manage_releases"


DEFAULT_ROLE_PERMISSIONS: dict[OrgRole, frozenset[OrgAction]] = {
    OrgRole.OWNER: frozenset(OrgAction),
    OrgRole.ADMIN: frozenset({OrgAction.MANAGE_STAFF, OrgAction.MANAGE_ROLES}),
    OrgRole.AGENT: frozenset(),
    OrgRole.CREATOR: frozenset(),
    OrgRole.ARTIST: frozenset(),
    OrgRole.USER: frozenset(),
}


def roles_from_account(account: Mapping[str, object]) -> frozenset[OrgRole]:
    """Translate current ESP account fields without changing their storage contract."""

    status = str(account.get("esp_status") or "").strip().lower()
    raw_roles = str(account.get("esp_roles") or "").strip().lower()
    if status == "owner":
        return frozenset({OrgRole.OWNER})
    if status != "active":
        return frozenset({OrgRole.USER})
    roles: set[OrgRole] = set()
    for value in raw_roles.replace("+", ",").split(","):
        value = value.strip()
        if value == "both":
            roles.update({OrgRole.CREATOR, OrgRole.AGENT})
        elif value in {role.value for role in OrgRole if role is not OrgRole.OWNER}:
            roles.add(OrgRole(value))
    return frozenset(roles or {OrgRole.USER})


class OrgAuthority:
    """Server-side ESP role/action policy independent of commercial plans."""

    def __init__(
        self,
        role_permissions: Mapping[OrgRole, Iterable[OrgAction]] | None = None,
    ) -> None:
        source = role_permissions or DEFAULT_ROLE_PERMISSIONS
        self.role_permissions = {
            role: frozenset(actions) for role, actions in source.items()
        }

    def is_allowed(self, roles: Iterable[OrgRole], action: OrgAction) -> bool:
        return any(action in self.role_permissions.get(role, frozenset()) for role in roles)

    def require(self, roles: Iterable[OrgRole], action: OrgAction) -> None:
        role_set = frozenset(roles)
        if not self.is_allowed(role_set, action):
            names = sorted(role.value for role in role_set)
            raise OrgAuthorityDeniedError(
                f"ESP roles {names!r} are not authorised for {action.value!r}"
            )
