# Chat 3 Shared Sky Studio Integration Contract

Status: production-facing Chat 3 control-room implementation targeting `development/full-site-build`.

Chat 3 owns professional Studio composition, Preview/Programme state, scene/source editing, operator controls, production graphics, Studio-local recovery and the composition-side handoff into Shared Sky transport. It does **not** own Chat 2 transport/delivery, Chat 4 viewer/community truth, Chat 5 financial truth, Chat 6 participant/Battle truth, Chat 7/8 editor capture authority, Chat 10 infrastructure hardening, or Chat 11 final release acceptance.

## Current canonical modules

- `aura_music_studio.shared_sky_control_room`
  - durable Studio sessions
  - explicit Preview vs Programme state
  - immutable-at-commit Programme snapshots
  - CUT/transition state machine
  - source/effect/audio normalization
  - optimistic concurrency and bounded recovery versions
  - project-owned Brand Kits
- `aura_music_studio.shared_sky_control_room_extensions`
  - original scene templates
  - audio presets backed only by implemented fields
  - media cue/trim/loop settings
  - scheduler delegation
  - participant compatibility boundary
  - advisory Aura diagnostics
- `aura_music_studio.shared_sky_professional_canvas`
  - desktop-first professional compositor
  - camera/microphone/screen browser capture
  - move/resize/rotate, zoom, safe guides, multi-select, align/distribute, numeric inspector
  - real browser-signal meters
- `aura_music_studio.shared_sky_studio_history_graphics`
  - atomic Preview graph transactions
  - bounded server-side Undo/Redo
  - stable scene/source ID restoration
  - scene lock/notes/folder metadata
  - typed original Shared Sky lower thirds/titles/subtitles/social banners/sponsor/custom text graphics
- `aura_music_studio.shared_sky_studio_recovery_hardening`
  - records history-managed Studio edits in the canonical bounded 50-version recovery ledger inside the same SQLite transaction
- `aura_music_studio.shared_sky_chat2_studio_integration`
  - canonical Chat 3 composition -> Chat 2 `studio_program` source adapter
  - authoritative transport status/preflight
  - Chat 2 recording request/status compatibility
- `aura_music_studio.shared_sky_chat2_studio_operator`
  - attach-broadcast, start, stop, destination retry and marker actions
- `aura_music_studio.shared_sky_professional_transport_toolbar`
  - Professional Studio transport console using only the mounted Chat 2/Chat 3 server contracts
  - authoritative preflight evidence, LIVE state, destination state, recording state and marker controls

## Canonical transport ownership after Chat 2 merge

The previous compatibility assumption that Chat 2 would expose `set_programme_snapshot(...)` is obsolete.

Chat 2 now owns a stable programme-source transport contract through:

```python
from aura_music_studio.shared_sky_transport_domain import transport
```

Chat 3 registers or reuses one tenant/project-scoped source:

- `source_type = "studio_program"`
- `source_ref = "studio://<project_id>/programme/main"`

Chat 3 remains authoritative for which Studio scene is Preview and which scene snapshot is Programme. Chat 2 transports the continuous `studio_program` feed and remains authoritative for aggregate broadcast state, destination runs, internal playback, delivery degradation/recovery, transport correlation/trace IDs, recording handoff metadata and provider/runtime capability.

A CUT or transition while transport is active is accepted only when Chat 2 confirms the active broadcast is already bound to the correct ready `studio_program` source. Chat 3 never silently swaps an active transport to another source.

For an inactive transport, the adapter may register/reuse the stable `studio_program` source and configure the broadcast before preflight.

## Chat 2 transport interfaces consumed by Chat 3

Chat 3 directly consumes the merged Chat 2 service methods:

- `register_source(...)`
- `source(...)`
- `configure(...)`
- `status(...)`
- `preflight(...)`
- `start(...)`
- `stop(...)`
- `retry_destination(...)`
- `request_recording(...)`
- `highlight_markers(...)`
- `add_highlight_marker(...)`

Chat 3 does not call the legacy relay directly for Go Live/Stop.

Transport start/stop/retry uses Chat 2 durable idempotency keys and state transitions. The Professional Studio generates a new bounded operation key for every explicit operator action.

## Recording truth boundary

Chat 2 currently supports recording requests for:

- `programme`
- `clean_feed`
- `isolated_source`
- `audio_tracks`

A Chat 3 recording request is not represented as actively recording until Chat 2 reports the authoritative recording state.

Chat 2 does not currently expose an independent `stop_recording(...)` operation. Chat 3 therefore does not fabricate a manual recording-stop success. Finalization remains owned by the recording writer/broadcast lifecycle until that canonical contract expands.

## Operator pages

- `GET /shared-sky/studio?project_id=<id>&profile_key=landscape-1080`
- `GET /shared-sky/studio/professional?project_id=<id>&profile_key=landscape-1080`

The Professional page includes the compositor, scene/source controls, history controls, audio meter surface and authoritative transport console.

