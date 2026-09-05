# Chat 3 Shared Sky Studio Integration Contract

Status: Chat 3 production-control-room foundation. This document describes the exact compatibility surface implemented on top of the current `development/full-site-build` Shared Sky control plane. It does not transfer ownership of transport, viewer community, Gifts, Battles, editor capture, or release infrastructure to Chat 3.

## Runtime modules and routes

- `aura_music_studio.shared_sky_control_room`
  - `StudioRepository`: Chat 3 persistence extension using the same SQLite database path as `SharedSkyStore`.
  - `StudioService`: Preview/Programme, scene-copy/reorder, transform/audio autosave and safe snapshot orchestration.
  - `SharedSkyTransportCompatibilityAdapter`: narrow Chat 2 boundary; live programme mutation fails closed until Chat 2 supplies an authoritative commit method.
  - `install_shared_sky_control_room(app)`: idempotent production route mounting helper.
- Existing Chat 2 source of truth: `aura_music_studio.shared_sky_streaming_studios.shared_sky`.
- Operator page: `GET /shared-sky/studio?project_id=<id>&profile_key=landscape-1080`.
- Session API:
  - `POST /shared-sky/studio/api/sessions`
  - `GET /shared-sky/studio/api/sessions/{session_id}`
  - `GET /shared-sky/studio/api/sessions/{session_id}/versions`
  - `POST /shared-sky/studio/api/sessions/{session_id}/preview`
  - `POST /shared-sky/studio/api/sessions/{session_id}/cut`
  - `POST /shared-sky/studio/api/sessions/{session_id}/transition`
  - `POST /shared-sky/studio/api/sessions/{session_id}/transition/complete`
- Scene/source production operations:
  - `POST /shared-sky/studio/api/scenes/{scene_id}/duplicate`
  - `POST /shared-sky/studio/api/projects/{project_id}/scenes/reorder`
  - `PATCH /shared-sky/studio/api/sessions/{session_id}/sources/{source_id}/transform`
  - `PATCH /shared-sky/studio/api/sessions/{session_id}/sources/{source_id}/audio`
- Brand Kit:
  - `POST /shared-sky/studio/api/projects/{project_id}/brand-kits`
  - `PUT /shared-sky/studio/api/projects/{project_id}/brand-kits/{kit_id}`
  - `GET /shared-sky/studio/api/projects/{project_id}/brand-kits`
- Capability surfaces:
  - `GET /shared-sky/studio/api/sessions/{session_id}/recording`
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
- `config.privacy`: must resolve to a Programme-safe state for visible sources
- browser sources: HTTP(S) only, no embedded credentials, no loopback/private/local host

Any recursively detected provider secret key (`stream_key`, OAuth/access/refresh tokens, credentials, passwords, private keys, client secrets, etc.) rejects persistence or Programme snapshot creation.

## Preview / Programme state machine

1. Editing/selecting Preview increments the studio optimistic version and does not alter Programme.
2. CUT captures the current Preview graph into an immutable snapshot, requests an authoritative transport commit, then writes Programme only if accepted.
3. TRANSITION first enters `in_progress` with a unique transition token and target snapshot. A second transition is rejected with 409.
4. Completion requires the same token and version. Transport rejection aborts the transition and leaves the previous Programme snapshot unchanged.
5. Reduced-motion mode drives transition duration to zero while preserving explicit state/commit semantics.
6. A stale tab/operator using an old version receives 409; it cannot overwrite newer studio state.

## Chat 2 handoff

Current adapter import: `SharedSkyTransportCompatibilityAdapter`.

Chat 2 may add this method to its canonical store/service without changing Chat 3 call sites:

```python
def set_programme_snapshot(user_id: str, broadcast_id: str, snapshot: dict, *, correlation_id: str) -> dict:
    # Required result keys: accepted: bool, state: str, optional reason: str
    ...
```

When a broadcast is `live` or `starting` and that method is absent, Chat 3 returns a real 503 and does not claim Programme switched. Draft/offline studio work remains available.

Optional recording compatibility method expected from Chat 2:

```python
def recording_status(user_id: str, broadcast_id: str) -> dict: ...
```

Until present, Chat 3 reports `supported=false`, `state=unavailable`, `reason="Chat 2 recording contract not merged"`. It never displays a fabricated Recording state.

