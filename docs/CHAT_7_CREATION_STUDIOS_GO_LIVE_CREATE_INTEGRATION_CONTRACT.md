# Chat 7 Creation Studios Go Live & Create Integration Contract

Status: active Chat 7 implementation for `development/full-site-build`.

This workstream owns the privacy-safe Music / Video-Cinema / Image-Visual project-source bridge into Shared Sky. It does not own transport, the professional control-room compositor, viewer/community truth, Coin/Gift finance or Battle scoring.

## Current integrated baseline

Chat 7 was refreshed onto `development/full-site-build` commit `1a6976a32a0deb832aca2ef983b899811ee1f92b` with two-parent merge commit `bc3905411bf278aaeab644eb34273567be55bd00`.

At that integration checkpoint:

- Chat 2 transport PR #570 is merged and is canonical transport authority.
- Chat 4 first-party LIVE/community PR #571 is merged and is canonical viewer/community authority.
- Chat 3 PR #572 remains open; Chat 7 therefore still fails closed for exact project-source Programme/ON-AIR confirmation and BRB/cut authority.
- Chat 5 PR #573 remains open and is not part of the integration branch. Chat 7 never imports its financial implementation directly.
- Chat 6 has an authoritative Battle branch, but no verified Chat 6 Battle contract is merged into the integration branch at this checkpoint. Chat 7 therefore consumes only Chat 4's registered Battle display adapter state.
- Chat 11 retains repository-wide release acceptance and final deployment ownership.

## Canonical Chat 7 modules

```python
from aura_music_studio.creation_live import (
    CreationLiveSourceDescriptor,
    CreationLiveStore,
    RightsPreflight,
    SourceCapabilities,
    creation_live_store,
    discover_sources,
)
```

Additional production layers:

- `aura_music_studio.creation_live_hardening`
  - source expiry/session-end revocation;
  - pre-side-effect idempotency reservation;
  - stale/revoked handle rejection;
  - discovery reconciliation;
  - browser capture cleanup.
- `aura_music_studio.creation_live_authority`
  - current-source rights/privacy revalidation before attach;
  - Chat 2 transport preflight projection;
  - authoritative LIVE-marker/session checks;
  - Chat 2 recording-state reconciliation for post-LIVE returns.
- `aura_music_studio.creation_live_community`
  - read-only Chat 4 creator-side community projection derived from the server-owned project/source association.
- `aura_music_studio.creation_live_ui_community`
  - read-only community section embedded in the Go Live & Create drawer.
- `aura_music_studio.route_integrity`
  - idempotent canonical route composition and duplicate-route/OpenAPI-ID reconciliation.

No second FastAPI application, transport service, chat service, wallet, Gift ledger or Battle engine is introduced.

## Embedded studio entry points

The same Go Live & Create system is embedded into:

- Music Studio: `/studio`
- Video/Cinema Studio: `/video-studio`
- Image/Poster/Visual Studio: `/image-designer`

Client script:

- `GET /creation-live/ui.js`

The editor project remains open. Opening the drawer does not start LIVE, start browser capture or attach a project source.

## Shared source descriptor

`CreationLiveSourceDescriptor` schema version 1 carries only safe cross-system data:

- `source_adapter_id`;
- adapter/schema version;
- `studio_type`: `music | video_cinema | image_visual`;
- canonical project/workspace/creator IDs;
- studio-specific source type;
- safe display name;
- media kind;
- aspect/fps/sample/channel capability metadata when known;
- source capability flags;
- privacy classification;
- allow-listed inclusion manifest;
- exclusion-policy reference;
- rights/preflight result;
- Shared Sky project/broadcast/source references after authorised attachment;
- presentation mode;
- health/version/timestamps/revocation/correlation references;
- optional Creative element/version IDs;
- preview kind.

It never serialises the whole creative-project document, raw server/local paths, destination credentials, provider tokens, OAuth material or private storage URLs.

## Durable state

Chat 7 adds additive SQLite tables in the canonical Shared Sky database:

- `creation_live_sources`
- `creation_live_idempotency`
- `creation_live_returns`
- `creation_live_markers`

`creation_live_sources.version` is the optimistic-concurrency token. `active_editor_instance_id` prevents stale duplicate editor tabs from controlling a newer attached source.

Transient browser `MediaStream` objects and browser permission grants are never persisted.

## Safe discovery policy

Source identity is stable per creator/project/studio/server-owned source key.

### Music

Normal safe sources currently include:

- ready active Creative music/audio outputs;
- allow-listed files under the project's `output/` tree;
- explicitly selected stems with an additional confirmation boundary;
- advanced whole-workspace capture only after explicit opt-in.

Obvious `private`, `reference`, `training` and `models` paths are not surfaced as normal Music sources.

### Video/Cinema

Normal safe source:

- current active ready Creative video output, resolved and confined to the current tenant project.

