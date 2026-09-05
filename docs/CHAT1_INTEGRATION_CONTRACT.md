# Chat 1 Integration Contract

Developer handoff for the shared integration backbone. This is not a second product/master specification.

## Security boundary

Commercial/product entitlement and ESP organisational authority are independent.

- Existing product access remains driven by `plans.py` / `Plan.has(...)` and active membership state.
- Existing membership middleware remains in `access_control.py` unchanged.
- ESP organisational role/action policy lives in `org_authority.py`.
- A paid plan never grants Owner/Admin/Agent/Creator authority.
- An ESP role never grants a paid plan feature.
- Owner commercial overrides require explicit `OwnerOverrideEvidence` and an audit callback.

## Canonical imports

```python
from aura_music_studio.shared_contracts import (
    AssetReference, BattleRoundReference, BattleSessionReference, BroadcastReference,
    CorrelationIdentity, CosmicCoinLedgerTransactionReference, CreatorIdentityReference,
    CreatorReceiptReference, EngagementEventReference, FeatureEntitlement,
    GiftCatalogueItemReference, LiveGiftReference, LiveSessionReference,
    ModerationReference, OrgRoleGrant, OwnerOverrideEvidence, ParticipantReference,
    ProductEntitlement, ProjectIdentity, RequestIdentity, StreamingDestinationReference,
    UserIdentity, WorkspaceIdentity,
)
from aura_music_studio.org_authority import OrgAction, OrgAuthority, OrgRole, roles_from_account
from aura_music_studio.authorization import (
    AuthorizationContext, require_authorized_feature,
    require_org_authority, require_product_entitlement,
)
from aura_music_studio.capabilities import (
    CapabilityRecord, CapabilityRegistry, CapabilityStatus,
    ProviderCapabilityState, derive_provider_capability,
)
from aura_music_studio.events import EventEnvelope, OutboxPublisher
from aura_music_studio.shared_persistence import SharedPersistence, canonical_request_hash
from aura_music_studio.shared_audit import AuditWriter
from aura_music_studio.runtime_config import ProviderRuntimeConfig, provider_config_from_env
from aura_music_studio.feature_routes import FeatureRoute, RouteRegistry, ROUTES
from aura_music_studio.api_errors import ApiError, ApiErrorCode
```

Shared test objects are imported from `aura_music_studio.testing`. Fixtures identify themselves as test data and are never production provider evidence.

## Persistence

`SharedPersistence.initialize()` applies additive schema version 1. It creates `shared_schema_migrations`, `shared_idempotency`, and `shared_event_outbox` without replacing existing domain tables.

Use `SharedPersistence.transaction()` plus `enqueue_event(..., connection=connection)` when a domain mutation and consequential event must commit atomically. Reusing an idempotency key with a different canonical request hash is a conflict, never a replay.

## Capability truth

Only server-known facts create provider capability state. Clients may display public snapshots but cannot declare credentials, platform approval, user eligibility or provider health. Statuses are centralized in `CapabilityStatus`.

Configuration presence and enablement are distinct: absent provider configuration is `not_configured`, an explicit Owner disable is `disabled`, and configured providers continue through credential, approval, eligibility and health gates.

## Feature discovery

`ROUTES` registers Shared Sky, Live Now, Battles, Gifts & Cosmic Coins, and Go Live & Create.

- `live_now` is wired to the merged Chat 4 public route `GET /live-now` through `aura_music_studio.shared_sky_live_community:router` and is marked `ready` at the route-wiring layer.
- A ready route is not a fabricated provider-success claim: Live Now still fails closed for individual sessions unless canonical broadcast and playback-readiness checks pass.
- Shared Sky root, Battles, Gifts & Cosmic Coins, and Go Live & Create remain `integration_pending` until their owning workstreams provide verified merged wiring.

Pending entries use an unavailable fallback and never mimic success.

## Audit and public error safety

`AuditWriter` wraps the existing SQLite `AuditLedger`; it does not create a competing audit store. Existing hash chaining and AuraSec DLP sanitisation remain the persistence boundary.

`ApiError.public_payload()` and `EventEnvelope.audit_metadata` reuse AuraSec DLP so server credentials, credential-shaped values and internal stack details are not exposed through shared public/audit surfaces. Internal errors return a generic public message plus correlation identity.

## Cross-workstream rules

1. Reuse existing durable string IDs; do not force a UUID migration.
2. Validate untrusted cross-domain payloads with the Pydantic contracts.
3. Keep roles, product entitlements, balances and provider capability server-authoritative.
4. Reuse canonical shared status values instead of inventing duplicate strings.
5. Put domain-specific data in versioned `EventEnvelope.payload`.
6. Derive external-provider capability from server configuration and approval evidence.
7. A UI label, feature branch or adapter is never provider-success evidence.
8. When an owning workstream is merged, update `ROUTES` to its verified route path/state instead of leaving stale provisional discovery metadata.
