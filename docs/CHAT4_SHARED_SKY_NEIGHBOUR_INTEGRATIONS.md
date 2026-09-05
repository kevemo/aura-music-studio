# Chat 4 Shared Sky Neighbour Integration Extension

This extension is part of the **Chat 4 Shared Sky Live Now & Community Integration Contract** and documents the runtime compatibility wiring added after the first Chat 4 viewer-network checkpoint merged.

## Purpose

Chat 4 remains the viewer-facing LIVE network. This extension binds canonical neighbouring modules when they are present without creating permanent duplicate transport, financial or Battle systems.

The integration bootstrap remains fail-closed. A missing neighbouring module does not make a capability appear available.

## Canonical runtime module

```python
from aura_music_studio.shared_sky_live_integrations import (
    Chat2PlaybackAdapter,
    SharedSkyGiftLiveSessionDirectory,
    Chat5GiftDisplayAdapter,
    configure_neighbor_live_integrations,
    integration_status,
)
```

`aura_music_studio.shared_sky_live_bootstrap.install_shared_sky_live_community(...)` calls `configure_neighbor_live_integrations()` during canonical application composition.

The bootstrap uses import-time immutable route snapshots, so repeated installation remains idempotent even when the repository's late router composition has already flattened source routers.

## Chat 2 playback integration

When `aura_music_studio.shared_sky_transport_domain.transport` is importable, Chat 4 registers `Chat2PlaybackAdapter` as the viewer playback adapter.

The adapter:

- resolves the canonical creator/owner from `shared_sky_broadcasts`;
- calls Chat 2 `transport.status(owner_user_id, broadcast_id)`;
- consumes the `playback` descriptor already returned by Chat 2;
- exposes playback only when Chat 2 reports `capability_state=ready` and transport is `live`, `degraded` or `reconnecting`;
- passes through the server-issued manifest URL and authorization token rather than signing or constructing them itself;
- maps Chat 2 rendition-profile metadata into a viewer-facing rendition list without pretending the browser can switch variants unless the player/runtime supports it;
- exposes no invented captions or DVR state;
- fails closed on missing/invalid transport state.

Viewer refresh route:

- `GET /shared-sky/live/api/watch/{broadcast_id}/playback`

The route independently re-checks direct Watch access before returning a fresh descriptor/token.

### Replay

Chat 2 recording handoff is authoritative. Chat 4 prefers `programme` then `clean_feed` recordings.

A completed recording that has an `asset_id` but no tenant-safe replay resolver is returned as `state=asset_ready`, `available=false`, `reason=replay_asset_resolver_unavailable`. Chat 4 does not expose Chat 2 storage URIs and does not invent a blob URL.

## Chat 5 LIVE Gift integration

When both `aura_music_studio.cosmic_economy` and `aura_music_studio.cosmic_economy_integrations` are importable, Chat 4:

1. supplies `SharedSkyGiftLiveSessionDirectory` to Chat 5 through `configure_economy_integrations(live_sessions=...)`;
2. constructs Chat 5's canonical `economy_service()`;
3. registers `Chat5GiftDisplayAdapter` for Watch display state.

`SharedSkyGiftLiveSessionDirectory` establishes only these facts from canonical Shared Sky broadcast state:

- the LIVE session exists;
- the canonical recipient creator owns that broadcast;
- the session is currently `live`.

It does **not** make age/region eligibility, Coin pricing, spending, risk, payout, Gift reversal or Battle-scoring decisions.

`Chat5GiftDisplayAdapter` projects:

- active Gift catalogue/version/cost and presentation references supplied by Chat 5;
- sender/receiver eligibility supplied by Chat 5;
- the signed-in viewer's authoritative balance and spending-warning state supplied by Chat 5;
- the canonical creator recipient and LIVE session ID;
- `send_endpoint=/economy/me/gifts/send`;
- `idempotency_required=true`.

Chat 4 never directly mutates `coin_accounts`, `coin_ledger_entries`, `gift_transactions`, `creator_gift_receipts`, payout policy or financial reconciliation data.

Viewer refresh route:

- `GET /shared-sky/live/api/watch/{broadcast_id}/gift-display`

Gift success must still be returned by Chat 5's authoritative send command and committed outbox/event state before Chat 4 renders success/animation.

## Chat 6 Battle integration

No canonical Chat 6 Battle module was present when this extension was authored. `chat6_battles` therefore remains explicitly `pending` and the existing unavailable Battle display adapter remains active.

Chat 4 will not infer a Battle score from reactions, viewer counts or Gifts while that contract is absent.

## Integration capability route

- `GET /shared-sky/live/api/integration-status`

Current keys:

- `chat2_playback`
- `chat5_gifts`
- `chat6_battles`

States are compatibility truth (`registered`, `pending`, or `degraded`) and not provider/runtime readiness claims. For example, a registered Chat 2 adapter can still truthfully return playback unavailable if the LL-HLS origin/signing deployment is not configured.

## Tests

`tests/test_shared_sky_live_integrations.py` verifies:

- Chat 2's server-issued manifest/token are preserved without local construction;
- non-ready playback fails closed and does not leak a manifest;
- replay asset IDs are not converted into invented playback URLs;
- canonical LIVE/creator validation for Chat 5;
- Gift display is a projection of Chat 5 state rather than a financial mutation;
- eligibility blocks disable sending;
- integration/refresh routes mount idempotently.

## Merge sequencing

This extension is safe to merge before Chat 2/5/6 because dynamic registration fails closed while their modules are absent.

After a neighbouring PR merges, Chat 11 (or the relevant integration workstream) must run the full repository suite on the combined ancestry. A module being importable is not sufficient evidence that production media origin, payment provider, provider approval, age/region policy or distributed realtime infrastructure exists.
