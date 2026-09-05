# Chat 7 Creation Studios Go Live & Create Integration Contract

Status: additive Chat 7 implementation for `development/full-site-build`. This contract owns only the safe Music / Video-Cinema / Image-Visual project-source bridge into Shared Sky. Chat 2 remains transport authority, Chat 3 remains Preview/Programme and composition authority, Chat 4 remains viewer/community authority, Chat 5 remains Coin/Gift financial authority, and Chat 6 remains participant/Battle authority.

## Canonical imports

```python
from aura_music_studio.creation_live import (
    CreationLiveSourceDescriptor,
    CreationLiveStore,
    RightsPreflight,
    SourceCapabilities,
    creation_live_store,
    discover_sources,
    install_creation_live,
    router as creation_live_router,
)
```

The production installer is invoked at the repository's canonical route-composition/reconciliation point in `aura_music_studio.route_integrity.deduplicate_http_routes`. `install_creation_live(app)` is idempotent and mounts the API plus `CreationLiveMiddleware` into the one FastAPI application. It does not create a second creative/live app.

## Studio routes and embedded entry points

Chat 7 injects the same project-aware Go Live & Create client into:

- Music Studio: `/studio`
- Video/Cinema Studio: `/video-studio`
- Image/Poster/Visual Studio: `/image-designer`

Client script: `GET /creation-live/ui.js`.

The control is keyboard-operable, uses a dialog landmark, moves focus to a labelled close control, supports Escape close, uses `aria-live=polite` for bounded status announcements, and never uses colour as the sole LIVE truth. The UI explicitly displays `NOT CONFIRMED ON AIR` unless the authoritative programme adapter can prove the exact source is Programme.

## Shared source descriptor schema

`CreationLiveSourceDescriptor` schema version 1 contains:

- stable `source_adapter_id` and adapter version;
- `studio_type`: `music | video_cinema | image_visual`;
- canonical project/workspace/creator IDs;
- studio-specific safe `source_type`;
- safe display label;
- media kind and optional aspect/fps/sample/channel metadata;
- capability flags;
- privacy classification;
- inclusion manifest and `creation-live-default-v1` exclusion-policy reference;
- rights/preflight result;
- Shared Sky project/broadcast/source references only after server-authorised attachment;
- explicit presentation mode;
- health/version/timestamps/revocation/correlation references;
- optional Creative element/version IDs;
- preview kind.

The public descriptor never contains the server-only `server_ref`, raw local/server paths, destination credentials, provider tokens, OAuth material, private storage URLs or whole project documents. Recursive private-key detection blocks secret-bearing metadata from being accepted into a source preflight.

## Source registry and persistence

Chat 7 adds additive idempotent SQLite tables to the canonical Shared Sky database path:

- `creation_live_sources`
- `creation_live_idempotency`
- `creation_live_returns`
- `creation_live_markers`

`creation_live_sources.version` provides optimistic concurrency. `active_editor_instance_id` prevents a stale duplicate tab from taking over an attached source. Attach/detach operations use durable request-hash-bound idempotency keys.

Transient MediaStream/browser capture grants are never persisted.

## Discovery and safe output policy

Source identity is a deterministic SHA-256-derived ID over creator, project, studio and server-only source key. Discovery is tenant-confined by the existing `tenant_storage.project_path` context.

Default source discovery is allow-list based:

### Music

- active ready Creative `music` / `audio` outputs;
- allow-listed audio files under the current project's `output/` tree;
- files under obvious `private`, `reference`, `training` or `models` paths are not surfaced;
- stem-like paths become explicit `selected_stem` sources and require deliberate rights confirmation;
- whole workspace exists only as an advanced source.

### Video/Cinema

- current active ready Creative `video` element output only;
- server resolves the element's project-relative file and verifies confinement to the current project;
- clean video output is the normal source;
- whole workspace is advanced/high-risk.

### Image/Visual

- current active ready Creative `image` element output only;
- server resolves a project-relative safe media file;
- clean artwork is the normal source;
- whole workspace is advanced/high-risk.

Image/video/music preview media is returned through an authenticated opaque source-adapter route with `private, no-store` and `nosniff`; the descriptor does not expose its backing path.