Advanced whole-workspace capture is separate and higher risk.

### Image/Visual

Normal safe source:

- current active ready Creative image/artwork output, resolved and confined to the current tenant project.

Advanced whole-workspace capture is separate and higher risk.

## Source lifecycle hardening

The durable source lifecycle now covers:

- discovery;
- preview;
- attach/register;
- ready/registered state;
- presentation-mode transition;
- detach;
- revoke;
- expiry;
- rediscovery/reissue after a new current safe source is deliberately selected.

Additional rules:

- expired handles revoke fail closed;
- a missing or terminal linked Shared Sky session revokes the handle;
- a revoked handle cannot silently reactivate;
- a project output no longer present in the current allow list is revoked;
- safe rediscovery clears obsolete broadcast/transport/editor ownership references;
- attach/detach idempotency keys are reserved with `BEGIN IMMEDIATE` before side effects, preventing concurrent duplicate transport work;
- failed side effects release the in-progress reservation so a legitimate retry can proceed.

## Rights / consent / provenance

Preflight blocks or warns on the current authoritative metadata, including:

- hidden/private/restricted/collaborator-only/internal-only material;
- `broadcast_allowed=false`;
- secret/provider/security metadata;
- real-person likeness without LIVE permission;
- missing/revoked/ineligible Voice Profiles;
- music cover/remix/backing projects lacking required rights confirmation.

Voice generation permission does not imply LIVE permission. Existing Voice Profiles must explicitly permit `live_streaming` and consent must remain active.

Missing rights metadata is never rewritten into a false `cleared for broadcast` result.

Immediately before an attach, `creation_live_authority` rediscovers the chosen project source and re-runs current rights/privacy eligibility. A stale source descriptor is not sufficient authority.

## Whole-workspace mode

`full_workspace` is always advanced/high-risk and never the default.

It requires:

- explicit rights/privacy warning confirmation;
- `full_workspace_confirmed=true`;
- browser `getDisplayMedia` permission.

The UI states that browser/window capture cannot guarantee application-level masking of unrelated notifications or windows.

Repeated preview requests stop the previous browser capture first. `MediaStreamTrack.onended` clears the preview and reports that the source is no longer available.

## Chat 2 transport integration — merged

Canonical import:

```python
from aura_music_studio.shared_sky_transport_domain import transport
```

Chat 7 uses the merged Chat 2 APIs rather than a local transport backend:

- `transport.register_source(...)`
- `transport.source(...)`
- `transport.preflight(user_id, broadcast_id)`
- `transport.status(user_id, broadcast_id)`

Source registration uses an opaque `creation-live://<source_adapter_id>` reference plus safe media/privacy capabilities. A persisted transport source ID is reused when valid.

Attach now reports Chat 2 preflight separately. Source registration or a passing transport preflight still does **not** prove the exact project source is on Programme.

Post-LIVE return reconciles recording state and authoritative `asset_id` from `transport.status(...)` when available. It projects only safe recording metadata; storage URIs/provider internals are not returned through Chat 7.

Chat 7 does not own destination OAuth, provider credentials, ingest/relay/transcoding or external-destination delivery truth.

## Chat 3 control-room handoff — pending merge

Chat 7 supplies:

- source adapter ID;
- safe display label;
- privacy classification;
- capabilities;
- correlation ID;
- Shared Sky source/session references.

Chat 3 owns:

- scene/source placement;
- transforms/crop;
- Preview/Programme;
- transitions/cuts;
- mixer/audio composition;
- overlays/widgets;
- programme output.

Until the merged Chat 3 contract exposes an authoritative exact-source Programme query, Chat 7 intentionally keeps `on_air=false` and `programme_state=unknown` rather than inferring ON AIR from broadcast state.

Emergency hide currently records `brb` presentation intent but does not claim a real Programme cut occurred without Chat 3 authority.

## Chat 4 community integration — merged

`GET /creation-live/projects/{project_name}/community` now consumes the merged `shared_sky_live_community.community` store.

The project-side endpoint derives the active LIVE session from Chat 7's server-owned project/source association. It does not accept a client-selected broadcast ID.

Safe creator-side projection includes:

- authoritative LIVE state;
- current Shared Sky viewer count and its count definition;
- chat settings;
- recent internal chat history;
- reaction aggregates;
- Chat 4 registered Gift display state;
- Chat 4 registered Battle display state;
- canonical Chat 4 chat/events/poll/Q&A action paths.

The embedded drawer displays current viewer/chat/reaction state using DOM text nodes. Incoming chat/reactions never become creative-editor commands automatically.

The endpoint reports:

- `project_mutated=false`;
- `financial_mutation=false`;
- `battle_score_mutation=false`.

Chat 7 does not duplicate Chat 4 presence, chat, polls, Q&A, moderation or realtime event backends.

## Chat 5 and Chat 6 boundaries

