# Chat 3 Shared Sky Studio Integration Contract

Status: Chat 3 production-control-room implementation on top of the current `development/full-site-build` Shared Sky control plane. This document describes exact interfaces now present in the feature branch. It does not transfer ownership of transport, viewer community, Gifts, Battles, editor capture, or release infrastructure to Chat 3.

## Runtime modules and routes

- `aura_music_studio.shared_sky_control_room`
  - `StudioRepository`: Chat 3 persistence extension using the same SQLite database path as `SharedSkyStore`.
  - `StudioService`: Preview/Programme, scene-copy/reorder, transform/audio autosave and safe snapshot orchestration.
  - `SharedSkyTransportCompatibilityAdapter`: narrow Chat 2 boundary; live programme mutation fails closed until Chat 2 supplies an authoritative commit method.
  - `install_shared_sky_control_room(app)`: idempotent production route mounting helper.
- `aura_music_studio.shared_sky_control_room_extensions`
  - original scene-template library and atomic instantiation.
  - Shared Sky audio presets limited to processing fields that have a real implementation path.
  - non-destructive media cue/playback settings.
  - Chat 2 scheduling facade, recording start/stop compatibility adapter and combined studio preflight.
  - Chat 6 read-only participant/green-room compatibility adapter.
  - advisory-only Aura production diagnostics derived from current studio/transport state.
  - `install_shared_sky_control_room_extensions(app)`: idempotent production route mounting helper.
- Existing Chat 2 source of truth: `aura_music_studio.shared_sky_streaming_studios.shared_sky`.
- Operator page: `GET /shared-sky/studio?project_id=<id>&profile_key=landscape-1080`.

Session API:
- `POST /shared-sky/studio/api/sessions`
- `GET /shared-sky/studio/api/sessions/{session_id}`
- `GET /shared-sky/studio/api/sessions/{session_id}/versions`
- `POST /shared-sky/studio/api/sessions/{session_id}/preview`
- `POST /shared-sky/studio/api/sessions/{session_id}/cut`
- `POST /shared-sky/studio/api/sessions/{session_id}/transition`
- `POST /shared-sky/studio/api/sessions/{session_id}/transition/complete`
- `GET /shared-sky/studio/api/sessions/{session_id}/preflight`
- `GET /shared-sky/studio/api/sessions/{session_id}/aura/diagnostics`

Scene/source production operations:
- `POST /shared-sky/studio/api/scenes/{scene_id}/duplicate`
- `POST /shared-sky/studio/api/projects/{project_id}/scenes/reorder`
- `PATCH /shared-sky/studio/api/sessions/{session_id}/sources/{source_id}/transform`
- `PATCH /shared-sky/studio/api/sessions/{session_id}/sources/{source_id}/audio`
- `PATCH /shared-sky/studio/api/sessions/{session_id}/sources/{source_id}/cue`
- `GET /shared-sky/studio/api/templates`
- `POST /shared-sky/studio/api/projects/{project_id}/templates/instantiate`
- `GET /shared-sky/studio/api/audio/presets`
- `POST /shared-sky/studio/api/sessions/{session_id}/sources/{source_id}/audio/preset`

Brand Kit:
- `POST /shared-sky/studio/api/projects/{project_id}/brand-kits`
- `PUT /shared-sky/studio/api/projects/{project_id}/brand-kits/{kit_id}`
- `GET /shared-sky/studio/api/projects/{project_id}/brand-kits`

Scheduling/recording/participant capability surfaces:
- `POST /shared-sky/studio/api/sessions/{session_id}/schedules`
- `DELETE /shared-sky/studio/api/sessions/{session_id}/schedules/{schedule_id}`
- `GET /shared-sky/studio/api/sessions/{session_id}/recording`
- `POST /shared-sky/studio/api/sessions/{session_id}/recording/start`
- `POST /shared-sky/studio/api/sessions/{session_id}/recording/stop`
- `GET /shared-sky/studio/api/sessions/{session_id}/participants`
- `GET /shared-sky/studio/api/layout/{layout_key}/{count}?profile_key=<profile>`

All routes retain `require_esp_hub_member` as the request-level membership gate. Owner transport operations remain owned by the existing Owner Shared Sky module.

## Persistence and migrations

`StudioRepository` creates additive, idempotent tables in the canonical Shared Sky database:

- `shared_sky_studio_sessions`
  - stable `id`, `user_id`, `project_id`, optional `broadcast_id`
  - `profile_key`
  - distinct `preview_scene_id` and `programme_scene_id`
  - immutable-at-commit `programme_snapshot_json`
  - transition lock/token state
  - autosave metadata
  - last authoritative transport result
  - optimistic `version`
- `shared_sky_studio_versions`
  - bounded (latest 50) version snapshots for recovery/conflict diagnostics
- `shared_sky_brand_kits`
  - project/user ownership
  - asset references only; no raw destination credentials or provider secrets
  - optimistic version

