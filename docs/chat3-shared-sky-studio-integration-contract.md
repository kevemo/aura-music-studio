# Chat 3 Shared Sky Studio Integration Contract

Status: Chat 3 production-control-room implementation on top of the current `development/full-site-build` Shared Sky control plane. This document describes exact interfaces now present in the feature branch. It does not transfer ownership of transport, viewer community, Gifts, Battles, editor capture, or release infrastructure to Chat 3.

## Runtime modules and routes

- `aura_music_studio.shared_sky_control_room`
  - `StudioRepository`: durable Studio session, recovery-version and Brand Kit persistence using the same SQLite database path as `SharedSkyStore`.
  - `StudioService`: Preview/Programme state machine, safe Programme snapshots, source/audio normalization and Chat 2 transport compatibility.
  - `SharedSkyTransportCompatibilityAdapter`: narrow Chat 2 boundary; live Programme mutation fails closed until Chat 2 supplies an authoritative commit method.
- `aura_music_studio.shared_sky_control_room_extensions`
  - original scene-template library and atomic instantiation.
  - Shared Sky audio presets limited to processing fields with a real implementation path.
  - non-destructive media cue/playback settings.
  - Chat 2 scheduling facade, recording start/stop compatibility adapter and combined studio preflight.
  - Chat 6 read-only participant/green-room compatibility adapter.
  - advisory-only Aura production diagnostics derived from current studio/transport state.
- `aura_music_studio.shared_sky_professional_canvas`
  - desktop-first professional Preview/Programme UI.
  - browser camera/microphone/screen attachment, explicit reconnect and track cleanup.
  - pointer move/resize/rotate, safe guides, zoom, multi-select, align/distribute, numeric transforms and real renderer-backed effect controls.
  - focus-safe operator hotkeys and real-signal browser audio meters.
- `aura_music_studio.shared_sky_studio_history_graphics`
  - atomic Preview graph transactions and bounded server-side Undo/Redo.
  - stable scene/source ID restoration.
  - scene lock, notes and folder/group metadata.
  - tracked scene/source create/delete/duplicate/reorder/transform operations.
  - typed original Shared Sky lower-third/title/subtitle/social/banner/sponsor/custom-text graphics with sanitized style schemas.
- Existing Chat 2 source of truth: `aura_music_studio.shared_sky_streaming_studios.shared_sky`.

Operator pages:
- `GET /shared-sky/studio?project_id=<id>&profile_key=landscape-1080`
- `GET /shared-sky/studio/professional?project_id=<id>&profile_key=landscape-1080`

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

Atomic history/scene/source API:
- `GET /shared-sky/studio/api/sessions/{session_id}/history`
- `POST /shared-sky/studio/api/sessions/{session_id}/undo`
- `POST /shared-sky/studio/api/sessions/{session_id}/redo`
- `PATCH /shared-sky/studio/api/sessions/{session_id}/sources/batch-transform`
- `POST /shared-sky/studio/api/sessions/{session_id}/sources/tracked`
- `POST /shared-sky/studio/api/sessions/{session_id}/sources/batch-delete`
- `POST /shared-sky/studio/api/sessions/{session_id}/scenes/tracked`
- `POST /shared-sky/studio/api/sessions/{session_id}/scenes/{scene_id}/duplicate-tracked`
- `PATCH /shared-sky/studio/api/sessions/{session_id}/scenes/{scene_id}/tracked`
- `DELETE /shared-sky/studio/api/sessions/{session_id}/scenes/{scene_id}/tracked`
- `POST /shared-sky/studio/api/sessions/{session_id}/scenes/reorder-tracked`
- `POST /shared-sky/studio/api/sessions/{session_id}/graphics`

Compatibility scene/source production operations retained for neighbouring callers:
- `POST /shared-sky/studio/api/scenes/{scene_id}/duplicate`
- `POST /shared-sky/studio/api/projects/{project_id}/scenes/reorder`
- `PATCH /shared-sky/studio/api/sessions/{session_id}/sources/{source_id}/transform`
- `PATCH /shared-sky/studio/api/sessions/{session_id}/sources/{source_id}/audio`
- `PATCH /shared-sky/studio/api/sessions/{session_id}/sources/{source_id}/cue`
- `PATCH /shared-sky/studio/api/sessions/{session_id}/profile`
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

