# Chat 7 Creation Studios Go Live & Create Integration Contract

Status: active Chat 7 implementation for `development/full-site-build`.

Chat 7 owns the privacy-safe Music / Video-Cinema / Image-Visual project-source bridge into Shared Sky. It does not own media transport, the professional Preview/Programme control room, viewer/community authority, Coin/Gift finance, Battle scoring, production infrastructure, or final release approval.

## Current integrated baseline

Chat 7 is reconciled onto `development/full-site-build` commit `736b2835773a28844c4ee5f01f9c9df43c8ae835` through two-parent merge commit `2eee6b04d8f097415260a56a4b6305c28e649368`.

That integration baseline includes:

- Chat 2 transport authority and first-party Shared Sky HLS/recording runtime;
- Chat 4 viewer/community authority;
- Chat 10/11 signed Shared Sky media-plane control layer;
- Chat 11 repository-wide integration/release ownership.

Current neighbouring ownership state:

- Chat 3 PR #572 remains open, so exact project-source Programme/ON-AIR confirmation and real BRB/cut authority remain fail-closed in Chat 7.
- Chat 5 financial authority is not imported into Chat 7. Gift state remains display-only through Chat 4's owning adapter boundary.
- Chat 6 Battle authority is not imported into Chat 7. Battle state remains display-only through Chat 4's owning adapter boundary.
- Chat 7 does not call the signed media-plane command layer directly. Media-plane orchestration remains behind Chat 2/Chat 10/owner control boundaries.

## Canonical Chat 7 modules

Core source model and UI:

- `aura_music_studio.creation_live`
  - typed source descriptor;
  - durable source store;
  - studio source discovery;
  - rights/privacy preflight;
  - local safe preview;
  - embedded Go Live & Create UI.

Lifecycle hardening:

- `aura_music_studio.creation_live_hardening`
  - source expiry/session-end revocation;
  - pre-side-effect idempotency reservation;
  - stale/revoked handle rejection;
  - discovery reconciliation;
  - active-session lease handling;
  - browser capture cleanup.

Consequential authority:

- `aura_music_studio.creation_live_authority`
  - current-source rights/privacy revalidation immediately before attach;
  - Chat 2 attach-time preflight;
  - authoritative LIVE marker/session checks;
  - Chat 2 recording-state reconciliation for post-LIVE returns.

Transport truth projection:

- `aura_music_studio.creation_live_transport_truth`
  - strict browser-safe Chat 2 preflight projection;
  - side-effect-free persisted transport status projection;
  - explicit registration / transport / Programme separation;
  - source-selection truth without exposing exact transport source IDs;
  - creator UI wording that never equates transport readiness with ON AIR.

Community projection:

- `aura_music_studio.creation_live_community`
  - read-only Chat 4 creator-side community projection derived from the server-owned project/source association.
- `aura_music_studio.creation_live_ui_community`
  - read-only viewer/chat/reaction/Gift/Battle display section in the creator drawer.

Route composition:

- `aura_music_studio.creation_live_routes`
  - immutable endpoint specification and fresh router factory;
  - direct canonical app registration path where required by the repository's overlay composition model.
- `aura_music_studio.route_integrity`
  - deterministic Chat 7 route composition;
  - exact duplicate-route removal;
  - OpenAPI operation-ID reconciliation.

No second FastAPI application, transport service, chat service, wallet, Gift ledger, Battle engine, or media-plane command service is introduced.

## Embedded creator entry points

The same Go Live & Create workflow is embedded into:

- Music Studio: `/studio`
- Video/Cinema Studio: `/video-studio`
- Image/Poster/Visual Studio: `/image-designer`

Client script:

- `GET /creation-live/ui.js`

Opening the drawer does not start LIVE, start browser capture, or attach a project source.

## Shared source descriptor

`CreationLiveSourceDescriptor` schema version 1 contains only bounded cross-system data:

- stable `source_adapter_id`;
- adapter/schema version;
- `studio_type`: `music | video_cinema | image_visual`;
- canonical project/workspace/creator IDs;
- studio-specific source type;
- safe display label;
- media kind and known media capability metadata;
- privacy classification;
- allow-listed inclusion manifest;
- exclusion-policy reference;
- rights/preflight result;
- Shared Sky project/broadcast/source references after authorised attachment;
- presentation mode;
- health/version/timestamps/revocation/correlation references;
- optional Creative element/version IDs;
- preview kind.

The public descriptor never includes raw server paths, storage paths, destination credentials, provider tokens, OAuth material, stream keys, passwords, private storage URLs, collaborator email addresses, training references, or arbitrary provider payloads.

## Durable state and concurrency

Chat 7 adds additive SQLite state in the canonical Shared Sky database:

- `creation_live_sources`
- `creation_live_idempotency`
- `creation_live_returns`
- `creation_live_markers`

Key invariants:

- `creation_live_sources.version` is the optimistic-concurrency token;
- `active_editor_instance_id` prevents stale duplicate tabs from taking over an attached source;
- attach/detach idempotency is reserved with `BEGIN IMMEDIATE` before side effects;
- concurrent duplicate operations return an in-progress conflict rather than executing twice;
- failed side effects release their reservation for legitimate retry;
- transient browser `MediaStream` objects and permission grants are never persisted.

## Safe source discovery

Source identity is deterministic per authenticated creator, project, studio, and server-owned source key.

### Music

Normal source options include:

- ready active Creative music/audio output;
- allow-listed project `output/` audio files;
- explicitly selected stems with additional confirmation;
- lyrics/chords/visualiser presentation sources when the project exposes a safe representation.

Obvious private/reference/training/model paths are excluded.

### Video/Cinema

Normal source options include the current active ready Creative video output and safe viewer/presentation variants when backed by current project state.

### Image/Visual

Normal source options include the current active ready Creative image/artwork output and safe selected-canvas/gallery/presentation variants when backed by current project state.

### Advanced whole-workspace capture

`full_workspace` is always high risk and never the default. It requires explicit creator acknowledgement plus browser `getDisplayMedia` permission.

The UI states that browser/window capture cannot guarantee masking of unrelated notifications or other windows. Repeated capture stops the previous `MediaStream` first, and `MediaStreamTrack.onended` immediately clears the preview state.

## Source lifecycle

The source lifecycle covers:

- discovery;
- safe preview;
- attach/register;
- registered/ready state;
- presentation-mode transition;
- detach;
- revoke;
- expiry;
- terminal-session reconciliation;
- deliberate rediscovery/reissue after the current safe output changes.

Rules include:

- expired handles revoke fail closed;
- missing or terminal linked Shared Sky sessions revoke linked source handles;
- revoked handles do not silently reactivate;
- outputs removed from the current allow list are revoked;
- safe rediscovery clears obsolete session/transport/editor ownership references;
- stale source versions and stale editor instances are rejected.

## Rights, consent, privacy and provenance

Preflight blocks or warns on current authoritative project state, including:

- hidden/private/restricted/collaborator-only/internal-only material;
- `broadcast_allowed=false`;
- prohibited secret/security/provider metadata;
- real-person likeness without LIVE consent;
- missing, revoked or ineligible Voice Profiles;
- cover/remix/backing projects lacking required rights confirmation.

Voice generation permission does not imply LIVE permission. Existing Voice Profiles must explicitly allow `live_streaming` and consent must remain active.

Missing rights metadata is never rewritten into a false `cleared` state.

Immediately before attach, Chat 7 rediscovers the current project source and re-runs current rights/privacy eligibility. A stale descriptor is never sufficient authority.

## Chat 2 transport integration

Canonical import:

```python
from aura_music_studio.shared_sky_transport_domain import transport
```

Chat 7 consumes the merged Chat 2 contract rather than implementing transport locally:

- `transport.register_source(...)`
- `transport.source(...)`
- `transport.preflight(user_id, broadcast_id)`
- `transport.status(user_id, broadcast_id)`

Source registration uses an opaque `creation-live://<source_adapter_id>` reference plus bounded safe capabilities. Existing valid Chat 2 source registration is reused.