Until their verified canonical contracts are merged into the integration branch:

- Gift state is consumed only through Chat 4's registered display adapter;
- Chat 7 never debits Coins or calculates balances/liabilities/payouts;
- Battle state is consumed only through Chat 4's registered display adapter;
- Chat 7 never creates a Battle, ticks a Battle timer or calculates a score.

## Create → showcase state model

One project-source association changes presentation intent without creating another hidden LIVE session.

Music modes:

- creating
- tutorial
- rehearsal
- performance
- premiere
- showcase
- listening_party
- brb
- detached

Video modes:

- creating
- tutorial
- review
- premiere
- showcase
- brb
- detached

Image modes:

- creating
- tutorial
- review
- showcase
- gallery
- brb
- detached

## Markers and post-LIVE return

Markers now require:

- source state `registered` or `ready`;
- marker session ID equal to the source's server-owned broadcast linkage;
- Shared Sky session in an active marker-eligible state.

Markers never mutate project creative content.

Returned recording/highlight provenance is deduplicated by `(user_id, project_name, return_import_id)` and retains project/studio/session/recording/source/time/category/correlation references.

When Chat 2 recording authority is available, client-declared `ready` cannot override the transport recording state. `ready` requires an authoritative recording asset ID.

A tenant-safe media resolver is still required before returned recording bytes can be materialised into a creative media library; Chat 7 does not invent a local file.

## Aura boundary

Aura assistance may recommend a safe current source/preset and explain warnings.

It explicitly cannot autonomously:

- start/stop LIVE;
- reveal hidden material;
- switch a private version onto Programme;
- enable whole-workspace capture;
- bypass rights/voice/likeness restrictions.

## Main API surface

- `GET /creation-live/capabilities`
- `GET /creation-live/projects/{project_name}/sources?studio_type=...`
- `GET /creation-live/projects/{project_name}/sources/{source_adapter_id}`
- `GET /creation-live/projects/{project_name}/sources/{source_adapter_id}/media`
- `POST /creation-live/projects/{project_name}/sources/{source_adapter_id}/attach`
- `POST /creation-live/projects/{project_name}/sources/{source_adapter_id}/transition`
- `POST /creation-live/projects/{project_name}/sources/{source_adapter_id}/emergency-hide`
- `POST /creation-live/projects/{project_name}/sources/{source_adapter_id}/detach`
- `GET /creation-live/shared-sky/broadcasts`
- `POST /creation-live/projects/{project_name}/markers`
- `POST /creation-live/projects/{project_name}/returns`
- `GET /creation-live/projects/{project_name}/community`
- `GET /creation-live/projects/{project_name}/aura-assistance?studio_type=...`
- `GET /creation-live/ui.js`

## Error/state additions

Important Chat 7 errors include:

- `stale_source_version`
- `source_controlled_by_another_editor`
- `source_revoked`
- `operation_in_progress`
- `idempotency_key_reused_with_different_request`
- `project_rights_blocked`
- `source_not_ready`
- `live_session_ended`
- `marker_session_mismatch`
- `return_session_mismatch`
- `return_asset_mismatch`
- `recording_not_found`
- `recording_asset_processing`

## Deterministic test suites

Chat 7 currently adds:

- `tests/test_creation_live.py`
- `tests/test_creation_live_hardening.py`
- `tests/test_creation_live_authority.py`
- `tests/test_creation_live_community.py`

Coverage includes descriptor secrecy, rights/voice boundaries, tenant/stable identity, optimistic concurrency, duplicate-editor rejection, pre-side-effect idempotency, expiry/revocation, rediscovery, browser capture cleanup, route idempotency, authoritative recording mapping, Chat 4 contract consumption and read-only community UI safety.

Repository CI/security/self-host checks remain the acceptance authority.

## Remaining genuine blockers / technical debt

1. Chat 3 exact project-source Programme/BRB authority is not merged yet.
2. Chat 5 Coin/Gift authority is not merged into this integration branch; Chat 7 remains display-only through Chat 4.
3. Chat 6 authoritative Battle/participant contract is not merged into this integration branch; Chat 7 remains display-only through Chat 4.
4. Music `/studio` still needs a canonical low-latency browser master/programme-bus `MediaStream` from the DAW runtime for true live graph contribution. Current Chat 7 Music source uses real ready project audio output rather than faking such a bus.
5. Video/Image editors still need canonical application-owned render-canvas/capture handles for low-latency clean canvas contribution. Current Chat 7 uses real ready clean project output rather than pretending `canvas.captureStream()` is already wired.
6. Returned Chat 2 recording binaries need a canonical tenant-safe media-asset resolver before physical project-library import.
7. Browser/device matrix, longer soak/leak tests and media-plane capacity/resource telemetry remain Chat 10/11 handoff items.
8. Final production enablement/deployment remains outside Chat 7.
