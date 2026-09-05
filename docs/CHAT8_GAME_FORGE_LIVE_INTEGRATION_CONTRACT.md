# Chat 8 Game Forge & Game Go Live Integration Contract

Status: Chat 8 compatibility contract for `development/full-site-build`.

Game Forge owns project/game source truth. Shared Sky transport, programme composition, community, Gifts and Battle truth remain in their canonical domains. The contract is additive and reuses the existing tenant/project, runtime, asset, build, playtest and sandbox architecture rather than creating a second engine or project store.

## Canonical Game Forge foundations

Primary existing imports:

- `aura_music_studio.game_forge_models.GameDNA`
- `aura_music_studio.game_forge_models.GameBuild`
- `aura_music_studio.game_forge_store.load_game`
- `aura_music_studio.game_forge_store.game_dir`
- `aura_music_studio.game_forge_project_binding.creative_project_name`
- `aura_music_studio.game_forge_api._creator`
- `aura_music_studio.aura_sandbox.AuraSandboxClient`
- `aura_music_studio.game_forge_model_assets.GameModelAssetRecord`

Game DNA and its subresources are stored below request-scoped member storage. Generated/user code remains delegated to `AuraSandboxClient`; Chat 8 does not execute arbitrary member code on the FastAPI host.

## Router composition

Both Chat 8 routers are mounted through:

`aura_music_studio.game_forge_project_binding.router`

Modules:

- `aura_music_studio.game_forge_live_integration`
- `aura_music_studio.game_forge_model_generation`

Project-context payloads expose:

`go_live_create_url=/game-creation/live/{game_id}`

## Existing project/scene/runtime contracts reused

The current repository already supplies canonical Game DNA, World DNA, entity/transform/physics/behavior state, native Aura2D/Aura3D runtime generation, static GLTF/GLB model import, project asset snapshots, build records, private playtest runtime, deterministic runtime state saves, accessibility controls and project-aware Aura command routes.

Chat 8 does not redefine those schemas. When neighbouring chats need project identity they use the canonical Game DNA `id` plus logical Creative-project binding when present, never an arbitrary filesystem path.

## 3D model import contract

Canonical module:

`aura_music_studio.game_forge_model_assets`

Current truthful formats:

- `.glb`
- embedded `.gltf`

Current import lifecycle includes size/type checks, SHA-256 integrity, closed static-mesh extraction/validation, metadata, rights attestation, tenant-scoped storage, build/rating invalidation, runtime projection and deletion/reference cleanup.

External GLTF resources are not enabled. Unsupported formats are not advertised. Skeletal animation/retargeting is not claimed by this contract.

## AI text/image-to-3D generation job contract

Canonical module:

`aura_music_studio.game_forge_model_generation`

Schema:

`ModelGenerationJob` version `1`.

Request model:

`CreateModelGenerationRequest`.

Capabilities:

- `text_to_3d`
- `image_to_3d`

Durable per-project job location:

`<tenant Game Forge game directory>/generation_jobs/gfgen_*.json`

Callers cannot supply filesystem paths.

A job records:

- `generation_request_id`
- `project_id`
- target asset kind
- capability
- provider identifier
- prompt SHA-256, not prompt text
- project-owned reference asset IDs
- quality profile/poly budget/texture request
- state: `queued | running | succeeded | failed | cancelled`
- provider-reported progress only
- provider result reference
- validation state
- final validated asset-version reference
- entitlement reference
- error/correlation data
- provider/provenance metadata
- created/updated timestamps

Production provider selection is server-side through `AURA_GAME_3D_PROVIDER` plus `AURA_GAME_3D_PROVIDER_<NAME>_ENABLED`. Provider credentials are not represented in the job record or client response.

When no provider is configured, the request is persisted as `failed` with `generation_provider_unavailable` and the HTTP request returns `503`. No sample GLB, fake completion, fake progress or success artifact is produced.

When a provider is enabled, creation records an internal `queued` job. Submission/execution is delegated to the canonical worker/queue boundary rather than performing provider work in the request process.

Internal worker hooks:

- `claim_generation_job(...)`
- `report_generation_progress(...)`
- `fail_generation_job(...)`
- `complete_generation_job(...)`

Only a running job can complete. Completion requires both an opaque provider result reference and an opaque final validated asset-version reference. Provider mismatch is rejected. Progress is absent until a worker/provider reports it; running progress is limited to `0..99`, with `100` only on validated completion.

Public API:

- `GET /api/game-forge/games/{game_id}/model-generation`
- `POST /api/game-forge/games/{game_id}/model-generation`
- `GET /api/game-forge/games/{game_id}/model-generation/{job_id}`
- `DELETE /api/game-forge/games/{game_id}/model-generation/{job_id}`

Image-to-3D requires project-owned opaque reference asset IDs; path-like references are rejected.

## Safe Game Forge LIVE source schema

Canonical model:

`GameForgeSafeLiveSource`

Schema version:

`game_forge_live_source.v1`

Durable state:

`GameForgeLiveState` under `<tenant Game Forge game directory>/live/shared_sky.json`.

State includes:

- project ID
- sources keyed by stable source-adapter ID
- promoted/structured playtest feedback
- idempotent Shared Sky return records
- schema/update metadata

Safe-source fields include:

- source adapter ID/schema version
- `studio_type="game_forge"`
- project/workspace/creator/session/participant references
- source type and safe display label
- media/aspect profile
- pinned project version and build ID
- project/audience/privacy classification
- inclusion manifest and fixed exclusion policy
- rights readiness
- Shared Sky registration reference slot
- health/status/revocation state
- presentation mode and optional approved presentation-surface reference
- timestamps/correlation ID

The descriptor does not contain the Game DNA document, scene document, source code, private repository contents, credentials or destination tokens.

## Safe source types

Supported Game Forge source intents:

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

Default gameplay source: `clean_game_output`.

Gameplay/build sources require a real `latest_build` with `private_playtest_ready=true`. `approved_build_output` additionally requires a current approved assessment matching the build content hash.

Editor/scene/code/node/profiler sources require an explicit opaque `presentation_surface_ref`; paths and URLs are rejected. Whole-window capture is never the implicit source implementation.

## Privacy exclusion contract

`LIVE_PRIVACY_EXCLUSIONS` is fixed code-owned policy and excludes:

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
- crash/log payloads containing secrets or personal data

The inclusion manifest is allowlist-based and explicitly records that private editor panels, source-code payloads, credentials and whole-window capture are not included.

## Rights and privacy

Public audience attachment requires `GameDNA.rights_confirmed=true`.

Private/unlisted development sources may remain `rights_readiness="unverified"`; this permits private work without falsely asserting commercial/public clearance.

Attaching a LIVE source never changes Game DNA publication status, public ID or project privacy. Project visibility and LIVE audience visibility remain distinct states.

## Version pinning and reconnect

On attach, Chat 8 records the current Game DNA version and, for gameplay/build sources, the current build ID.

The source ID is deterministic for the same creator/project/LIVE session/participant/source identity (or supplied idempotency key). Repeated attach returns the existing source rather than duplicating registration.

Later working-project edits do not silently change the pinned viewer version.

Explicit promotion:

`POST /api/game-forge/games/{game_id}/live/sources/{source_adapter_id}/promote-version`

A stale expected project version or build ID returns `stale_project_version`.

## Development/playtest/showcase transition

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

The transition preserves the same canonical `live_session_id` and source-adapter ID. It does not create a second LIVE session.

## Emergency hide and revocation

Route:

`POST /api/game-forge/games/{game_id}/live/sources/{source_adapter_id}/emergency-hide`

Normal emergency hide changes the presentation to BRB/hidden without deleting the project, terminating autosave or deleting the playtest build.

`revoke=true` also revokes the source handle.

Authoritative auth/project-lifecycle compatibility hook:

`revoke_project_live_sources(game_id, reason=...)`

The caller must already possess the canonical permission-revocation decision.

## Shared Sky Chat 2/3 handoff

Function:

`shared_sky_compatibility_payload(source)`

It emits the existing Shared Sky source vocabulary with `source_type="game_forge"`, a safe label/visibility flag and bounded config containing only project/session/participant refs, media/aspect metadata, version/build pins, privacy inclusion/exclusion metadata, rights state, presentation state, health/revocation and correlation fields.

Chat 2 owns transport/ingest/transcode/relay/recording/destination delivery.

Chat 3 owns scene placement, crop/transform, Preview/Programme, transitions, overlays and generic source composition.

Game Forge stores neither destination OAuth tokens nor destination stream keys and never reports destination delivery success.

## Chat 4/5/6 boundaries

Chat 4 owns viewer chat/Q&A/polls/reactions/presence.

Chat 5 owns Cosmic Creation Coin/Gift financial truth.

Chat 6 owns participant/co-host/Battle score/timer truth.

Game Forge may consume those events read-only beside the editor. Inbound community/Gift/Battle events never mutate Game DNA automatically.

A viewer suggestion becomes Game Forge feedback only when a creator explicitly promotes it or the LIVE is configured as a structured playtest.

