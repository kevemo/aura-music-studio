from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from .org_authority import OrgAction, OrgRole

NonEmptyId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1024)]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        raise ValueError("datetime must include timezone information")
    return value


class AssetProvenance(str, Enum):
    UPLOAD = "upload"
    GENERATED = "generated"
    IMPORTED = "imported"
    EXTERNAL_PROVIDER = "external_provider"
    DERIVED = "derived"


class LiveSessionStatus(str, Enum):
    SCHEDULED = "scheduled"
    PREPARING = "preparing"
    LIVE = "live"
    ENDED = "ended"
    FAILED = "failed"
    CANCELED = "canceled"


class ParticipantRole(str, Enum):
    HOST = "host"
    COHOST = "cohost"
    GUEST = "guest"
    VIEWER = "viewer"
    MODERATOR = "moderator"


class ReceiptStatus(str, Enum):
    PENDING = "pending"
    RECORDED = "recorded"
    REVERSED = "reversed"
    REFUNDED = "refunded"
    DISPUTED = "disputed"


class UserIdentity(ContractModel):
    user_id: NonEmptyId
    account_id: NonEmptyId | None = None


class OrgRoleGrant(ContractModel):
    grant_id: NonEmptyId
    user_id: NonEmptyId
    role: OrgRole
    granted_by: NonEmptyId
    granted_at: datetime
    revoked_at: datetime | None = None
    _dates = field_validator("granted_at", "revoked_at")(_aware)


class ProductEntitlement(ContractModel):
    user_id: NonEmptyId
    plan_id: NonEmptyId
    active: bool
    feature_key: NonEmptyId | None = None
    source: NonEmptyText
    valid_until: datetime | None = None
    _until = field_validator("valid_until")(_aware)


class FeatureEntitlement(ContractModel):
    user_id: NonEmptyId
    feature_key: NonEmptyId
    enabled: bool
    source: NonEmptyText
    valid_until: datetime | None = None
    _until = field_validator("valid_until")(_aware)


class OwnerOverrideEvidence(ContractModel):
    override_id: NonEmptyId
    owner_user_id: NonEmptyId
    reason: NonEmptyText
    correlation_id: NonEmptyId
    approved_at: datetime
    _approved = field_validator("approved_at")(_aware)


class CreatorIdentityReference(ContractModel):
    creator_id: NonEmptyId
    user_id: NonEmptyId
    handle: str | None = Field(default=None, max_length=128)


class WorkspaceIdentity(ContractModel):
    workspace_id: NonEmptyId
    owner_user_id: NonEmptyId


class ProjectIdentity(ContractModel):
    project_id: NonEmptyId
    workspace_id: NonEmptyId
    owner_user_id: NonEmptyId


class AssetReference(ContractModel):
    asset_id: NonEmptyId
    version_id: NonEmptyId
    workspace_id: NonEmptyId
    project_id: NonEmptyId | None = None
    provenance: AssetProvenance
    source_asset_id: NonEmptyId | None = None
    provider: str | None = Field(default=None, max_length=128)


class LiveSessionReference(ContractModel):
    live_session_id: NonEmptyId
    creator_id: NonEmptyId
    status: LiveSessionStatus


class BroadcastReference(ContractModel):
    broadcast_id: NonEmptyId
    live_session_id: NonEmptyId


class StreamingDestinationReference(ContractModel):
    destination_id: NonEmptyId
    account_id: NonEmptyId
    provider: NonEmptyId


class ParticipantReference(ContractModel):
    participant_id: NonEmptyId
    live_session_id: NonEmptyId
    user_id: NonEmptyId | None = None
    creator_id: NonEmptyId | None = None
    role: ParticipantRole


class EngagementEventReference(ContractModel):
    engagement_event_id: NonEmptyId
    live_session_id: NonEmptyId
    actor_user_id: NonEmptyId | None = None
    kind: NonEmptyId


class GiftCatalogueItemReference(ContractModel):
    gift_id: NonEmptyId
    catalogue_version: NonEmptyId


class CosmicCoinLedgerTransactionReference(ContractModel):
    transaction_id: NonEmptyId
    ledger_account_id: NonEmptyId


class LiveGiftReference(ContractModel):
    gift_send_id: NonEmptyId
    live_session_id: NonEmptyId
    gift_id: NonEmptyId
    ledger_transaction_id: NonEmptyId


class CreatorReceiptReference(ContractModel):
    receipt_id: NonEmptyId
    creator_id: NonEmptyId
    gift_send_id: NonEmptyId
    status: ReceiptStatus


class BattleSessionReference(ContractModel):
    battle_id: NonEmptyId
    live_session_id: NonEmptyId


class BattleRoundReference(ContractModel):
    round_id: NonEmptyId
    battle_id: NonEmptyId
    ordinal: int = Field(ge=1)


class BattleScoreEventReference(ContractModel):
    score_event_id: NonEmptyId
    round_id: NonEmptyId


class ModerationReference(ContractModel):
    moderation_id: NonEmptyId
    target_type: NonEmptyId
    target_id: NonEmptyId


class AuditEventReference(ContractModel):
    audit_event_id: NonEmptyId
    correlation_id: NonEmptyId


class ProviderConfigurationReference(ContractModel):
    provider: NonEmptyId
    capability_key: NonEmptyId
    configuration_id: NonEmptyId | None = None


class RequestIdentity(ContractModel):
    request_id: NonEmptyId
    idempotency_key: NonEmptyId | None = None
    correlation_id: NonEmptyId
    trace_id: NonEmptyId | None = None


class CorrelationIdentity(ContractModel):
    correlation_id: NonEmptyId
    trace_id: NonEmptyId | None = None


__all__ = [
    "AssetProvenance", "AssetReference", "AuditEventReference", "BattleRoundReference",
    "BattleScoreEventReference", "BattleSessionReference", "BroadcastReference",
    "ContractModel", "CorrelationIdentity", "CosmicCoinLedgerTransactionReference",
    "CreatorIdentityReference", "CreatorReceiptReference", "EngagementEventReference",
    "FeatureEntitlement", "GiftCatalogueItemReference", "LiveGiftReference",
    "LiveSessionReference", "LiveSessionStatus", "ModerationReference", "NonEmptyId",
    "OrgAction", "OrgRole", "OrgRoleGrant", "OwnerOverrideEvidence", "ParticipantReference",
    "ParticipantRole", "ProductEntitlement", "ProjectIdentity", "ProviderConfigurationReference",
    "ReceiptStatus", "RequestIdentity", "StreamingDestinationReference", "UserIdentity",
    "WorkspaceIdentity",
]
