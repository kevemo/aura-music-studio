# Chat 3 Shared Sky Studio Integration Contract

Status: production-facing Chat 3 control-room implementation targeting `development/full-site-build`.

Chat 3 owns professional Studio composition, Preview/Programme state, scene/source editing, operator controls, graphics, Studio-local recovery and the composition-side handoff into Shared Sky transport. It does **not** own Chat 2 transport/delivery, Chat 4 viewer/community truth, Chat 5 financial truth, Chat 6 participant/Battle truth, Chat 7/8 editor capture authority, Chat 10 infrastructure hardening, or Chat 11 final release acceptance.

## Canonical modules

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
  - implemented-field audio presets
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
  - typed original lower thirds/titles/subtitles/social banners/sponsor/custom text graphics
- `aura_music_studio.shared_sky_studio_recovery_hardening`
  - records history-managed Studio edits in the canonical bounded 50-version recovery ledger in the same SQLite transaction
- `aura_music_studio.shared_sky_chat2_studio_integration`
  - canonical Chat 3 composition -> Chat 2 `studio_program` source adapter
  - authoritative transport status/preflight
  - Chat 2 recording request/status compatibility
- `aura_music_studio.shared_sky_chat2_studio_operator`
  - attach-broadcast, start, stop, destination retry and marker actions
- `aura_music_studio.shared_sky_professional_transport_toolbar`
  - Professional Studio transport console consuming mounted Chat 2/Chat 3 server contracts
- `aura_music_studio.shared_sky_operator_profiles`
  - project/user-scoped persisted hotkey and macro profiles
  - shortcut normalization/reserved-key rejection
  - optimistic profile versioning and one-active-profile state
  - permission-bounded macro command schema
- `aura_music_studio.shared_sky_professional_operator_ui`
  - server-profile selection/activation
  - capture-phase custom hotkey dispatch that avoids double-firing fallback controls
  - explicit sequential macro execution with Programme confirmation
- `aura_music_studio.shared_sky_motion_graphics`
  - typed ticker and timezone-aware countdown creation through atomic Studio history
  - static ticker or read-only authoritative transport/recording bindings only
  - no arbitrary network data source
- `aura_music_studio.shared_sky_professional_motion_graphics_ui`
  - real CSS ticker animation
  - reduced-motion fallback
  - browser countdown renderer
  - read-only Chat 2 transport/recording ticker values

## Canonical transport ownership after Chat 2 merge

The earlier compatibility assumption that Chat 2 would expose `set_programme_snapshot(...)` is obsolete.

Chat 2 owns the stable programme-source transport contract through:

```python
from aura_music_studio.shared_sky_transport_domain import transport
```

Chat 3 registers or reuses one tenant/project-scoped source:

- `source_type = "studio_program"`
- `source_ref = "studio://<project_id>/programme/main"`

Chat 3 remains authoritative for the editable Preview graph and the committed Programme composition snapshot. Chat 2 transports the continuous `studio_program` feed and remains authoritative for aggregate broadcast state, destination runs, internal playback, delivery degradation/recovery, correlation/trace IDs, recording handoff metadata and provider/runtime capability.

A CUT or transition while transport is active is accepted only when Chat 2 confirms the active broadcast is already bound to the correct ready `studio_program` source. Chat 3 never silently swaps an active transport to another source.

For an inactive transport, Chat 3 may register/reuse the stable `studio_program` source and configure the broadcast before preflight.

## Chat 2 interfaces consumed by Chat 3

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

Chat 3 does not call the legacy relay directly for Go Live/Stop. Start/stop/retry uses Chat 2 durable idempotency and lifecycle state. The Professional Studio generates a new bounded operation key for each explicit operator action.

## Recording truth boundary

Chat 2 supports recording requests for:

- `programme`
- `clean_feed`
- `isolated_source`
- `audio_tracks`

A request is not represented as actively recording until Chat 2 reports that state.

Chat 2 does not currently expose an independent `stop_recording(...)` operation. Chat 3 does not fabricate one. Finalization remains owned by the recording writer/broadcast lifecycle until the canonical contract expands.

## Operator pages

- `GET /shared-sky/studio?project_id=<id>&profile_key=landscape-1080`
- `GET /shared-sky/studio/professional?project_id=<id>&profile_key=landscape-1080`

Professional Studio includes the compositor, scene/source controls, history controls, measured audio surface, authoritative transport console, operator profiles/macros and motion-graphic controls.

## Core Studio routes

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