## Feedback contract

Route:

`POST /api/game-forge/games/{game_id}/live/feedback`

`GameForgeLiveFeedback` records feedback ID, project/build/version, live session/source, optional opaque author ref, LIVE time, category, text, optional clip ref, moderation/triage state and correlation metadata.

Casual viewer chat is rejected unless `creator_promoted=true` or `structured_playtest=true`.

## Returned recording/clip/highlight contract

Route:

`POST /api/game-forge/games/{game_id}/live/returns`

`GameForgeLiveReturnRecord` records project/build/version, LIVE session/source, opaque recording/replay/clip/highlight ref, time range, asset type, `provenance="shared_sky"`, processing state, idempotency key and correlation metadata.

Repeated callbacks resolve to the same return record rather than creating duplicates.

## Safe embedded Go Live & Create portal

Route:

`GET /game-creation/live/{game_id}`

The portal exposes safe Game Forge source attach, Playtest, Launch/Showcase, BRB and Emergency Hide controls plus a link to the existing private playtest runtime. It uses a CSP nonce and states the clean game output privacy boundary. It is a Game Forge source controller, not a replacement Shared Sky compositor.

## Error contracts

Live boundary structured codes include:

- `unauthenticated`
- `project_unauthorised`
- `live_source_not_ready`
- `live_source_privacy_blocked`
- `stale_project_version`
- `rights_not_verified`
- `internal`

3D generation boundary codes include:

- `project_unauthorised`
- `project_read_only`
- `asset_invalid`
- `generation_provider_unavailable`
- `generation_failed`
- `generation_cancelled`

Sensitive stack traces, provider credentials, paths and source snippets are not exposed intentionally by these contracts.

## Tests and fixtures

Chat 8 tests:

- `tests/test_game_forge_live_integration.py`
- `tests/test_game_forge_model_generation.py`

Live tests cover privacy allowlisting, explicit safe presentation surfaces, rights gating, attach/reconnect idempotency, build/version pinning and promotion, same-session transitions, emergency hide, feedback promotion, returned-asset deduplication, permission revocation and production route composition.

3D generation tests cover honest unavailable-provider failure, no fake asset/progress, idempotent generation request, configured-provider queue/claim/progress/complete transitions, wrong-provider rejection, required image reference IDs, path-reference rejection, failed-provider cleanup semantics and production route composition.

No external provider credentials are required for deterministic tests.

## Inter-chat handoff

### Chat 1

Use canonical user/workspace/project IDs, auth/entitlements/correlation contracts. Import `revoke_project_live_sources` only after an authoritative revocation decision.

### Chat 2

Consume `shared_sky_compatibility_payload`. Return recording/replay/clip IDs through `/live/returns`. Never send destination credentials to Game Forge.

### Chat 3

Register the `game_forge` compatibility source and own all compositor placement/mixing/transition semantics. Preserve canonical source identity across presentation transitions.

### Chat 4

Keep viewer/community truth external. Only creator-promoted or structured playtest feedback enters Game Forge.

### Chat 5

Gift data is display/read-only at the Game Forge boundary. No Coin/wallet/ledger mutation exists here.

### Chat 6

Provide authoritative participant/session association for Game Forge sources. Game Forge does not calculate Battle scores/timers.

### Chat 7

Converge on shared creation-source vocabulary: studio type, source adapter/schema IDs, project/workspace refs, safe source type/label, media/aspect metadata, version refs, inclusion/exclusion policy, rights, health/revocation and correlation fields.

### Chat 9

Consume Game Forge project/showcase/build readiness and returned promotional candidates. Do not bypass Game Forge rights/build readiness for publishing.

### Chat 10

Own provider workers/queues, sandbox/container isolation, provider credentials, global resource limits, telemetry and secret scanning. The Game Forge 3D job module exports worker lifecycle hooks and does not implement a second infrastructure stack.

### Chat 11

Release-gate these additive schemas/routes/tests, detect duplicate source/provider implementations and replace compatibility imports only after canonical neighbouring contracts prove equivalent.

## Migration references

No database migration is introduced by this Chat 8 unit.

Added durable schemas are versioned JSON beneath the existing tenant-scoped Game Forge directory:

- `live/shared_sky.json`
- `generation_jobs/gfgen_*.json`

If canonical Shared Sky or provider queue persistence later moves these records into shared database tables, migrate by source-adapter/generation-request ID while preserving LIVE session/participant association, project/build pins, privacy state, provider state, provenance, correlation IDs and idempotency keys.
