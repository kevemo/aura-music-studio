from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from aura_music_studio.api_errors import ApiError, ApiErrorCode
from aura_music_studio.authorization import (
    AuthorizationContext,
    EntitlementDeniedError,
    InvalidOverrideError,
    require_authorized_feature,
    require_live_moderation_authority,
    require_product_entitlement,
)
from aura_music_studio.capabilities import (
    CapabilityRegistry,
    CapabilityStatus,
    derive_provider_capability,
)
from aura_music_studio.events import EventEnvelope
from aura_music_studio.feature_routes import ROUTES, RouteImplementationState, RouteRegistry
from aura_music_studio.org_authority import (
    LIVE_MODERATION_ACTIONS,
    OrgAction,
    OrgAuthorityDeniedError,
    OrgRole,
    roles_from_account,
)
from aura_music_studio.plans import MULTITRACK_DAW
from aura_music_studio.runtime_config import provider_config_from_env
from aura_music_studio.shared_contracts import OwnerOverrideEvidence, OrgRoleGrant, UserIdentity

NOW = datetime.now(timezone.utc)


def test_contracts_forbid_extra_and_naive_datetimes():
    with pytest.raises(ValidationError):
        UserIdentity(user_id="u1", unexpected=True)
    with pytest.raises(ValidationError):
        OrgRoleGrant(
            grant_id="g1",
            user_id="u1",
            role=OrgRole.AGENT,
            granted_by="owner",
            granted_at=datetime.now(),
        )


def test_account_role_mapping_preserves_creator_agent_both():
    assert roles_from_account({"esp_status": "owner"}) == frozenset({OrgRole.OWNER})
    assert roles_from_account(
        {"esp_status": "active", "esp_roles": "both"}
    ) == frozenset({OrgRole.CREATOR, OrgRole.AGENT})
    assert roles_from_account({"esp_status": "none"}) == frozenset({OrgRole.USER})


def test_moderator_is_separate_additive_permission_and_requires_agent():
    agent_only = roles_from_account({"esp_status": "active", "esp_roles": "agent"})
    assert agent_only == frozenset({OrgRole.AGENT})

    # Moderator cannot be smuggled into the primary ESP role field.
    smuggled = roles_from_account(
        {"esp_status": "active", "esp_roles": "agent,moderator"}
    )
    assert smuggled == frozenset({OrgRole.AGENT})

    agent_moderator = roles_from_account(
        {
            "esp_status": "active",
            "esp_roles": "agent",
            "esp_permissions": ["moderator"],
        }
    )
    assert agent_moderator == frozenset({OrgRole.AGENT, OrgRole.MODERATOR})

    creator_moderator = roles_from_account(
        {
            "esp_status": "active",
            "esp_roles": "creator",
            "esp_additional_roles": "moderator",
        }
    )
    assert creator_moderator == frozenset({OrgRole.CREATOR})

    agent_context = AuthorizationContext(user_id="agent", org_roles=agent_only)
    dual_context = AuthorizationContext(user_id="dual", org_roles=agent_moderator)
    creator_context = AuthorizationContext(user_id="creator", org_roles=creator_moderator)
    owner_context = AuthorizationContext(
        user_id="owner", org_roles=frozenset({OrgRole.OWNER})
    )

    for action in LIVE_MODERATION_ACTIONS:
        with pytest.raises(OrgAuthorityDeniedError):
            require_live_moderation_authority(agent_context, action)
        with pytest.raises(OrgAuthorityDeniedError):
            require_live_moderation_authority(creator_context, action)
        require_live_moderation_authority(dual_context, action)
        require_live_moderation_authority(owner_context, action)

    with pytest.raises(ValueError):
        require_live_moderation_authority(dual_context, OrgAction.MANAGE_FINANCES)


def test_paid_plan_never_grants_org_authority():
    context = AuthorizationContext(
        user_id="u1",
        org_roles=frozenset({OrgRole.USER}),
        plan_id="pro",
        membership_active=True,
    )
    with pytest.raises(OrgAuthorityDeniedError):
        require_authorized_feature(
            context,
            org_action=OrgAction.MANAGE_FINANCES,
            feature_key=MULTITRACK_DAW,
        )

    paid_agent = AuthorizationContext(
        user_id="agent",
        org_roles=frozenset({OrgRole.AGENT}),
        plan_id="pro",
        membership_active=True,
    )
    with pytest.raises(OrgAuthorityDeniedError):
        require_live_moderation_authority(
            paid_agent, OrgAction.MODERATE_LIVE_TIMEOUT_USER
        )


def test_owner_role_never_silently_grants_paid_entitlement():
    context = AuthorizationContext(
        user_id="owner",
        org_roles=frozenset({OrgRole.OWNER}),
        plan_id="free",
        membership_active=True,
    )
    with pytest.raises(EntitlementDeniedError):
        require_product_entitlement(context, feature_key=MULTITRACK_DAW)