The transport toolbar polls authoritative status while a broadcast is attached. It renders `live`, `degraded`, `reconnecting`, `stopping`, terminal and offline states from Chat 2 only; button clicks never create local LIVE truth.

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

Undo/Redo restores the canonical Preview graph with stable IDs and never rewrites `programme_snapshot_json`. Restored camera/microphone/screen sources return detached and require explicit browser permission again; MediaStreams are never serialized.

## Operator profile routes

- `GET /shared-sky/studio/api/projects/{project_id}/operator-profiles`
- `POST /shared-sky/studio/api/projects/{project_id}/operator-profiles`
- `PUT /shared-sky/studio/api/projects/{project_id}/operator-profiles/{profile_id}`
- `POST /shared-sky/studio/api/projects/{project_id}/operator-profiles/{profile_id}/activate`
- `DELETE /shared-sky/studio/api/projects/{project_id}/operator-profiles/{profile_id}`

Current macro commands are deliberately bounded to:

- `cut`
- `transition`
- `undo`
- `redo`
- `scene_next`
- `scene_previous`
- `marker_highlight`

Transport, recording, participant and destination mutations are not macro commands. A macro containing CUT or TRANSITION must carry `confirm_programme=true`, and the Professional UI still asks the operator for confirmation every run.

Reserved browser/system shortcuts such as reload/close-tab/close-window combinations are rejected. Alphanumeric hotkeys require a modifier. No profile stores arbitrary JavaScript, URL handlers or provider credentials.

## Motion-graphic routes

- `POST /shared-sky/studio/api/sessions/{session_id}/graphics/ticker`
- `POST /shared-sky/studio/api/sessions/{session_id}/graphics/countdown`

Ticker bindings are limited to:

- `static`
- `transport_state`
- `recording_state`

`transport_state` and `recording_state` are read-only projections of already-authoritative Chat 2 state held by Studio. They do not fetch arbitrary external endpoints.

Countdown `target_at` must be timezone-aware ISO 8601. The renderer updates from the local clock; when the target elapses it shows the configured completion text. Reduced-motion disables scrolling animation rather than removing ticker content.

Both ticker and countdown creation are history-managed Preview mutations; Programme remains unchanged until explicit CUT/TRANSITION.

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

## Persistence

Chat 3 adds additive/idempotent tables in the canonical Shared Sky SQLite database:

- `shared_sky_studio_sessions`
- `shared_sky_studio_versions`
- `shared_sky_brand_kits`
- `shared_sky_scene_metadata`
- `shared_sky_studio_history`
- `shared_sky_operator_profiles`

Chat 2 remains owner of transport tables including programme sources, transport sessions, destination runs, transport events/rate limits/idempotency, recordings, destination presets and highlight markers.

No `localStorage` or browser object is authoritative for Studio/project/operator state.

## Preview / Programme contract

1. Editing/selecting Preview does not alter Programme.
2. CUT snapshots Preview and changes Programme only after the composition/transport boundary accepts it.
3. TRANSITION creates an in-progress token and immutable target snapshot; double transitions are rejected.
4. Completion requires the same token/version.
5. Reduced-motion preserves explicit state transitions while motion duration becomes zero where appropriate.
6. Stale tabs/operators receive 409 and cannot overwrite newer Studio state.
7. Undo/Redo, media-cue, operator-profile and motion-graphic edits do not rewrite committed Programme.
8. Visible private/backstage or unsafe browser sources cannot enter Programme.

## Production profiles

- `landscape-1080` — 1920×1080 / 16:9
- `portrait-1080` — 1080×1920 / 9:16
- `square-1080` — 1080×1080 / 1:1

These are composition profiles, not provider-capability claims. Chat 2/provider adapters remain authoritative for destination capability.

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
- fixed focus-safe fallback hotkeys plus persisted custom operator profiles

Fixed fallback keys include Alt+C CUT, Ctrl+Enter TRANSITION, Ctrl/Cmd+Z Undo, Ctrl/Cmd+Shift+Z or Ctrl/Cmd+Y Redo and arrow nudging. A configured active profile can override a matching shortcut through the capture-phase handler without allowing the fixed handler to fire a second time.

## Audio truth boundary

Professional Studio currently measures real browser signal with `AudioContext`/`AnalyserNode` when an actual capture stream is attached. Persisted audio configuration supports gain/pan/delay/high-pass/compressor/limiter metadata and preset selection.

