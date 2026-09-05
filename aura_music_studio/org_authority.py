from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import Enum


class OrgAuthorityDeniedError(PermissionError):
    """Raised when ESP organisational authority is not explicitly granted."""


class OrgRole(str, Enum):
    OWNER = "owner"
    ADMIN = "admin"
    AGENT = "agent"
    MODERATOR = "moderator"
    CREATOR = "creator"
    ARTIST = "artist"
    USER = "user"


class OrgAction(str, Enum):
    MANAGE_STAFF = "manage_staff"
    MANAGE_ROLES = "manage_roles"
    VIEW_FINANCES = "view_finances"
    MANAGE_FINANCES = "manage_finances"
    MANAGE_RELEASES = "manage_releases"
    MODERATE_LIVE_MUTE_USER = "moderate_live_mute_user"
    MODERATE_LIVE_REMOVE_COMMENT = "moderate_live_remove_comment"
    MODERATE_LIVE_TIMEOUT_USER = "moderate_live_timeout_user"
    MODERATE_LIVE_REMOVE_VIEWER = "moderate_live_remove_viewer"
    MODERATE_LIVE_ESCALATE_REPORT = "moderate_live_escalate_report"
    MODERATE_LIVE_FLAG_STREAM = "moderate_live_flag_stream"


LIVE_MODERATION_ACTIONS = frozenset(
    {
        OrgAction.MODERATE_LIVE_MUTE_USER,
        OrgAction.MODERATE_LIVE_REMOVE_COMMENT,
        OrgAction.MODERATE_LIVE_TIMEOUT_USER,
        OrgAction.MODERATE_LIVE_REMOVE_VIEWER,
        OrgAction.MODERATE_LIVE_ESCALATE_REPORT,
        OrgAction.MODERATE_LIVE_FLAG_STREAM,
    }
)


DEFAULT_ROLE_PERMISSIONS: dict[OrgRole, frozenset[OrgAction]] = {
    OrgRole.OWNER: frozenset(OrgAction),
    OrgRole.ADMIN: frozenset({OrgAction.MANAGE_STAFF, OrgAction.MANAGE_ROLES}),
    OrgRole.AGENT: frozenset(),
    OrgRole.MODERATOR: LIVE_MODERATION_ACTIONS,
    OrgRole.CREATOR: frozenset(),
    OrgRole.ARTIST: frozenset(),
    OrgRole.USER: frozenset(),
}


def _role_tokens(raw: object) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, str):
        values = raw.replace("+", ",").split(",")
    elif isinstance(raw, (list, tuple, set, frozenset)):
        values = raw
    else:
        values = (raw,)
    return tuple(str(value).strip().lower() for value in values if str(value).strip())


def roles_from_account(account: Mapping[str, object]) -> frozenset[OrgRole]:
    """Translate current ESP account fields while keeping Moderator additive."""

    status = str(account.get("esp_status") or "").strip().lower()
    if status == "owner":
        return frozenset({OrgRole.OWNER})
    if status != "active":
        return frozenset({OrgRole.USER})

    roles: set[OrgRole] = set()
    primary_roles = _role_tokens(account.get("esp_roles"))
    allowed_primary = {
        OrgRole.ADMIN.value,
        OrgRole.AGENT.value,
        OrgRole.CREATOR.value,
        OrgRole.ARTIST.value,
        OrgRole.USER.value,
    }
    for value in primary_roles:
        if value == "both":
            roles.update({OrgRole.CREATOR, OrgRole.AGENT})
        elif value in allowed_primary:
            roles.add(OrgRole(value))

    # Moderator is deliberately not accepted from esp_roles. Owners assign it
    # separately as an additive permission/role, and it becomes effective only
    # while the same active account also has the Agent role.
    additional = (
        _role_tokens(account.get("esp_permissions"))
        + _role_tokens(account.get("esp_additional_roles"))
    )
    if OrgRole.AGENT in roles and OrgRole.MODERATOR.value in additional:
        roles.add(OrgRole.MODERATOR)

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
        role_set = frozenset(roles)
        if OrgRole.OWNER in role_set:
            return action in self.role_permissions.get(OrgRole.OWNER, frozenset())
        if action in LIVE_MODERATION_ACTIONS:
            return (
                OrgRole.AGENT in role_set
                and OrgRole.MODERATOR in role_set
                and action in self.role_permissions.get(OrgRole.MODERATOR, frozenset())
            )
        return any(
            action in self.role_permissions.get(role, frozenset()) for role in role_set
        )

    def require(self, roles: Iterable[OrgRole], action: OrgAction) -> None:
        role_set = frozenset(roles)
        if not self.is_allowed(role_set, action):
            names = sorted(role.value for role in role_set)
            raise OrgAuthorityDeniedError(
                f"ESP roles {names!r} are not authorised for {action.value!r}"
            )