def test_owner_override_requires_matching_evidence_and_audit():
    context = AuthorizationContext(
        user_id="owner",
        org_roles=frozenset({OrgRole.OWNER}),
        plan_id="free",
        membership_active=True,
    )
    evidence = OwnerOverrideEvidence(
        override_id="ovr1",
        owner_user_id="owner",
        reason="Incident recovery",
        correlation_id="corr1",
        approved_at=NOW,
    )
    with pytest.raises(InvalidOverrideError):
        require_product_entitlement(
            context, feature_key=MULTITRACK_DAW, override=evidence
        )
    seen = []
    require_product_entitlement(
        context,
        feature_key=MULTITRACK_DAW,
        override=evidence,
        audit_writer=lambda item, purpose: seen.append((item.override_id, purpose)),
    )
    assert seen == [("ovr1", f"feature:{MULTITRACK_DAW}")]


def test_capability_registry_is_server_derived():
    state = derive_provider_capability(
        key="stream.youtube",
        provider="youtube",
        implemented=True,
        owner_enabled=True,
        feature_flag_enabled=True,
        credentials_present=False,
        approval_granted=False,
    )
    assert state.status is CapabilityStatus.CREDENTIALS_MISSING
    assert not state.available
    registry = CapabilityRegistry([state])
    with pytest.raises(Exception):
        registry.require_available("stream.youtube")


def test_provider_state_distinguishes_unconfigured_disabled_and_missing_credentials():
    unconfigured = provider_config_from_env(
        provider="youtube",
        capability_key="stream.youtube",
        credential_env="YOUTUBE_SECRET",
        prefix="YOUTUBE",
        implemented=True,
        environ={},
    ).capability_state()
    assert unconfigured.status is CapabilityStatus.NOT_CONFIGURED

    disabled = provider_config_from_env(
        provider="youtube",
        capability_key="stream.youtube",
        credential_env="YOUTUBE_SECRET",
        prefix="YOUTUBE",
        implemented=True,
        environ={"YOUTUBE_ENABLED": "false"},
    ).capability_state()
    assert disabled.status is CapabilityStatus.DISABLED

    missing_credentials = provider_config_from_env(
        provider="youtube",
        capability_key="stream.youtube",
        credential_env="YOUTUBE_SECRET",
        prefix="YOUTUBE",
        implemented=True,
        environ={
            "YOUTUBE_ENABLED": "true",
            "YOUTUBE_FEATURE_FLAG": "true",
            "YOUTUBE_APPROVED": "true",
        },
    ).capability_state()
    assert missing_credentials.status is CapabilityStatus.CREDENTIALS_MISSING


def test_runtime_config_never_exposes_secret():
    config = provider_config_from_env(
        provider="youtube",
        capability_key="stream.youtube",
        credential_env="YOUTUBE_SECRET",
        prefix="YOUTUBE",
        implemented=True,
        environ={
            "YOUTUBE_SECRET": "super-secret",
            "YOUTUBE_ENABLED": "true",
            "YOUTUBE_FEATURE_FLAG": "true",
            "YOUTUBE_APPROVED": "true",
        },
    )
    assert "super-secret" not in repr(config.public_payload())
    assert config.public_payload()["available"] is True


def test_public_errors_and_event_audit_metadata_scrub_secrets():
    bearer = "Bearer very-sensitive-token-value"
    placeholder_secret = "test-provider-secret-placeholder"
    error = ApiError(
        code=ApiErrorCode.VALIDATION_FAILED,
        message=f"Provider rejected {bearer}",
        correlation_id="corr-public-error",
        details={"api_key": placeholder_secret, "note": bearer},
    )
    public = error.public_payload()
    serialized = repr(public)
    assert "very-sensitive-token-value" not in serialized
    assert placeholder_secret not in serialized

    internal = ApiError(
        code=ApiErrorCode.INTERNAL_ERROR,
        message="Traceback: /srv/app/secret.py line 9",
        correlation_id="corr-internal",
        details={"stack": "Traceback: sensitive implementation detail"},
    ).public_payload()
    assert internal["message"] == "Internal error"
    assert internal["details"] == {}

    event = EventEnvelope(
        event_id="evt-1",
        type="test.event",
        subject_type="test",
        subject_id="subject-1",
        occurred_at=NOW,
        correlation_id="corr-event",
        source="tests",
        audit_metadata={"note": bearer},
    )
    assert "very-sensitive-token-value" not in repr(event.audit_metadata)
    with pytest.raises(ValidationError):
        EventEnvelope(
            event_id="evt-2",
            type="test.event",
            subject_type="test",
            subject_id="subject-2",
            occurred_at=NOW,
            correlation_id="corr-event-2",
            source="tests",
            audit_metadata={"access_token": "must-never-be-accepted"},
        )


def test_routes_reflect_verified_merged_owners_without_fabricating_pending_domains():
    required = {
        "shared_sky",
        "live_now",
        "battles",
        "gifts_cosmic_coins",
        "go_live_create",
    }
    assert required.issubset({route.key for route in ROUTES.all()})
    live_now = ROUTES.get("live_now")
    assert live_now.path == "/live-now"
    assert live_now.implementation_state is RouteImplementationState.READY
    for key in required - {"live_now"}:
        assert ROUTES.get(key).implementation_state is RouteImplementationState.INTEGRATION_PENDING
    with pytest.raises(ValueError):
        RouteRegistry([ROUTES.get("shared_sky"), ROUTES.get("shared_sky")])
