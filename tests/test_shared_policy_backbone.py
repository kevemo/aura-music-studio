from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from aura_music_studio.access_control import AccessDeniedError, ESPAction, ESPRole
from aura_music_studio.api_errors import ApiError, ApiErrorCode
from aura_music_studio.authorization import (
    AuthorizationContext,
    EntitlementDeniedError,
    InvalidOverrideError,
    require_authorized_feature,
    require_product_entitlement,
)
from aura_music_studio.capabilities import (
    CapabilityRegistry,
    CapabilityStatus,
    derive_provider_capability,
)
from aura_music_studio.feature_routes import ROUTES, RouteImplementationState, RouteRegistry
from aura_music_studio.membership import MembershipStatus, MembershipTier
from aura_music_studio.runtime_config import provider_config_from_env
from aura_music_studio.shared_contracts import OwnerOverrideEvidence, OrgRoleGrant, UserIdentity


NOW = datetime.now(timezone.utc)


def test_contracts_forbid_extra_and_naive_datetimes():
    with pytest.raises(ValidationError):
        UserIdentity(user_id="u1", unexpected=True)
    with pytest.raises(ValidationError):
        OrgRoleGrant(
            grant_id="g1", user_id="u1", role=ESPRole.AGENT, granted_by="owner",
            granted_at=datetime.now(),
        )


def test_paid_membership_never_grants_org_authority():
    context = AuthorizationContext(
        user_id="u1", org_role=ESPRole.USER,
        membership_tier=MembershipTier.STUDIO,
        membership_status=MembershipStatus.ACTIVE,
    )
    with pytest.raises(AccessDeniedError):
        require_authorized_feature(
            context, org_action=ESPAction.MANAGE_FINANCES,
            minimum_tier=MembershipTier.CREATOR,
        )


def test_owner_role_never_silently_grants_paid_entitlement():
    context = AuthorizationContext(
        user_id="owner", org_role=ESPRole.OWNER,
        membership_tier=MembershipTier.FREE,
        membership_status=MembershipStatus.ACTIVE,
    )
    with pytest.raises(EntitlementDeniedError):
        require_product_entitlement(context, minimum_tier=MembershipTier.PRO)


def test_owner_override_requires_matching_evidence_and_audit():
    context = AuthorizationContext(
        user_id="owner", org_role=ESPRole.OWNER,
        membership_tier=MembershipTier.FREE,
    )
    evidence = OwnerOverrideEvidence(
        override_id="ovr1", owner_user_id="owner", reason="Incident recovery",
        correlation_id="corr1", approved_at=NOW,
    )
    with pytest.raises(InvalidOverrideError):
        require_product_entitlement(
            context, minimum_tier=MembershipTier.PRO, override=evidence
        )
    seen = []
    require_product_entitlement(
        context, minimum_tier=MembershipTier.PRO, override=evidence,
        audit_writer=lambda item, purpose: seen.append((item.override_id, purpose)),
    )
    assert seen == [("ovr1", "membership:pro")]


def test_capability_registry_is_server_derived():
    state = derive_provider_capability(
        key="stream.youtube", provider="youtube", implemented=True,
        owner_enabled=True, feature_flag_enabled=True, credentials_present=False,
        approval_granted=False,
    )
    assert state.status is CapabilityStatus.CREDENTIALS_MISSING
    assert not state.available
    registry = CapabilityRegistry([state])
    with pytest.raises(Exception):
        registry.require_available("stream.youtube")
    public = registry.public_snapshot()[0]
    assert public["available"] is False


def test_runtime_config_never_exposes_secret():
    config = provider_config_from_env(
        provider="youtube", capability_key="stream.youtube",
        credential_env="YOUTUBE_SECRET", prefix="YOUTUBE", implemented=True,
        environ={
            "YOUTUBE_SECRET": "super-secret",
            "YOUTUBE_ENABLED": "true",
            "YOUTUBE_FEATURE_FLAG": "true",
            "YOUTUBE_APPROVED": "true",
        },
    )
    payload = config.public_payload()
    assert "super-secret" not in repr(payload)
    assert payload["available"] is True


def test_route_registry_has_required_discovery_routes_without_fake_ready_state():
    required = {"shared_sky", "live_now", "battles", "gifts_cosmic_coins", "go_live_create"}
    assert required.issubset({route.key for route in ROUTES.all()})
    assert all(
        route.implementation_state is RouteImplementationState.INTEGRATION_PENDING
        for route in ROUTES.all()
    )
    with pytest.raises(ValueError):
        RouteRegistry([ROUTES.get("shared_sky"), ROUTES.get("shared_sky")])


def test_api_error_has_stable_status_and_correlation():
    error = ApiError(
        code=ApiErrorCode.IDEMPOTENCY_CONFLICT,
        message="Request conflicts with an earlier request.",
        correlation_id="corr1",
    )
    assert error.http_status == 409
    assert error.public_payload()["correlation_id"] == "corr1"
