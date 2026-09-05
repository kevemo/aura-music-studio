# Chat 8 Game Forge & Game Go Live Integration Contract

Status: Chat 8 compatibility contract for `development/full-site-build`.

This document is intentionally narrow. Game Forge owns project/game source truth. Shared Sky transport, programme composition, community, Gifts and Battle truth remain in their canonical domains.

## Canonical Game Forge foundations reused

Game Forge already persists member-scoped Game DNA below the tenant-aware `projects_root()` via `aura_music_studio.game_forge_store`. It already has native 2D/3D runtime modules, world/gameplay/state-machine systems, media/model asset bindings, project continuity, export/build/playtest paths and deterministic tests. Chat 8 must extend those systems rather than create a second project store or engine.

Primary existing imports used by this integration:

- `aura_music_studio.game_forge_models.GameDNA`
- `aura_music_studio.game_forge_models.GameBuild`
- `aura_music_studio.game_forge_store.load_game`
- `aura_music_studio.game_forge_store.game_dir`
- `aura_music_studio.game_forge_project_binding.creative_project_name`
- `aura_music_studio.game_forge_api._creator`
- `aura_music_studio.aura_sandbox.AuraSandboxClient`

The Shared Sky compatibility consumer currently recognises `source_type="game_forge"`. Game Forge does not write destination OAuth tokens or claim destination delivery success.

## Chat 8 live module

Canonical module:

`aura_music_studio.game_forge_live_integration`

Mounted through:

`aura_music_studio.game_forge_project_binding.router`

Safe portal:

`GET /game-creation/live/{game_id}`

Project continuity responses expose:

`go_live_create_url=/game-creation/live/{game_id}`

## Durable live state

Per-member/per-game state is stored beneath the existing tenant-scoped game directory:

`projects/members/<request-user>/_games/<game_id>/live/shared_sky.json`

The path is resolved through `game_forge_store.game_dir`; callers do not supply filesystem paths.

State schema:

`GameForgeLiveState`

Fields:

- `schema_version`
- `project_id`
- `sources: dict[source_adapter_id, GameForgeSafeLiveSource]`
- `feedback: dict[feedback_id, GameForgeLiveFeedback]`
- `returns: dict[idempotency_key, GameForgeLiveReturnRecord]`
- `updated_at`

Writes use a same-directory temporary file followed by atomic replacement.

## Safe source schema

Canonical model:

`GameForgeSafeLiveSource`

Schema version:

`game_forge_live_source.v1`

Core fields:

- `source_adapter_id`
- `studio_type="game_forge"`
- `project_id`
- logical `workspace_id` when bound to a Creative project
- `creator_identity_ref`
- `live_session_id`
- optional `participant_ref`
- `source_type`
- `safe_display_label`
- `media_kind`
- `aspect_profile`
- pinned `project_version`
- pinned `build_id` where relevant
- project and audience visibility classifications
- `LiveInclusionManifest`
- fixed `exclusion_policy`
- rights readiness
- optional Shared Sky registration reference
- health/status/revocation state
- presentation mode
- optional opaque presentation-surface reference
- timestamps/correlation ID

The source descriptor never serialises the Game DNA document, scene document, source code, repository paths, environment variables, API keys, destination credentials or arbitrary caller-provided source configuration.

## Supported Game Forge source types

`GameForgeLiveSourceType`:

- `clean_game_output`
- `playtest_runtime`
- `approved_build_output`
- `selected_editor_viewport`
- `selected_scene_viewport`
- `coding_tutorial`
- `visual_logic`
- `profiler_tutorial`
- `creator_camera`
- `microphone`
- `game_audio`

The default is `clean_game_output`.

Gameplay/build source types require a real `latest_build` with `private_playtest_ready=true`. `approved_build_output` additionally requires a current approved test assessment whose content hash matches the build.

