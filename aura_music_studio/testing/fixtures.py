from __future__ import annotations

from datetime import datetime, timezone

from aura_music_studio.access_control import ESPRole
from aura_music_studio.authorization import AuthorizationContext
from aura_music_studio.capabilities import CapabilityRecord, CapabilityStatus
from aura_music_studio.events import EventEnvelope
from aura_music_studio.membership import MembershipStatus, MembershipTier
from aura_music_studio.shared_contracts import OwnerOverrideEvidence, UserIdentity


def test_user(*, user_id: str = "test-user") -> UserIdentity:
    return UserIdentity(user_id=user_id, account_id=f"acct-{user_id}")


def test_authorization_context(
    *,
    user_id: str = "test-user",
    org_role: ESPRole = ESPRole.USER,
    membership_tier: MembershipTier = MembershipTier.FREE,
    entitlements: frozenset[str] = frozenset(),
) -> AuthorizationContext:
    return AuthorizationContext(
        user_id=user_id,
        org_role=org_role,
        membership_tier=membership_tier,
        membership_status=MembershipStatus.ACTIVE,
        explicit_entitlements=entitlements,
    )


def test_capability(
    *,
    key: str = "test.capability",
    status: CapabilityStatus = CapabilityStatus.NOT_CONFIGURED,
) -> CapabilityRecord:
    return CapabilityRecord(
        key=key,
        status=status,
        reason="Local test fixture; not production provider evidence.",
        provider="test",
        owner_enabled=True,
    )


def test_event(
    *,
    event_id: str = "evt-test-1",
    event_type: str = "test.event",
    subject_type: str = "test",
    subject_id: str = "subject-test-1",
    correlation_id: str = "corr-test-1",
) -> EventEnvelope:
    return EventEnvelope(
        event_id=event_id,
        type=event_type,
        subject_type=subject_type,
        subject_id=subject_id,
        occurred_at=datetime.now(timezone.utc),
        correlation_id=correlation_id,
        source="test",
    )


def test_owner_override(
    *,
    owner_user_id: str = "owner-test",
    correlation_id: str = "corr-test-override",
) -> OwnerOverrideEvidence:
    return OwnerOverrideEvidence(
        override_id="override-test-1",
        owner_user_id=owner_user_id,
        reason="Explicit local test override.",
        correlation_id=correlation_id,
        approved_at=datetime.now(timezone.utc),
    )