`StudioRepository` and `HistoryRepository` create additive, idempotent tables in the canonical Shared Sky database:

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
  - bounded latest recovery snapshots for session conflict/recovery diagnostics.
- `shared_sky_brand_kits`
  - project/user ownership, asset references only and optimistic version.
- `shared_sky_scene_metadata`
  - scene lock, operator notes and folder/group metadata without changing Chat 2's canonical scene identity.
- `shared_sky_studio_history`
  - bounded latest 100 atomic Preview-graph actions.
  - before/after snapshots, action key and undone state.
  - new edits after Undo discard the redo branch.

No `localStorage` value is authoritative. Browser state is rendering/cache state only. `programme_snapshot_json` is deliberately a deep scene/source snapshot so later Preview edits, Undo/Redo, scene deletion or source restoration cannot silently rewrite the last committed Programme pixels.

## Undo/Redo and atomic editor contract

The professional canvas sends one `batch-transform` request for a multi-source drag, resize, rotate, align, distribute or keyboard-nudge gesture. The transaction validates the optimistic Studio version and every selected source before committing any source update.

Undo/Redo restores the canonical Preview scene/source graph with the same stable IDs rather than creating replacement identities. A restored camera/microphone/screen source returns as a detached browser-capture source and must explicitly reacquire browser permission; a `MediaStream` is never serialized into history.

Undo/Redo does not restore or mutate `programme_snapshot_json`. Programme changes still require CUT or TRANSITION through the authoritative transport boundary. Undo/Redo and editor mutation are rejected while a transition is in progress.

Scene deletion keeps at least one scene, rejects locked scenes, and requires explicit confirmation when deleting the editable scene identity corresponding to the current Programme snapshot. The Programme snapshot remains preserved after that confirmed deletion.

## Production profiles

Canonical Chat 3 profile keys currently implemented:
- `landscape-1080` — 1920×1080 / 16:9
- `portrait-1080` — 1080×1920 / 9:16
- `square-1080` — 1080×1080 / 1:1

Chat 2 remains authoritative for whether a destination supports a profile. Chat 3 never infers provider capability from this registry alone.

## Scene/source snapshot contract

Programme snapshot schema version 1 contains `schema_version`, `captured_at`, a production `profile`, stable scene identity/configuration and the ordered source graph. Each source keeps the canonical Shared Sky source identity. Chat 3 normalizes production-only configuration before Programme commit:

- `config.transform`: x/y/width/height/rotation/opacity/crop.
- `config.effects`: brightness/contrast/saturation/hue/blur/rounded bounds.
- `config.audio`: mute/gain/pan/delay/monitor/high-pass/compressor/limiter for relevant audio-bearing types.
- `config.playback`: cue/trim/loop/volume/scene-enter/scene-exit settings for media sources.
- `config.privacy`: must resolve to a Programme-safe state for visible sources.
- browser sources: HTTP(S) only, no embedded credentials, loopback/private/local host.

Any recursively detected provider secret key (`stream_key`, OAuth/access/refresh tokens, credentials, passwords, private keys, client secrets, etc.) rejects Studio, history, Brand Kit or Programme snapshot persistence.

## Original graphics contract

The atomic graphics endpoint creates canonical Shared Sky `text` sources rather than a separate graphics database. Current real renderer-backed graphic kinds are:
- Lower Third
- Title
- Subtitle
- Social Handle
- Banner
- Sponsor Card
- Custom Text

The style schema bounds font size, weight, alignment, text/background hex colours, background opacity, padding and corner radius. Arbitrary CSS is not accepted. The professional DOM compositor renders these typed fields and escapes user text. These are original Shared Sky structures; no competitor artwork or proprietary scene package is included.