### Attach-time preflight

Attach calls Chat 2's canonical preflight. Chat 7 then projects only these safe issue fields:

- `code`
- `scope`
- `destination_id` when present and safe
- bounded human-readable `message`
- readiness state and a bounded correlation ID.

Chat 7 does not forward trace IDs, endpoints, provider payloads, credentials, internal playback authorization, arbitrary nested future fields, or other debug/provider metadata.

### Refresh/status truth

`GET /creation-live/projects/{project_name}/sources/{source_adapter_id}` is side-effect-free. It reads persisted `transport.status(...)` rather than rerunning preflight.

The browser-safe response distinguishes:

1. source registration state;
2. last transport validation state;
3. whether a transport source is selected;
4. whether the selected transport source corresponds to this Chat 7 source;
5. transport session state/health;
6. exact-source Programme state from the owning control-room boundary;
7. final `on_air` truth.

Exact Chat 2 source IDs are reduced to booleans for the browser and are not exposed by this status projection.

Critical invariant:

> A registered source, a passing transport preflight, or even an active transport session does not prove this project source is on Programme.

## Signed Shared Sky media-plane layer

The integration baseline now includes the Chat 10/11 signed Shared Sky media-plane control layer.

Chat 7 does **not** call its `/v1/commands` boundary directly and does not hold its HMAC secret. Chat 7 remains upstream source metadata/rights/creator-UX authority only.

The ownership path remains:

- Chat 7: creator project source, rights/privacy, source lifecycle, safe presentation intent;
- Chat 2: transport/session/ingest/relay/recording authority;
- Chat 3: Preview/Programme composition and exact-source ON-AIR authority;
- Chat 10: production media-plane/security/operational controls;
- Chat 11: final integrated acceptance/release.

This prevents a creative editor from bypassing transport, Programme, or production security controls by issuing media-plane commands directly.

## Chat 3 control-room handoff — still pending merge

Chat 7 supplies safe source identity/capability/session linkage for the control room.

Chat 3 owns:

- scene/source placement;
- transforms/crop;
- Preview/Programme;
- transitions/cuts;
- audio composition;
- overlays/widgets;
- programme output;
- real emergency BRB/cut authority.

Until an exact-source Programme contract is merged, Chat 7 intentionally reports `on_air=false` / `programme_state=unknown` rather than inferring ON AIR from Shared Sky broadcast or transport state.

`/emergency-hide` currently records BRB presentation intent. It never falsely claims a real Programme cut occurred.

## Chat 4 creator community integration

`GET /creation-live/projects/{project_name}/community` derives the active session from the authenticated project/source association and consumes the canonical Chat 4 community store.

Safe creator-side projection includes:

- authoritative Shared Sky LIVE state;
- current viewer count and count definition;
- chat settings;
- recent internal chat history;
- reaction aggregates;
- registered Gift display state;
- registered Battle display state;
- canonical Chat 4 chat/events/poll/Q&A action paths.

Incoming community activity never mutates creative project content automatically.

The projection explicitly reports:

- `project_mutated=false`
- `financial_mutation=false`
- `battle_score_mutation=false`

Chat messages are rendered with DOM text nodes, not unsafe HTML.

## Chat 5 and Chat 6 boundaries

Chat 7 remains read-only across finance and Battle state:

- no Coin debit or wallet mutation;
- no Gift financial transaction or creator liability calculation;
- no payout/reversal/refund logic;
- no Battle creation, timer, round state or score calculation.

Display state is consumed through Chat 4's owning adapter boundary only.

## Create to showcase without duplicate LIVE sessions

One project-source association can change presentation intent while retaining the same LIVE session.

Music modes include creating, tutorial, rehearsal, performance, premiere, showcase, listening party, BRB and detached.

Video modes include creating, tutorial, review, premiere, showcase, BRB and detached.

Image modes include creating, tutorial, review, showcase, gallery, BRB and detached.

A presentation-mode change is not a transport start and does not prove Programme state.

## Markers and post-LIVE return

Markers require:

- source state `registered` or `ready`;
- marker session ID equal to the source's server-owned broadcast linkage;
- Shared Sky session in an active marker-eligible state.

Markers do not mutate project creative content.

Returned recording/highlight provenance is deduplicated by `(user_id, project_name, return_import_id)` and retains project/studio/session/recording/source/time/category/correlation references.

When Chat 2 recording authority is available, client-declared `ready` cannot override authoritative recording state. Ready import requires an authoritative recording asset ID.

Chat 7 deliberately strips Chat 2 storage URI, checksum and provider internals from its public recording projection.

A canonical tenant-safe media resolver is still required before returned recording bytes can be physically materialised into a creative media library.

## Aura boundary

Aura may recommend a safe current source/preset and explain warnings.

Aura cannot autonomously:

- start or stop LIVE;
- reveal hidden/private source material;
- switch a private version to Programme;
- enable whole-workspace capture;
- bypass rights, voice or likeness restrictions;
- issue Chat 2 transport mutations without the creator's consequential action path;
- issue signed media-plane commands;
- debit Coins or score Battles.

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

## Important errors/states

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

Transport projection states include `ready`, `blocked`, `not_validated`, `preflight_unavailable`, `status_unavailable`, `broadcast_not_selected` and compatibility-pending states when an owning contract is not present.

## Deterministic Chat 7 regression suites

Current focused coverage includes:

- `tests/test_creation_live.py`
- `tests/test_creation_live_active_lease.py`
- `tests/test_creation_live_authority.py`
- `tests/test_creation_live_community.py`
- `tests/test_creation_live_community_ui_runtime.py`
- `tests/test_creation_live_hardening.py`
- `tests/test_creation_live_live_revalidation.py`
- `tests/test_creation_live_preview_cleanup.py`
- `tests/test_creation_live_rights_reconciliation.py`
- `tests/test_creation_live_route_factory.py`
- `tests/test_creation_live_route_isolation.py`
- `tests/test_creation_live_transport_integration.py`
- `tests/test_creation_live_transport_truth.py`

Coverage includes descriptor secrecy, recursive private metadata rejection, rights/voice/likeness boundaries, tenant identity, optimistic concurrency, duplicate-editor rejection, pre-side-effect idempotency, expiry/revocation, active-session lease handling, safe rediscovery, browser capture cleanup, route factory/isolation, authoritative recording mapping, Chat 4 read-only community safety, Chat 2 source-registration reuse, strict transport preflight field whitelisting, persisted transport status projection, and explicit proof that transport-ready does not mean ON AIR.

Repository-wide Command Center CI, Security Gates and Self-Host Smoke remain the acceptance authority. A green result from an earlier ancestor is not sufficient after a branch or integration change.

## Remaining genuine blockers / technical debt

1. Chat 3 exact project-source Programme/BRB authority is not merged yet.
2. Chat 5 Coin/Gift financial authority is not owned or mutated by Chat 7; only display state is consumed here.
3. Chat 6 Battle/participant authority is not owned or mutated by Chat 7; only display state is consumed here.
4. Music `/studio` still needs a canonical low-latency browser master/programme-bus `MediaStream` from the DAW runtime for true live graph contribution. Chat 7 uses real ready project audio rather than fabricating a graph tap.
5. Video/Image still need canonical application-owned clean render-canvas/capture handles for low-latency clean canvas contribution. Chat 7 uses real ready clean project output rather than pretending `canvas.captureStream()` is wired.
6. Returned Chat 2 recording binaries still need a canonical tenant-safe media-asset resolver before physical project-library import.
7. Camera/microphone selection is not yet fully bound to a shared control-room/media graph; Chat 7 does not fake that integration.
8. Source version/snapshot pinning is represented only partially; a complete immutable snapshot-selection UI remains future work.
9. Chat 2 source registration is control-plane metadata registration. It does not by itself prove media contribution is flowing.
10. Browser/device matrix, longer capture leak/soak testing and capacity/resource telemetry remain Chat 10/11 acceptance items.
11. Final production release/deployment remains outside Chat 7 and belongs to Chat 11.