Editor, scene, coding, node-graph and profiler sources require an explicit opaque `presentation_surface_ref`. Paths and URLs are rejected. Whole-window capture is never the implicit implementation of these source types.

## Privacy exclusion contract

`LIVE_PRIVACY_EXCLUSIONS` is fixed code-owned policy and includes:

- API keys/tokens/environment variables
- signing/destination credentials
- Git/private repository credentials
- unselected private source files
- backend/database/admin consoles
- hidden debug/security logic
- unpublished monetisation configuration
- private player/user data
- collaborator contact details
- unreleased roadmap/tasks/comments
- private test accounts
- private training/reference assets
- other tenant projects
- crash/log payloads carrying secrets or personal data

The inclusion manifest is allowlist-based. It carries only approved presentation surface IDs or safe runtime/device labels.

## Rights and project privacy

Public audience attachment requires `GameDNA.rights_confirmed=true`.

Private/unlisted development sources may exist with `rights_readiness="unverified"` so creators can work privately without falsely asserting clearance.

Attaching a Game Forge source never changes `GameDNA.status`, `public_id`, public test publication, or project storage visibility. Live audience visibility and project visibility remain separate states.

## Version pinning

On source attachment, Chat 8 records the current `GameDNA.version` and, for gameplay/build sources, the current `GameBuild.build_id`.

Re-attaching the same source identity in the same LIVE session is idempotent and returns the already pinned source. Later working-project edits do not silently move viewers to a new version.

Explicit promotion route:

`POST /api/game-forge/games/{game_id}/live/sources/{source_adapter_id}/promote-version`

Request model:

`PromoteLiveVersionRequest(expected_project_version, expected_build_id?)`

A stale project version or build ID is rejected with `stale_project_version`.

## Presentation transitions

Presentation modes:

- `development`
- `tutorial`
- `build_review`
- `playtest`
- `multiplayer_playtest`
- `gameplay`
- `launch_showcase`
- `brb`

Route:

`PATCH /api/game-forge/games/{game_id}/live/sources/{source_adapter_id}/presentation`

The route mutates source presentation state while preserving the same canonical `live_session_id` and `source_adapter_id`; it does not create another LIVE session.

## Emergency hide and revocation

Route:

`POST /api/game-forge/games/{game_id}/live/sources/{source_adapter_id}/emergency-hide`

A normal emergency hide switches the source to BRB/hidden without deleting the project, terminating autosave, or deleting a playtest build.

`revoke=true` additionally revokes the source handle.

Chat 1/auth integration hook:

`aura_music_studio.game_forge_live_integration.revoke_project_live_sources(game_id, reason=...)`

The caller must already have authoritative permission-revocation context. The hook does not invent an auth decision.

## Shared Sky Chat 2/3 handoff

Function:

`shared_sky_compatibility_payload(source: GameForgeSafeLiveSource) -> dict`

The returned shape is compatible with the existing Shared Sky source vocabulary:

- `source_type="game_forge"`
- safe display `name`
- `visible`
- `locked`
- bounded `config`

The bounded config contains only source adapter/project/session/participant refs, media/aspect data, version/build pins, privacy classifications, allowlist/exclusion metadata, rights readiness, presentation state and correlation data.

Chat 2 owns transport/ingest/relay/recording delivery.

Chat 3 owns scene placement, crop/transform, Preview/Programme, transitions, overlays and generic source composition.

Game Forge does not persist destination OAuth tokens or destination stream keys.

## Chat 4/5/6 read-only boundaries

Viewer chat, Q&A, polls, reactions and presence are Chat 4 truth.

Cosmic Creation Coin wallet/Gift financial truth is Chat 5 truth.

Co-host/Battle participant state, score and timer truth are Chat 6 truth.

Game Forge may display those events beside the editor, but inbound community/Gift/Battle events do not mutate Game DNA automatically.

A viewer suggestion only becomes durable Game Forge feedback through an explicit creator promotion or a session explicitly configured as a structured playtest.