QR generation, animated tickers and externally data-driven graphics remain separate follow-up capabilities until a real safe renderer/data-binding path is implemented.

## Scene template contract

`SCENE_TEMPLATES` contains original Shared Sky starters: Camera Full Screen, Camera + Chat, Creator + Canvas, Canvas Only, Interview 2-Up, Panel / Grid, Screen Share + Presenter, Tutorial, Music Performance, Gameplay, Premiere, BRB, Starting Soon, Ending and Custom.

Capture/guest/media template slots are created hidden until the operator attaches the real device/media/participant. Atomic instantiation deletes the newly-created scene if any source slot fails, preventing half-created templates.

## Audio preset contract

`AUDIO_PRESETS` exposes Speech, Podcast, Music, Gaming, Interview, Quiet Room and Noisy Room. Presets only set currently backed fields: gain, pan, sync delay, monitor state, high-pass cutoff, compressor and limiter. No unimplemented de-esser/EQ/gate control is represented as functional by these presets.

## Preview / Programme state machine

1. Editing/selecting Preview increments the Studio optimistic version and does not alter Programme.
2. CUT captures the current Preview graph into an immutable snapshot, requests an authoritative transport commit, then writes Programme only if accepted.
3. TRANSITION enters `in_progress` with a unique token and target snapshot; a second transition is rejected with 409.
4. Completion requires the same token and version. Transport rejection aborts the transition and leaves previous Programme unchanged.
5. Reduced-motion mode drives motion duration to zero while preserving explicit state/commit semantics.
6. Stale tabs/operators receive 409 and cannot overwrite newer Studio state.
7. Media cue edits and history-managed Preview edits explicitly leave committed Programme unchanged until the next explicit take.

## Professional browser compositor actually implemented

The professional page is desktop-first and uses a DOM compositor backed by persisted normalized transforms. Programme renders from the saved Programme snapshot; Preview renders the editable graph.

Implemented operator controls include pointer move/resize/rotate, Shift rotation snapping, multi-select, alignment/distribution, keyboard nudging, numeric transform fields, safe/title-safe/centre guides, zoom and 16:9/9:16/1:1 profile reflow. Rotation/drag selection chrome never exists in the Programme snapshot.

Browser capture uses `getUserMedia` and `getDisplayMedia`. Tracks are stopped when a source is removed, a device/capture track ends, history removes the source, or the page exits. Recovered capture sources require explicit reconnect.

CSS-backed effects actually rendered by this control room are brightness, contrast, saturation, hue rotation, blur, rounded masking, opacity and transforms. Unbacked effects are not advertised by the professional surface.

Hotkeys are ignored while focus is in input/textarea/select/contenteditable controls. Current professional bindings include Alt+C CUT, Ctrl+Enter TRANSITION, Ctrl/Cmd+Z Undo, Ctrl/Cmd+Shift+Z or Ctrl/Cmd+Y Redo, and arrow-key nudging.

## Browser audio behaviour actually implemented

The deterministic audio utility computes RMS/peak/dBFS/clipping from real supplied samples. Browser runtime meters use a real `AudioContext`/`AnalyserNode` path only after a real `MediaStream` is attached; otherwise the UI reports signal unavailable.

The broader Studio audio configuration supports gain, pan, sync delay, monitor state, high-pass, compressor and limiter. Local monitoring remains off by default. More advanced processing is not presented as implemented unless a real processing path exists.

## Chat 2 handoff

Chat 2 may add this canonical method without changing Chat 3 call sites:

```python
def set_programme_snapshot(user_id: str, broadcast_id: str, snapshot: dict, *, correlation_id: str) -> dict:
    # Required: accepted: bool, state: str; optional reason: str
    ...
```

When a broadcast is `live` or `starting` and this method is absent, Chat 3 returns 503 and does not claim Programme switched. Offline/draft production remains usable.

Recording methods expected from Chat 2:

```python
def recording_status(user_id: str, broadcast_id: str) -> dict: ...
def start_recording(user_id: str, broadcast_id: str) -> dict: ...
def stop_recording(user_id: str, broadcast_id: str) -> dict: ...
```