Scheduling execution remains the existing Chat 2 `shared_sky_schedules` path. Chat 3 must consume that service rather than create a second scheduler.

## Chat 4 handoff — viewer/community overlays

Chat 3 owns presentation only. Safe binding kind values are `chat`, `poll`, `qa`, `captions`, and `custom_text`. Chat 4 supplies authoritative event/state feeds. Binding payloads must be secret-free IDs/configuration; no community ledger is persisted here.

## Chat 5 handoff — Gifts/supporter presentation

Safe display-only binding kinds are `gift_goal` and `supporter`. Chat 3 does not debit Cosmic Creation Coins, derive balances, calculate creator liabilities, or persist financial truth. Chat 5 event/goal IDs are rendered as external authoritative state.

## Chat 6 handoff — participants/Battles

Chat 3 exposes deterministic normalized tile geometry via `participant_layout(layout_key, count, profile_key)` for one through eight supplied participants. Layout keys currently include Solo, Side-by-Side/Interview, Grid, Speaker Focus hook, Host + Guests, Picture-in-Picture hook, Vertical Stack and Battle Teams hook.

Participant identity/order/stage/Battle score/lifecycle remain Chat 6/shared participant authority. Until the participant contract lands, Chat 3 does not claim a connected guest is on-air.

## Chat 7 handoff — Music/Video/Image sources

Provide a canonical Shared Sky source object owned by the current user/project with a stable `id`, `source_type`, `name`, `config`, visibility/lock/z-order and safe provenance/privacy metadata. Private editor panels/tokens must never be represented as a browser source. Chat 3 only snapshots the safe programme source.

## Chat 8 handoff — Game Forge sources

Same source contract as Chat 7. Gameplay/editor/playtest capture adapters own media acquisition. Chat 3 owns scene placement/composition only.

## Chat 9 handoff — Creator surfaces

Chat 9 may read studio project/session history using creator-authorised workflows. Programme mutations must still flow through Chat 3 endpoints with the current optimistic version and normal server permission checks.

## Chat 10 handoff — observability/security/performance

Chat 10 should instrument `studio_cut` / `studio_transition` Shared Sky events and correlation IDs, transition/transport rejection reason codes, browser Web Audio/device failures, 409 stale-version frequency, canvas/effect pressure metrics when measurable, and source cleanup/leaked-media-track diagnostics. No numeric performance value should be synthesized when no browser metric exists.

## Chat 11 release handoff

Release acceptance should verify production route reachability, additive database migration, truthful Chat 2 live-commit/recording capability, CI/security/self-host smoke status, no provider credentials in Studio/Brand Kit snapshots, and that Preview editing cannot mutate the last Programme snapshot.

## Browser audio/canvas behaviour actually implemented

The current operator page uses a DOM compositor backed by persisted normalized transforms. Programme renders from the saved Programme snapshot, while Preview renders the current scene graph. CSS-backed effects represented in the renderer have real paths for brightness, contrast, saturation, hue, blur and rounded masking; unbacked effects are not advertised by this control-room surface.

The audio utility computes RMS/peak/dBFS/clipping from real supplied samples. Browser runtime meter attachment uses `AudioContext`, `MediaStreamAudioSourceNode`, a high-pass `BiquadFilterNode`, `DynamicsCompressorNode`, `GainNode`, and `AnalyserNode`. Meters start explicitly unavailable and only animate after a real `MediaStream` is attached. Local monitoring is off by default.

Hotkeys are ignored while focus is in input/textarea/select/contenteditable controls. Current bindings are Alt+C for CUT and Ctrl+Enter for TRANSITION. Alt+B does not silently take an emergency scene; it reports that a configured BRB scene is required.

## Known compatibility gaps after this slice

- Chat 2 authoritative live Programme registration method has not landed in the current integration branch.
- Chat 2 recording start/stop/status contract has not landed in the current integration branch.
- Chat 6 green-room/participant media authority has not landed in the current integration branch.
- Full drag/resize/rotate pointer compositor, multi-select/snapping and touch gestures are not yet implemented in this slice; normalized transform persistence and numeric accessible controls are.
- Full source creation/device picker, media playlist cueing, scene template instantiation, scheduling form, guest invite UX and Aura recommendation panel remain follow-up Chat 3 units.
- Effects not backed by a real browser processor are intentionally not exposed by the new control-room surface.