## Rights, consent and provenance preflight

`RightsPreflight` returns `ready`, `warning`, `blocked` or `unknown`-compatible state semantics.

Chat 7 blocks:

- hidden/private/restricted/collaborator-only/internal-only sources;
- `broadcast_allowed=false`;
- detected secret/provider/security metadata;
- real-person likeness without LIVE permission;
- missing/revoked/ineligible Voice Profiles;
- music cover/remix/backing projects whose authoritative manifest does not confirm rights.

A Voice Profile that is valid for singing or voice conversion is **not** automatically valid for public LIVE output. Chat 7 calls the existing `VoiceProfile.assert_usable("live_streaming")`; live use must therefore be explicitly included in `allowed_uses` and consent must remain active.

Missing complete rights metadata yields a warning requiring creator confirmation; it never fabricates a `cleared for broadcast` result.

## Advanced full-workspace capture

`full_workspace` is always `advanced_workspace`, never the default. Attachment requires both rights-warning confirmation and `full_workspace_confirmed=true`.

The browser preview uses `navigator.mediaDevices.getDisplayMedia` only after the creator selects Preview. The browser's native permission UI remains authoritative. Chat 7 explicitly states that application masking cannot guarantee unrelated windows/notifications are hidden. Preview tracks are stopped when the live drawer closes.

No whole-screen/window capture is silently started by opening Go Live & Create.

## API routes

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

All project operations require the existing membership context. Creative project paths remain server-derived from the current tenant; a client cannot supply a filesystem path.

## Chat 2 transport handoff

Chat 7 dynamically consumes the pending canonical module:

```python
from aura_music_studio.shared_sky_transport_domain import transport
```

When present, attachment calls `transport.register_source(...)` using only:

- authenticated creator ID;
- server-authorised Shared Sky project ID;
- `music_project` or `video_project` contribution type per Chat 2's current contract;
- opaque `creation-live://<source_adapter_id>` source reference;
- safe media/privacy capabilities.

A persisted `transport_source_id` is reused after reconnect rather than registering duplicates.

When Chat 2 is not merged, attachment becomes truthfully `registered`/`compatibility_pending`; the UI explicitly states that the source is safely prepared but no transport/LIVE success is claimed.

Chat 7 never creates destination OAuth, provider credentials, relay/transcode jobs or a second broadcast transport backend.

## Chat 3 control-room handoff

Attachment returns `control_room_handoff` containing only:

- `source_adapter_id`;
- safe display label;
- privacy classification;
- capabilities;
- correlation ID.

Chat 3 owns source placement, crop/transform, scene state, Preview/Programme, mixer, transitions, overlays and programme commit.

A broadcast being in Shared Sky `live` state is **not** considered proof that a Chat 7 project source is on Programme. `_programme_truth` defaults `on_air=false` and `programme_state=unknown`. It only changes if Chat 3 exposes an authoritative source-programme query. This prevents fake ON AIR state.

## Chat 4 / Chat 5 / Chat 6 boundary

`GET /creation-live/projects/{project_name}/community` is deliberately non-authoritative compatibility state until the owning contracts are merged:

- Chat 4: display contract only, no project mutation;
- Chat 5: display only, no Coin debit/balance/payout mutation;
- Chat 6: read only, no local Battle score/timer engine.

Chat 7 does not fabricate chat messages, viewer counts, Gifts or Battle score state.

## Create -> showcase state model

The single source association uses presentation modes rather than creating additional hidden live sessions:

- Music: creating, tutorial, rehearsal, performance, premiere, showcase, listening_party, brb, detached.
- Video: creating, tutorial, review, premiere, showcase, brb, detached.
- Image: creating, tutorial, review, showcase, gallery, brb, detached.

`POST .../transition` preserves the existing Shared Sky project/broadcast association and increments source version. It returns `same_live_session=true`; Chat 3 remains responsible for the actual scene/programme switch.

## Emergency hide / BRB

`POST .../emergency-hide` changes the Chat 7 presentation intent to `brb` using optimistic concurrency. It never deletes editor/project data. Until Chat 3 exposes an authoritative BRB/cut method, the response explicitly says `brb_intent_requested` and does **not** claim Programme changed.

## Markers and post-live return

