from __future__ import annotations

from collections.abc import Callable
from typing import FrozenSet

from pydantic import Field

from .org_authority import (
    LIVE_MODERATION_ACTIONS,
    OrgAction,
    OrgAuthority,
    OrgRole,
)
from .plans import get_plan
from .shared_contracts import ContractModel, NonEmptyId, OwnerOverrideEvidence


class EntitlementDeniedError(PermissionError):
    """Commercial/product access failed independently of organisational authority."""


class InvalidOverrideError(PermissionError):
    """Owner override evidence was absent, mismatched, or unaudited."""


class AuthorizationContext(ContractModel):
    user_id: NonEmptyId
    org_roles: FrozenSet[OrgRole] = Field(default_factory=lambda: frozenset({OrgRole.USER}))
    plan_id: NonEmptyId = "free"
    membership_active: bool = False
    explicit_entitlements: FrozenSet[str] = Field(default_factory=frozenset)


OverrideAuditWriter = Callable[[OwnerOverrideEvidence, str], None]


def require_org_authority(
    context: AuthorizationContext,
    action: OrgAction,
    *,
    authority: OrgAuthority | None = None,
) -> None:
    (authority or OrgAuthority()).require(context.org_roles, action)


def require_live_moderation_authority(
    context: AuthorizationContext,
    action: OrgAction,
    *,
    authority: OrgAuthority | None = None,
) -> None:
    """Require the shared Agent + Moderator gate for a LIVE moderation action."""

    if action not in LIVE_MODERATION_ACTIONS:
        raise ValueError(f"{action.value!r} is not a LIVE moderation action")
    require_org_authority(context, action, authority=authority)


def _validate_override(
    *,
    context: AuthorizationContext,
    override: OwnerOverrideEvidence | None,
    audit_writer: OverrideAuditWriter | None,
    purpose: str,
) -> bool:
    if override is None:
        return False
    if OrgRole.OWNER not in context.org_roles or override.owner_user_id != context.user_id:
        raise InvalidOverrideError("Owner override evidence does not match the authorised Owner.")
    if audit_writer is None:
        raise InvalidOverrideError("Owner overrides must be written to the audit trail.")
    audit_writer(override, purpose)
    return True


def require_product_entitlement(
    context: AuthorizationContext,
    *,
    feature_key: str,
    override: OwnerOverrideEvidence | None = None,
    audit_writer: OverrideAuditWriter | None = None,
) -> None:
    """Enforce product access without deriving it from any ESP role."""

    if feature_key in context.explicit_entitlements:
        return
    plan = get_plan(context.plan_id)
    if context.membership_active and plan.has(feature_key):
        return
    if _validate_override(
        context=context,
        override=override,
        audit_writer=audit_writer,
        purpose=f"feature:{feature_key}",
    ):
        return
    raise EntitlementDeniedError(
        f"Product feature {feature_key!r} is required; ESP role does not grant it."
    )


def require_authorized_feature(
    context: AuthorizationContext,
    *,
    org_action: OrgAction | None = None,
    feature_key: str | None = None,
    override: OwnerOverrideEvidence | None = None,
    audit_writer: OverrideAuditWriter | None = None,
    authority: OrgAuthority | None = None,
) -> None:
    """Apply organisational and commercial checks as independent policy dimensions."""

    if org_action is not None:
        require_org_authority(context, org_action, authority=authority)
    if feature_key is not None:
        require_product_entitlement(
            context,
            feature_key=feature_key,
            override=override,
            audit_writer=audit_writer,
        )