## Core Studio session routes

- `POST /shared-sky/studio/api/sessions`
- `GET /shared-sky/studio/api/sessions/{session_id}`
- `GET /shared-sky/studio/api/sessions/{session_id}/versions`
- `POST /shared-sky/studio/api/sessions/{session_id}/preview`
- `POST /shared-sky/studio/api/sessions/{session_id}/cut`
- `POST /shared-sky/studio/api/sessions/{session_id}/transition`
- `POST /shared-sky/studio/api/sessions/{session_id}/transition/complete`
- `GET /shared-sky/studio/api/sessions/{session_id}/preflight`
- `GET /shared-sky/studio/api/sessions/{session_id}/aura/diagnostics`

## Canonical Chat 2 Studio bridge/operator routes

- `GET /shared-sky/studio/api/sessions/{session_id}/transport/status`
- `POST /shared-sky/studio/api/sessions/{session_id}/transport/bind`
- `POST /shared-sky/studio/api/sessions/{session_id}/transport/preflight`
- `POST /shared-sky/studio/api/sessions/{session_id}/transport/recordings`
- `POST /shared-sky/studio/api/sessions/{session_id}/broadcast`
- `POST /shared-sky/studio/api/sessions/{session_id}/transport/start`
- `POST /shared-sky/studio/api/sessions/{session_id}/transport/stop`
- `POST /shared-sky/studio/api/sessions/{session_id}/transport/retry-destination`
- `POST /shared-sky/studio/api/sessions/{session_id}/markers`
- `GET /shared-sky/studio/api/sessions/{session_id}/markers`

The Professional transport toolbar polls the authoritative transport-status route while a broadcast is attached. It renders `live`, `degraded`, `reconnecting`, `stopping`, terminal and offline states from Chat 2 only; a button click never creates local LIVE truth.

## Atomic history/scene/source routes

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

A multi-source move/resize/rotate/align/distribute gesture is one atomic history transaction and one optimistic Studio-version increment.

Undo/Redo restores the canonical Preview graph with stable IDs. It never rewrites `programme_snapshot_json`. Restored camera/microphone/screen sources return detached and require explicit browser permission again; a `MediaStream` is never serialized.

## Other production routes

Scene/source compatibility:

- `POST /shared-sky/studio/api/scenes/{scene_id}/duplicate`
- `POST /shared-sky/studio/api/projects/{project_id}/scenes/reorder`
- `PATCH /shared-sky/studio/api/sessions/{session_id}/sources/{source_id}/transform`
- `PATCH /shared-sky/studio/api/sessions/{session_id}/sources/{source_id}/audio`
- `PATCH /shared-sky/studio/api/sessions/{session_id}/sources/{source_id}/cue`
- `PATCH /shared-sky/studio/api/sessions/{session_id}/profile`

Templates/audio:

- `GET /shared-sky/studio/api/templates`
- `POST /shared-sky/studio/api/projects/{project_id}/templates/instantiate`
- `GET /shared-sky/studio/api/audio/presets`
- `POST /shared-sky/studio/api/sessions/{session_id}/sources/{source_id}/audio/preset`

Brand Kits:

- `POST /shared-sky/studio/api/projects/{project_id}/brand-kits`
- `PUT /shared-sky/studio/api/projects/{project_id}/brand-kits/{kit_id}`
- `GET /shared-sky/studio/api/projects/{project_id}/brand-kits`

Scheduling/participants/layout:

- `POST /shared-sky/studio/api/sessions/{session_id}/schedules`
- `DELETE /shared-sky/studio/api/sessions/{session_id}/schedules/{schedule_id}`
- `GET /shared-sky/studio/api/sessions/{session_id}/participants`
- `GET /shared-sky/studio/api/layout/{layout_key}/{count}?profile_key=<profile>`

Legacy recording compatibility routes remain reachable, but canonical Professional Studio recording actions now use the Chat 2 bridge described above.

## Persistence

Chat 3 adds only additive/idempotent tables in the canonical Shared Sky SQLite database:

- `shared_sky_studio_sessions`
- `shared_sky_studio_versions`
- `shared_sky_brand_kits`
- `shared_sky_scene_metadata`
- `shared_sky_studio_history`

Chat 2 remains owner of its transport tables, including programme sources, transport sessions, destination runs, transport events/rate limits/idempotency, recordings, destination presets and highlight markers.

No `localStorage` or browser object is authoritative.

## Preview / Programme contract

1. Editing/selecting Preview does not alter Programme.
2. CUT snapshots the current Preview graph and changes Programme only after the transport-side composition boundary accepts it.
3. TRANSITION creates an in-progress token and immutable target snapshot; double transitions are rejected.
4. Completion requires the same token/version.
5. Reduced-motion still performs explicit state transitions while motion duration becomes zero where appropriate.
6. Stale tabs/operators receive 409 and cannot overwrite newer Studio state.
7. Undo/Redo and media-cue edits leave committed Programme unchanged until the next explicit take.
8. Visible private/backstage or unsafe browser sources cannot enter Programme.

