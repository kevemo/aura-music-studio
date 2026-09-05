# Chat 1 Integration Contract

This is a developer handoff for the shared integration backbone. It is not a product/master specification.

## Security boundary

Commercial/product entitlement and ESP organisational authority are independent. Never infer an ESP role from a paid plan, and never infer a paid feature from an ESP role. Owner overrides are explicit evidence objects and must be audited.

## Canonical imports

```python
from aura_music_studio.shared_contracts import (
    AssetReference,
    BattleRoundReference,
    BattleSessionReference,
    BroadcastReference,
    CorrelationIdentity,
    CosmicCoinLedgerTransactionReference,
    CreatorIdentityReference,
    CreatorReceiptReference,
    EngagementEventReference,
    FeatureEntitlement,
    GiftCatalogueItemReference,
    LiveGiftReference,
    LiveSessionReference,
    ModerationReference,
    OrgRoleGrant,
    OwnerOverrideEvidence,
    ParticipantReference,
    ProductEntitlement,
    ProjectIdentity,
    RequestIdentity,
    StreamingDestinationReference,
    UserIdentity,
    WorkspaceIdentity,
)
```

```python
from aura_music_studio.authorization import (
    AuthorizationContext,
    require_authorized_feature,
    require_org_authority,
    require_product_entitlement,
)
from aura_music_studio.capabilities import (
    CapabilityRecord,
    CapabilityRegistry,
    CapabilityStatus,
    ProviderCapabilityState,
    derive_provider_capability,
)
from aura_music_studio.events import EventEnvelope, OutboxPublisher
from aura_music_studio.shared_persistence import (
    SharedPersistence,
    canonical_request_hash,
)
from aura_music_studio.shared_audit import AuditWriter
from aura_music_studio.runtime_config import (
    ProviderRuntimeConfig,
    provider_config_from_env,
)
from aura_music_studio.feature_routes import FeatureRoute, RouteRegistry, ROUTES
from aura_music_studio.api_errors import ApiError, ApiErrorCode
```

Shared test objects are imported from:

```python
from aura_music_studio.testing import (
    test_authorization_context,
    test_capability,
    test_event,
    test_owner_override,
    test_user,
)
```

Fixtures intentionally identify themselves as test data and never represent provider configuration or approval.

## Persistence contract

`SharedPersistence.initialize()` applies additive schema version 1 and does not replace account, membership, economy, creator, project or domain storage.

It creates:

- `shared_schema_migrations`
- `shared_idempotency`
- `shared_event_outbox`

Use `SharedPersistence.transaction()` when a domain mutation and `enqueue_event(..., connection=connection)` must commit atomically. The outbox is broker-neutral; a later queue adapter can publish the same validated `EventEnvelope` without changing domain contracts.

Idempotency keys are scoped by the calling domain. Reusing a key with a different canonical request hash is a conflict, never a replay.

## Capability truth

Only server-known facts create `CapabilityRecord` state. Clients may display `public_snapshot()` / `public_payload()` but must not decide that credentials, platform approval, account eligibility or provider health exist.

Supported status values are centralized in `CapabilityStatus`.

## Feature discovery

`ROUTES` registers Shared Sky, Live Now, Battles, Gifts & Cosmic Coins, and Go Live & Create as discovery entries. They remain `integration_pending` until their owning workstream replaces that state with verified implementation wiring. The fallback is deliberately unavailable; it must not mimic success.

## Audit contract

Use `AuditWriter` for consequential cross-domain changes. It preserves the existing `AuditLogger` hash-chain storage and adds correlation IDs, safe state diffs, explicit override evidence and sensitive-key redaction. Do not create a separate domain audit table.

## API contract

Use `ApiError` / `ApiErrorCode` for stable UI-safe failure semantics. Internal exceptions, provider tokens, stack traces and filesystem details stay server-side; client errors carry a correlation ID.

## Cross-workstream rules

1. Reuse existing durable string IDs; do not force a UUID migration.
2. Validate untrusted payloads with the Pydantic contracts rather than TypeScript/Python type hints alone.
3. Preserve server authority for roles, entitlements, balances, provider capability and consequential state.
4. Do not add a second enum/string for a shared status when a canonical value exists here.
5. Domain-specific payloads live in the versioned `EventEnvelope.payload`; cross-domain envelope fields stay stable.
6. New external providers must derive capability truth from server configuration and approval evidence.
7. A feature branch or UI label is never provider-success evidence.