Until present, recording reports unsupported and start/stop returns a real unavailable/503 path. Scheduling continues through Chat 2's existing `shared_sky_schedules` execution path; Chat 3 does not create a second scheduler.

## Chat 4 handoff — viewer/community overlays

Chat 3 owns presentation only. Safe binding kinds include `chat`, `poll`, `qa`, `captions` and `custom_text`. Chat 4 supplies authoritative event/state feeds. No community ledger is persisted here.

## Chat 5 handoff — Gifts/supporter presentation

Display-only bindings include `gift_goal` and `supporter`. Chat 3 does not debit Cosmic Creation Coins, derive balances, calculate creator liabilities or persist financial truth.

## Chat 6 handoff — participants/Battles

Chat 3 exposes deterministic one-to-eight tile geometry and a read-only `ParticipantCompatibilityAdapter`. The future provider method is:

```python
def studio_participants(user_id: str, broadcast_id: str) -> list[dict]: ...
```

Rows carry explicit participant identity, display name, stage, connection state, camera/microphone state, connection quality and role. `connected` never implies `on_programme`. Battle scoring/lifecycle remains Chat 6 authority.

## Chat 7 handoff — Music/Video/Image sources

Provide canonical Shared Sky sources owned by the current user/project with stable IDs, source type, config, visibility/lock/z-order and safe provenance/privacy metadata. Private editor panels/tokens must never be represented as programme browser sources.

## Chat 8 handoff — Game Forge sources

Same source contract as Chat 7. Gameplay/editor/playtest acquisition remains Chat 8; Chat 3 owns placement/composition only.

## Chat 9 handoff — Creator surfaces

Chat 9 may read authorised Studio project/session/history status. Programme mutations still flow through Chat 3's versioned server endpoints.

## Chat 10 handoff — observability/security/performance

Instrument `studio_cut`, `studio_transition`, `studio_undo`, `studio_redo`, scheduling and recording events; correlation/rejection reason codes; stale-version 409 frequency; browser capture/Web Audio failures; and measurable source/effect/resource-pressure/cleanup telemetry. Never synthesize numeric performance data.

## Chat 11 release handoff

Verify production route reachability, additive DB migration, stable-ID Undo/Redo, Programme isolation through history operations, source secret rejection, typed graphic sanitization, browser capture cleanup, truthful Chat 2 live/recording capability, template rollback, timezone scheduling, participant staging separation and the latest CI/Security/self-host evidence.

## Aura production assistance

`GET /shared-sky/studio/api/sessions/{session_id}/aura/diagnostics` is advisory only. It derives recommendations from current Preview, Programme and transport state. It performs no authoritative action and returns `authoritative_actions_performed=false`.

## Known compatibility gaps after current Chat 3 units

- Chat 2 authoritative live Programme registration/commit has not landed on the audited integration baseline.
- Chat 2 authoritative recording start/stop/status has not landed on the audited integration baseline.
- Chat 6 real green-room/participant media authority has not landed; the fail-closed read-only adapter is ready.
- Media cue configuration is durable and Preview-safe, but authoritative remote pre-recorded playout remains a Chat 2 transport/worker dependency.
- Guest invite creation, participant mute/remove and moderation-facing production actions await the shared participant authority and must not be duplicated here.
- Full Brand Kit integration with canonical media-library type/size/licence validation remains deeper follow-up work; current kits use bounded asset references and project ownership.
- Advanced bus routing, PFL/solo, noise gate, parametric EQ, de-esser and similar processing remain absent unless/until a tested Web Audio/backend path is implemented.
- QR generation, animated ticker/data feeds and richer graphics animation authoring remain follow-up work.
- Configurable hotkey profiles and deterministic multi-action macros remain follow-up work; current hotkeys are fixed and focus-safe.
- Chat 10 device/resource pressure telemetry and browser-matrix E2E/accessibility scanning remain release-hardening dependencies.
- Aura remains advisory; no autonomous consequential programme, participant, destination or recording action path is enabled.