## Production profiles

- `landscape-1080` — 1920×1080 / 16:9
- `portrait-1080` — 1080×1920 / 9:16
- `square-1080` — 1080×1080 / 1:1

These are composition profiles, not provider-capability claims. Chat 2/provider adapters remain authoritative for actual destination capability.

## Browser compositor and capture

Implemented:

- pointer move/resize/rotate
- Shift rotation snapping
- multi-select
- alignment/distribution
- arrow-key nudging
- numeric transforms/effects
- safe/title-safe/centre guides
- zoom/profile reflow
- browser camera/microphone/screen permission and reconnect
- browser track cleanup on removal/end/unload
- real Web Audio analyser meters only when a real stream is attached

Fixed focus-safe keys currently include Alt+C CUT, Ctrl+Enter TRANSITION, Ctrl/Cmd+Z Undo, Ctrl/Cmd+Shift+Z or Ctrl/Cmd+Y Redo and arrow nudging.

## Source/privacy/security contract

Programme snapshot and persisted Studio state reject recursively detected provider secret material such as stream keys, OAuth/access/refresh tokens, credentials, passwords, private keys and client secrets.

Browser-source URLs are HTTP(S) only, reject embedded credentials, localhost/local names and private/loopback/link-local/reserved addresses.

Capture permissions and MediaStreams remain browser-local. Restored capture-source metadata does not pretend the capture is still attached.

## Original graphics

Current renderer-backed types:

- Lower Third
- Title
- Subtitle
- Social Handle
- Banner
- Sponsor Card
- Custom Text

Typed style fields are bounded and user text is escaped. Arbitrary CSS is not persisted through the typed graphic schema.

## Scene templates

Original Shared Sky starters currently include Camera Full Screen, Camera + Chat, Creator + Canvas, Canvas Only, Interview 2-Up, Panel/Grid, Screen Share + Presenter, Tutorial, Music Performance, Gameplay, Premiere, BRB, Starting Soon, Ending and Custom.

Capture/guest/media slots begin hidden until a real source/participant is attached. Template creation rolls back the new scene when any slot creation fails.

## Chat 4/5/6/7/8/9 handoffs

Chat 4 owns community/viewer event truth. Chat 3 may render display-only chat/poll/Q&A/caption bindings.

Chat 5 owns Cosmic Creation Coins, Gift transactions, balances, creator liabilities, reversals and financial truth. Chat 3 graphics may display approved read-only supporter/Gift information only.

Chat 6 owns guest invitations, green-room authority, participant moderation, Battle lifecycle/timers/scores. Chat 3 currently provides deterministic one-to-eight layout geometry and a fail-closed read-only participant adapter. `connected` never implies `programme`.

Chats 7/8 own creative-editor/game capture and project provenance. Chat 3 owns placement/composition after a safe canonical source handoff.

Chat 9 may consume authorised Studio/project/history/transport state but does not bypass Chat 3 mutation/version guards.

## Chat 10/11 handoff

Chat 10 should consume measurable Studio/transport events and harden browser matrix, resource pressure, telemetry, runtime infrastructure and security controls without fabricating media metrics.

Chat 11 should verify exact integration ancestry, route reachability, migrations on existing databases, Preview/Programme isolation, stable-ID Undo/Redo, transition conflicts, secret rejection, browser-capture cleanup, Chat 2 source binding, idempotent start/stop/retry, preflight blockers, recording truth, marker persistence, participant staging separation and exact-head CI/Security/Self-Host evidence.

## Aura assistance

`GET /shared-sky/studio/api/sessions/{session_id}/aura/diagnostics` remains advisory only. It derives recommendations from current Studio/transport evidence and performs no consequential action.

## Remaining known gaps

- Chat 6 real guest/green-room media authority and invite/mute/remove moderation actions are not merged into Chat 3.
- Dedicated authoritative remote pre-recorded playout remains a transport/worker dependency.
- Brand Kit media-library integration still needs deeper canonical asset type/size/licence validation; current kits store bounded project-owned references.
- Advanced buses/PFL/solo/noise gate/parametric EQ/de-esser remain absent unless a tested real audio-processing path exists.
- QR generation, animated/data-driven ticker graphics and richer motion authoring remain follow-up work.
- Configurable per-operator hotkey profiles and permission-bounded multi-action macros remain follow-up work; current hotkeys are fixed and focus-safe.
- Chat 10 browser-matrix E2E/accessibility/resource-pressure hardening remains a release dependency.
- Provider approval, deployed origin/CDN, media termination, SFU/guest infrastructure and recording-writer deployment remain external capability gates; code does not convert an absent deployment into a production-ready service.
- Aura remains advisory and cannot autonomously start/stop transport, CUT Programme, alter participants, change destinations or request recordings.