Markers persist project/live time, source, kind, label and correlation ID. They report `project_mutated=false`.

Post-live return uses one `ReturnAssetRequest` for all three studios and deduplicates by `(user_id, project_name, return_import_id)`. It retains project, studio, live session, recording/highlight, source adapter, processing state, time range, visibility and correlation provenance.

Chat 2's current recording contract returns an asset ID/metadata rather than a local editor blob. Chat 7 therefore persists the provenance link and does **not** invent a local file. A future canonical media-library resolver can materialise the asset once it can prove tenant ownership/readiness. Processing/failed/incomplete/recovered states remain explicit.

## Aura boundary

Aura assistance may recommend the safest currently discoverable source and an original studio preset. The endpoint explicitly reports:

- `consequential_actions_require_creator_confirmation=true`;
- `can_start_or_stop_live=false`;
- `can_reveal_hidden_content=false`;
- `can_enable_full_workspace_capture=false`.

Aura recommendations therefore cannot silently expose private work or start LIVE.

## Browser/device/resource handling

Current browser handling is capability based:

- safe media previews use native audio/video/image playback;
- advanced workspace checks `getDisplayMedia` before presenting it as usable;
- browser denial is reported as denial rather than fake preview success;
- active browser-capture tracks are stopped when the drawer closes;
- no CPU/GPU/bitrate/frame-drop number is fabricated.

The first slice does not yet own a live Web Audio graph tap, WebRTC/SFU, canvas encoder or transport worker. Those remain the existing Music/Video editor runtime plus Chat 2/3/10 integration boundaries.

## Exact privacy exclusions

The descriptor/preflight rejects or excludes secret-bearing keys including API/OAuth/access/refresh/stream keys, passwords, client/private secrets, credentials, storage/server/filesystem paths, provider payloads, training data/file references, collaborator email and billing fields. Safe source discovery does not serialize the whole Creative Manifest.

The default clean Image/Video live contribution is pixel/media presentation; project EXIF/private metadata is not included in the public descriptor. The preview route itself remains private and is not a public Shared Sky asset URL.

## Deterministic tests

`tests/test_creation_live.py` covers:

- descriptor secrecy and studio/source validation;
- stable tenant-scoped source IDs;
- optimistic stale-tab rejection;
- second-editor control rejection;
- durable idempotency replay and conflicting-key rejection;
- private/hidden/secret metadata blocking;
- real-person likeness checks;
- Voice Profile generation-vs-LIVE permission separation;
- cover rights blocking;
- advanced-workspace confirmation semantics;
- browser permission and track cleanup strings;
- non-fabricated ON AIR wording;
- absence of Chat 7 wallet/Battle score implementation;
- complete source lifecycle/privacy/return API route surface;
- idempotent production route installation.

## Known integration gaps / handoff

1. PR #570 (Chat 2) is still open at this branch base. Once merged, Chat 7's dynamic transport adapter activates without a parallel implementation. Re-run contract tests and rebase before merge.
2. PR #572 (Chat 3) is still open at this branch base. Its current contract does not yet expose `source_programme_state`, so Chat 7 correctly reports Programme state unknown/ON AIR false. Chat 3 can add that query without changing Chat 7 source identity.
3. Chat 4/5/6 owning contracts should replace the current display-only compatibility payloads; Chat 7 must never keep a permanent duplicate community/Gift/Battle backend.
4. A true low-latency Music master-bus Web Audio MediaStream tap is not introduced in this slice because the current `/studio` page does not expose one canonical browser audio-graph handle. Chat 7 currently sources ready project audio output without fabricating a live graph. The DAW/audio-runtime owner should expose one safe project programme-bus handle, then Chat 7 can attach it under the same descriptor.
5. Video/Image current main studio pages expose ready project media elements rather than a canonical application render-canvas handle. Chat 7 uses those real clean outputs and does not pretend canvas capture exists. A future explicit render-surface handle can be added without changing the source schema.
6. Returned Chat 2 recording/highlight binaries are not materialised until a canonical tenant-safe asset resolver exists. Provenance/deduplication is implemented now.
7. Final deployment, provider approvals, media-plane capacity and repository-wide release acceptance remain Chat 10/11 concerns.