No `localStorage` value is authoritative. Browser state is a rendering/cache concern only. `programme_snapshot_json` is deliberately a deep scene/source snapshot so later Preview edits cannot silently alter Programme.

## Production profiles

Canonical Chat 3 profile keys currently implemented:

- `landscape-1080` — 1920×1080 / 16:9
- `portrait-1080` — 1080×1920 / 9:16
- `square-1080` — 1080×1080 / 1:1

Chat 2 remains authoritative for whether a destination supports a profile. Chat 3 must not infer provider capability from this registry alone.

## Scene/source snapshot contract

Programme snapshot schema version 1 contains `schema_version`, `captured_at`, a production `profile`, a stable scene identity/configuration, and the ordered source graph. Each source keeps the canonical Chat 2 source identity. Chat 3 normalizes production-only configuration before Programme commit:

- `config.transform`: normalized x/y/width/height/rotation/opacity/crop values
- `config.effects`: brightness/contrast/saturation/hue/blur/rounded bounds
- `config.audio`: mute/gain/pan/delay/monitor/high-pass/compressor/limiter for relevant audio-bearing types
- `config.playback`: cue/trim/loop/volume/scene-enter/scene-exit settings for media sources
- `config.privacy`: must resolve to a Programme-safe state for visible sources
- browser sources: HTTP(S) only, no embedded credentials, no loopback/private/local host

Any recursively detected provider secret key (`stream_key`, OAuth/access/refresh tokens, credentials, passwords, private keys, client secrets, etc.) rejects persistence or Programme snapshot creation.

## Scene template contract

`SCENE_TEMPLATES` contains original Shared Sky starters: Camera Full Screen, Camera + Chat, Creator + Canvas, Canvas Only, Interview 2-Up, Panel / Grid, Screen Share + Presenter, Tutorial, Music Performance, Gameplay, Premiere, BRB, Starting Soon, Ending and Custom.

Capture/guest/media template slots are created hidden until the operator attaches the real device/media/participant. Atomic instantiation deletes the newly-created scene if any source slot fails, preventing half-created templates.

## Audio preset contract

`AUDIO_PRESETS` exposes Speech, Podcast, Music, Gaming, Interview, Quiet Room and Noisy Room. Presets only set currently backed fields: gain, pan, sync delay, monitor state, high-pass cutoff, compressor and limiter. No unimplemented de-esser/EQ/gate control is represented as functional by these presets.

## Preview / Programme state machine

1. Editing/selecting Preview increments the studio optimistic version and does not alter Programme.
2. CUT captures the current Preview graph into an immutable snapshot, requests an authoritative transport commit, then writes Programme only if accepted.
3. TRANSITION first enters `in_progress` with a unique transition token and target snapshot. A second transition is rejected with 409.
4. Completion requires the same token and version. Transport rejection aborts the transition and leaves the previous Programme snapshot unchanged.
5. Reduced-motion mode drives transition duration to zero while preserving explicit state/commit semantics.
6. A stale tab/operator using an old version receives 409; it cannot overwrite newer studio state.
7. Media cue edits update Preview/source configuration and explicitly leave the committed Programme snapshot unchanged until the next explicit take.

## Chat 2 handoff

Current adapter import: `SharedSkyTransportCompatibilityAdapter`.

Chat 2 may add this method to its canonical store/service without changing Chat 3 call sites:

```python
def set_programme_snapshot(user_id: str, broadcast_id: str, snapshot: dict, *, correlation_id: str) -> dict:
    # Required result keys: accepted: bool, state: str, optional reason: str
    ...
```

When a broadcast is `live` or `starting` and that method is absent, Chat 3 returns a real 503 and does not claim Programme switched. Draft/offline studio work remains available.

Recording compatibility methods expected from Chat 2:

```python
def recording_status(user_id: str, broadcast_id: str) -> dict: ...
def start_recording(user_id: str, broadcast_id: str) -> dict: ...
def stop_recording(user_id: str, broadcast_id: str) -> dict: ...
```

Until present, Chat 3 reports recording unsupported and start/stop returns 503. It never displays or returns a fabricated Recording state.

Scheduling execution remains the existing Chat 2 `shared_sky_schedules` path. Chat 3's schedule endpoint calls `SharedSkyStore.create_schedule`; cancellation calls `SharedSkyStore.delete_schedule`. It does not create a second scheduler. Scheduled times are required to include an explicit timezone.

## Chat 4 handoff — viewer/community overlays

Chat 3 owns presentation only. Safe binding kind values are `chat`, `poll`, `qa`, `captions`, and `custom_text`. Chat 4 supplies authoritative event/state feeds. Binding payloads must be secret-free IDs/configuration; no community ledger is persisted here.

## Chat 5 handoff — Gifts/supporter presentation

Safe display-only binding kinds are `gift_goal` and `supporter`. Chat 3 does not debit Cosmic Creation Coins, derive balances, calculate creator liabilities, or persist financial truth. Chat 5 event/goal IDs are rendered as external authoritative state.

## Chat 6 handoff — participants/Battles