The current Professional browser analyser is **not** yet the authoritative Chat 2 contribution audio bus. Therefore Chat 3 does not claim that advanced PFL/solo/noise gate/parametric EQ/de-esser controls affect the delivered programme. Those controls remain blocked until a tested contribution-bus/media handoff exists. Decorative mixer switches are not added merely to satisfy UI scope.

## Source/privacy/security contract

Programme snapshots and persisted Studio state reject recursively detected provider secret material such as stream keys, OAuth/access/refresh tokens, credentials, passwords, private keys and client secrets.

Browser-source URLs are HTTP(S) only and reject embedded credentials, localhost/local names and private/loopback/link-local/reserved addresses.

Capture permissions and MediaStreams remain browser-local. Restored capture-source metadata does not pretend capture is still attached.

## Graphics

Renderer-backed typed graphics now include:

- Lower Third
- Title
- Subtitle
- Social Handle
- Banner
- Sponsor Card
- Custom Text
- Animated Ticker
- Countdown

Typed style fields are bounded and user text is escaped. Arbitrary CSS is not persisted. Ticker binding does not expose an arbitrary URL/data-source channel.

QR generation is not claimed because the repository does not currently contain an approved QR encoder and Chat 3 does not send private Studio data to an external QR service.

## Scene templates

Original Shared Sky starters include Camera Full Screen, Camera + Chat, Creator + Canvas, Canvas Only, Interview 2-Up, Panel/Grid, Screen Share + Presenter, Tutorial, Music Performance, Gameplay, Premiere, BRB, Starting Soon, Ending and Custom.

Capture/guest/media slots begin hidden until a real source/participant is attached. Template creation rolls back the new scene when any slot creation fails.

## Brand Kit asset authority

Brand Kits currently store bounded project-owned references and reject secret-shaped material.

The repository has a project-root Music `AssetLibrary` and a separate rights/provenance-aware Social House media library. Neither is a canonical cross-product Shared Sky asset authority. Chat 3 does not create a third competing media store merely to claim deeper Brand Kit integration. Cross-product type/size/licence validation should attach when the shared asset authority exists.

## Chat 4/5/6/7/8/9 handoffs

Chat 4 owns community/viewer event truth. Chat 3 may render display-only chat/poll/Q&A/caption bindings.

Chat 5 owns Cosmic Creation Coins, Gift transactions, balances, creator liabilities, reversals and financial truth. Chat 3 graphics may display approved read-only supporter/Gift information only.

Chat 6 owns guest invitations, green-room authority, participant moderation, Battle lifecycle/timers/scores. Chat 3 currently provides deterministic one-to-eight layout geometry and a fail-closed read-only participant adapter. `connected` never implies `programme`.

Chats 7/8 own creative-editor/game capture and project provenance. Chat 3 owns placement/composition after a safe canonical source handoff.

Chat 9 may consume authorised Studio/project/history/transport state but does not bypass Chat 3 mutation/version guards.

## Chat 10/11 handoff

Chat 10 should consume measurable Studio/transport events and harden browser matrix, resource pressure, telemetry, runtime infrastructure and security controls without fabricating media metrics.

Chat 11 should verify exact integration ancestry, route reachability, migrations on existing databases, Preview/Programme isolation, stable-ID Undo/Redo, transition conflicts, secret rejection, browser-capture cleanup, Chat 2 source binding, idempotent start/stop/retry, preflight blockers, recording truth, marker persistence, participant staging separation, operator-profile constraints, ticker/countdown safety and exact-head CI/Security/Self-Host evidence.

## Aura assistance

`GET /shared-sky/studio/api/sessions/{session_id}/aura/diagnostics` remains advisory only. It derives recommendations from current Studio/transport evidence and performs no consequential action.

## Remaining known gaps

- Chat 6 real guest/green-room media authority and invite/mute/remove moderation actions are not merged into Chat 3.
- Dedicated authoritative remote pre-recorded playout remains a transport/worker dependency.
- Brand Kit cross-product media validation waits for a canonical shared asset authority rather than duplicating existing domain stores.
- Advanced audio buses/PFL/solo/noise gate/parametric EQ/de-esser remain absent from the authoritative programme path until a tested contribution-bus contract exists.
- QR generation and richer authored motion/animation remain follow-up work; ticker/countdown are implemented.
- Chat 10 browser-matrix E2E/accessibility/resource-pressure hardening remains a release dependency.
- Provider approval, deployed origin/CDN, media termination, SFU/guest infrastructure and recording-writer deployment remain external capability gates; code does not convert absence into production-ready service.
- Aura remains advisory and cannot autonomously start/stop transport, CUT Programme, alter participants, change destinations, run macros or request recordings.