## Feedback contract

Route:

`POST /api/game-forge/games/{game_id}/live/feedback`

Model:

`GameForgeLiveFeedback`

Carries:

- feedback ID
- project/build/version
- live session/source adapter
- optional opaque author reference
- live timestamp
- category
- text
- optional clip reference
- moderation/triage state
- creation/correlation timestamps

Casual chat is rejected unless `creator_promoted=true` or `structured_playtest=true`.

## Returned recording/clip contract

Route:

`POST /api/game-forge/games/{game_id}/live/returns`

Model:

`GameForgeLiveReturnRecord`

Carries:

- project/build/version
- live session/source adapter
- opaque recording/replay/clip/highlight reference
- time range
- asset type
- `provenance="shared_sky"`
- processing state
- idempotency key
- correlation/creation data

Repeated callbacks with the same idempotency material resolve to the same return record rather than creating duplicate project assets.

## Error codes introduced at this boundary

Structured error payloads use `detail.code`, `detail.message` and `detail.correlation_id`.

Relevant codes:

- `unauthenticated`
- `project_unauthorised`
- `live_source_not_ready`
- `live_source_privacy_blocked`
- `stale_project_version`
- `rights_not_verified`
- `internal`

Sensitive stack traces, file paths, tokens and source snippets are not returned by these errors.

## Tests and deterministic fixtures

Primary tests:

`tests/test_game_forge_live_integration.py`

Coverage includes:

- clean gameplay source allowlisting
- build/version pinning
- no whole-window/code/credential flags
- explicit opaque editor/code presentation surfaces
- public rights gate
- idempotent source attach/reconnect
- explicit version promotion
- development-to-playtest/showcase transition on one LIVE session
- emergency hide/BRB
- explicit feedback promotion
- idempotent clip/record return linkage
- project permission revocation
- production-app route composition

No external streaming provider credentials are needed for these tests.

## Inter-chat import and route handoff

Chat 1:

- import `revoke_project_live_sources`
- use `project_id`, `workspace_id`, creator identity, correlation and authoritative auth context

Chat 2:

- consume `shared_sky_compatibility_payload`
- return recording/replay/clip IDs through `/live/returns`
- never send destination credentials into Game Forge

Chat 3:

- register the `game_forge` compatibility source
- retain canonical scene/source registration ID in Shared Sky
- treat Game Forge presentation changes as source intent, not a new broadcast session

Chat 4:

- keep viewer/community truth external to Game Forge
- only promoted/structured feedback enters `/live/feedback`

Chat 5:

- expose Gift activity read-only beside Game Forge
- no wallet/ledger mutation through this module

Chat 6:

- pass authoritative `participant_ref`/session association when registering Game Forge sources
- reconnect must preserve participant/source association
- no Battle scores/timers in Game Forge

Chat 7:

- converge on `studio_type`, source adapter ID/schema version, project/workspace refs, source type, safe label, media/aspect, version refs, privacy inclusion/exclusion, rights, registration/health/revocation and correlation fields

Chat 9:

- consume project/showcase/build readiness and returned promotional candidates
- do not bypass Game Forge rights/build readiness for publishing

Chat 10:

- harden sandbox/container isolation, provider/build queues, resource limits, telemetry and secret scanning
- Game Forge arbitrary code continues to use `AuraSandboxClient`; no host execution is added here

Chat 11:

- release gate the migration-free JSON state schema, new routes, tests and duplicate-source behavior
- replace compatibility imports only after canonical Chat 1–7 contracts land and their tests prove equivalence

## Migration note

No database migration is introduced by this unit. The live adapter is additive and stores schema-versioned state within each existing tenant-scoped Game Forge project directory.

If a future canonical Shared Sky source registry replaces the compatibility payload, migrate by source adapter ID and preserve `live_session_id`, participant association, project/build pins, privacy state and idempotency keys.