Chat 3 exposes deterministic normalized tile geometry via `participant_layout(layout_key, count, profile_key)` for one through eight supplied participants. Layout keys currently include Solo, Side-by-Side/Interview, Grid, Speaker Focus hook, Host + Guests, Picture-in-Picture hook, Vertical Stack and Battle Teams hook.

The read-only `ParticipantCompatibilityAdapter` looks for this future provider method:

```python
def studio_participants(user_id: str, broadcast_id: str) -> list[dict]: ...
```

Each row is validated into `ParticipantState` with explicit `participant_id`, `display_name`, `stage`, `connection_state`, camera/microphone state, connection quality and role. A participant may be `connected` while still `green_room`; connection never implies Programme. Until the method lands the API reports `supported=false` rather than producing fake guests.

Participant identity/order/stage/Battle score/lifecycle remain Chat 6/shared participant authority.

## Chat 7 handoff — Music/Video/Image sources

Provide a canonical Shared Sky source object owned by the current user/project with a stable `id`, `source_type`, `name`, `config`, visibility/lock/z-order and safe provenance/privacy metadata. Private editor panels/tokens must never be represented as a browser source. Chat 3 only snapshots the safe programme source.

## Chat 8 handoff — Game Forge sources

Same source contract as Chat 7. Gameplay/editor/playtest capture adapters own media acquisition. Chat 3 owns scene placement/composition only.

## Chat 9 handoff — Creator surfaces

Chat 9 may read studio project/session history using creator-authorised workflows. Programme mutations must still flow through Chat 3 endpoints with the current optimistic version and normal server permission checks.

## Chat 10 handoff — observability/security/performance

Chat 10 should instrument `studio_cut`, `studio_transition`, `studio_schedule_created`, `studio_schedule_cancelled`, `studio_recording_start` and `studio_recording_stop` Shared Sky events and correlation IDs where provided; transition/transport rejection reason codes; browser Web Audio/device failures; 409 stale-version frequency; canvas/effect pressure metrics when measurable; and source cleanup/leaked-media-track diagnostics. No numeric performance value should be synthesized when no browser metric exists.

## Chat 11 release handoff

Release acceptance should verify production route reachability, additive database migration, truthful Chat 2 live-commit/recording capability, CI/security/self-host smoke status, no provider credentials in Studio/Brand Kit snapshots, that Preview editing cannot mutate the last Programme snapshot, template rollback, timezone-aware scheduling and green-room-vs-Programme participant separation.

## Aura production assistance

`GET /shared-sky/studio/api/sessions/{session_id}/aura/diagnostics` is advisory only. It derives recommendations from the current Preview source graph, committed Programme state and transport capability. It may identify an empty scene, no committed Programme scene, missing live-commit capability, absence of an audio-bearing source or unattached template slots. It performs no authoritative action and returns `authoritative_actions_performed=false`.

## Browser audio/canvas behaviour actually implemented

The current operator page uses a DOM compositor backed by persisted normalized transforms. Programme renders from the saved Programme snapshot, while Preview renders the current scene graph. CSS-backed effects represented in the renderer have real paths for brightness, contrast, saturation, hue, blur and rounded masking; unbacked effects are not advertised by this control-room surface.

The audio utility computes RMS/peak/dBFS/clipping from real supplied samples. Browser runtime meter attachment uses `AudioContext`, `MediaStreamAudioSourceNode`, a high-pass `BiquadFilterNode`, `DynamicsCompressorNode`, `GainNode`, and `AnalyserNode`. Meters start explicitly unavailable and only animate after a real `MediaStream` is attached. Local monitoring is off by default.

Hotkeys are ignored while focus is in input/textarea/select/contenteditable controls. Current bindings are Alt+C for CUT and Ctrl+Enter for TRANSITION. Alt+B does not silently take an emergency scene; it reports that a configured BRB scene is required.

## Known compatibility gaps after current Chat 3 units

- Chat 2 authoritative live Programme registration method has not landed in the current integration branch.
- Chat 2 authoritative recording start/stop/status contract has not landed in the current integration branch.
- Chat 6 green-room/participant media authority has not landed in the current integration branch; the read-only fail-closed adapter is ready.
- Full drag/resize/rotate pointer compositor, multi-select/snapping, source grouping/alignment and touch gestures are not yet implemented; normalized transform persistence and numeric accessible controls are.
- Full integrated source/device picker and capture attachment workflow remains to be built into the main control-room page.
- Media cue configuration is durable and Preview-safe, but a provider/backend playout adapter is still required for authoritative remote pre-recorded execution.
- Guest invite creation/moderation controls await the shared participant authority; Chat 3 must not duplicate it.
- Advanced audio processing not already represented by a real Web Audio/config path remains intentionally absent.
- Full graphics authoring for QR/tickers/data-driven overlays and animation authoring remains follow-up work; typed external widget bindings are already isolated from authoritative community/financial state.
- The Aura diagnostics endpoint is advisory; no autonomous consequential action path is enabled.
