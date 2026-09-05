from __future__ import annotations

from collections.abc import Callable
from typing import FrozenSet

from pydantic import Field

from .access_control import AccessControl, AccessDeniedError, ESPAction, ESPRole
from .membership import MembershipStatus, MembershipTier, TIER_RANK
from .shared_contracts import ContractModel, NonEmptyId, OwnerOverrideEvidence


class EntitlementDeniedError(AccessDeniedError):
    """Commercial/product access failed independently of organisational authority."""


class InvalidOverrideError(AccessDeniedError):
    """Owner override evidence was absent, mismatched, or unaudited."""


class AuthorizationContext(ContractModel):
    user_id: NonEmptyId
    org_role: ESPRole
    membership_tier: MembershipTier = MembershipTier.FREE
    membership_status: MembershipStatus = MembershipStatus.ACTIVE
    explicit_entitlements: FrozenSet[str] = Field(default_factory=frozenset)


OverrideAuditWriter = Callable[[OwnerOverrideEvidence, str], None]


def require_org_authority(
    context: AuthorizationContext,
    action: ESPAction,
    *,
    access_control: AccessControl | None = None,
) -> None:
    (access_control or AccessControl()).require(context.org_role, action)


def _tier_satisfies(context: AuthorizationContext, minimum_tier: MembershipTier | None) -> bool:
    if minimum_tier is None:
        return True
    return (
        context.membership_status is MembershipStatus.ACTIVE
        and TIER_RANK[context.membership_tier] >= TIER_RANK[minimum_tier]
    )


def _validate_override(
    *,
    context: AuthorizationContext,
    override: OwnerOverrideEvidence | None,
    audit_writer: OverrideAuditWriter | None,
    purpose: str,
) -> bool:
    if override is None:
        return False
    if context.org_role is not ESPRole.OWNER or override.owner_user_id != context.user_id:
        raise InvalidOverrideError("Owner override evidence does not match the authorised Owner.")
    if audit_writer is None:
        raise InvalidOverrideError("Owner overrides must be written to the audit trail.")
    audit_writer(override, purpose)
    return True


def require_product_entitlement(
    context: AuthorizationContext,
    *,
    entitlement_key: str | None = None,
    minimum_tier: MembershipTier | None = None,
    override: OwnerOverrideEvidence | None = None,
    audit_writer: OverrideAuditWriter | None = None,
) -> None:
    """Enforce product access without deriving it from an ESP organisational role."""

    if entitlement_key is None and minimum_tier is None:
        raise ValueError("an entitlement key or minimum tier is required")
    explicit = bool(entitlement_key and entitlement_key in context.explicit_entitlements)
    tier_allowed = minimum_tier is not None and _tier_satisfies(context, minimum_tier)
    if explicit or tier_allowed:
        return
    purpose = entitlement_key or f"membership:{minimum_tier.value if minimum_tier else 'unknown'}"
    if _validate_override(
        context=context,
        override=override,
        audit_writer=audit_writer,
        purpose=purpose,
    ):
        return
    raise EntitlementDeniedError(
        f"Product entitlement {purpose!r} is required; ESP role does not grant it."
    )


def require_authorized_feature(
    context: AuthorizationContext,
    *,
    org_action: ESPAction | None = None,
    entitlement_key: str | None = None,
    minimum_tier: MembershipTier | None = None,
    override: OwnerOverrideEvidence | None = None,
    audit_writer: OverrideAuditWriter | None = None,
    access_control: AccessControl | None = None,
) -> None:
    """Apply organisational and commercial checks as independent policy dimensions."""

    if org_action is not None:
        require_org_authority(context, org_action, access_control=access_control)
    if entitlement_key is not None or minimum_tier is not None:
        require_product_entitlement(
            context,
            entitlement_key=entitlement_key,
            minimum_tier=minimum_tier,
            override=override,
            audit_writer=audit_writer,
        )
