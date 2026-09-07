# Chat 7 Creation Studios Go Live & Create Integration Contract

Status: current Chat 7 creation-studio integration contract for `development/full-site-build` after the Chat 3 Shared Sky production-admission merge.

## Scope boundary

Chat 7 owns the safe bridge from Music Studio, Video/Cinema Studio and Image/Visual Studio into Shared Sky. It owns project-source discovery, privacy/rights preflight, source-adapter identity, embedded creation-side controls, source lifecycle, project/live correlation and post-live project-return provenance.

Chat 7 does **not** own transport/transcoding/destination delivery (Chat 2), Preview/Programme composition and scene mixing (Chat 3), viewer/community truth (Chat 4), Coin/Gift financial truth (Chat 5), Battle scoring/participant authority (Chat 6), Game Forge (Chat 8), production hardening (Chat 10), or final release acceptance (Chat 11).

## Canonical imports

```python
from aura_music_studio import creation_live
from aura_music_studio.creation_live_chat3_bridge import (
    canonical_chat3_source_type,
    programme_truth,
    register_preview_source,
    hide_graph_sources,
)
from aura_music_studio.creation_live_transport_truth import (
    safe_chat2_preflight,
    safe_chat2_status,
)
```

Production route composition is owned by:

```python
from aura_music_studio.creation_live_routes import install_creation_live_api_routes
```

## Embedded studio routes

The creation-live API is mounted under `/creation-live` and the UI is embedded only on:

- `/studio` — Music Studio
- `/video-studio` — Video/Cinema Studio
- `/image-designer` — Image/Poster/Visual Studio

Core API routes include:

- `GET /creation-live/capabilities`
- `GET /creation-live/projects/{project_name}/sources?studio_type=...`
- `GET /creation-live/projects/{project_name}/sources/{source_adapter_id}`
- `GET /creation-live/projects/{project_name}/sources/{source_adapter_id}/media`
- `POST /creation-live/projects/{project_name}/sources/{source_adapter_id}/attach`
- `POST /creation-live/projects/{project_name}/sources/{source_adapter_id}/transition`
- `POST /creation-live/projects/{project_name}/sources/{source_adapter_id}/emergency-hide`
- `POST /creation-live/projects/{project_name}/sources/{source_adapter_id}/detach`
- `POST /creation-live/projects/{project_name}/markers`
- `POST /creation-live/projects/{project_name}/returns`
- `GET /creation-live/projects/{project_name}/community`
- `GET /creation-live/projects/{project_name}/aura-assistance`

## Shared creation-live source descriptor

The durable/public source descriptor is project scoped and contains only safe presentation metadata. It includes, as applicable:

- `source_adapter_id`
- schema/version state
- `studio_type`: `music`, `video_cinema`, `image_visual`
- creative project ID and creator/workspace identity
- source type and safe display name
- media kind and output capabilities
- aspect/frame/sample/channel hints when available
- privacy classification
- explicit inclusion manifest
- rights/provenance preflight
- public version pin/reference
- health and Shared Sky linkage state
- expiry/revocation/version/correlation references

The descriptor never exposes the server-only `server_ref`/backing path, provider credentials, raw storage paths, voice-model internals or hidden project documents.

## Source discovery and safe defaults

Chat 7 discovers only allow-listed project outputs.

Music sources include real ready project audio outputs and explicitly selected stems. Private/reference/training/model directories are excluded from automatic Music output discovery. Selected stems require an explicit warning/confirmation path.

Video sources use ready selected project video outputs. Image/Visual sources use ready selected project artwork outputs. Full-workspace capture is an explicit advanced source and is never selected by default.

The local preview route is derived from the same safe project output used by the source adapter. Whole-workspace preview uses the browser's `getDisplayMedia` permission flow and is explicitly described as higher risk; Chat 7 does not claim application masking can hide unrelated notifications/windows when the browser does not provide that guarantee.

## Rights, voice and likeness preflight

Chat 7 revalidates current source eligibility immediately before attachment. The preflight can return ready, warning/requires-confirmation, or blocked.

Blocked cases include private/restricted project flags, explicit broadcast denial, unauthorised likeness use, private provider/security metadata, missing/revoked voice consent and other project rights blockers.

Voice Profiles used for LIVE output must be authorised for `live_streaming`; generation permission alone is not interpreted as public-LIVE permission.

## Chat 2 transport handoff

Chat 7 dynamically consumes the canonical `shared_sky_transport_domain.transport` contract. Chat 7 registers only a stable safe contribution reference such as `creation-live://<adapter_id>` plus bounded source capabilities. It does not pass destination credentials or create a second broadcast backend.

Transport registration, transport validation and Programme placement are separate truth dimensions. Chat 7's status API exposes a browser-safe projection of Chat 2 preflight/status and does not treat an active transport session as proof that the exact creative source is ON AIR.

## Chat 3 control-room handoff

After the current Chat 3 production-admission merge, Chat 7 uses `creation_live_chat3_bridge.py` as the exact compatibility seam.

### Canonical source-type mapping

Chat 7 does not add competing source types to Chat 3. It maps safe project media onto Chat 3's existing source vocabulary:

- Music/audio -> `audio`
- Video/audiovisual -> `video`
- Image/still -> `image`
- data/presentation or advanced whole-workspace contribution -> `presentation`

The Chat 7 adapter identity is retained in `config.creation_live_adapter_id`. Safe provenance/capability metadata is retained in source config; server backing paths are not.

### Preview materialisation

When a creator attaches a Chat 7 source and an existing Chat 3 control-room session already exists for the same creator, Shared Sky project and broadcast, Chat 7 idempotently materialises that source in the session's current Preview scene.

Chat 7 does **not** create a Chat 3 session merely because Go Live & Create was opened or attached. If no control-room session exists, the response reports `control_room_not_open` and remains truthful.

Repeated attachment does not create duplicate Chat 3 scene sources. The bridge reuses the scene source carrying the same `creation_live_adapter_id`.

### Exact Programme truth

ON-AIR truth is read from Chat 3's immutable `programme_snapshot` for the creator's current studio session. Chat 7 searches the committed snapshot for the exact `creation_live_adapter_id` and reports:

- `control_room_not_open`
- `not_on_programme`
- `programme_hidden`
- `on_programme`

`on_air=true` requires both:

1. the exact adapter to be visible in Chat 3's committed Programme snapshot; and
2. the authoritative Shared Sky broadcast to be `live`, `degraded` or `reconnecting`.

A source in Preview, a Chat 2 registered source, or an active transport session is insufficient by itself.

### Detach semantics

Chat 7 detach hides every matching Chat 3 scene-graph occurrence for future composition. It deliberately does **not** rewrite an already committed Programme snapshot. If the immutable Programme still contains the source, the status can remain truthfully `on_programme`/ON AIR until Chat 3 performs a real CUT/TRANSITION to another safe scene/source.

This preserves Chat 3 authority and avoids the dangerous false claim that editing a Preview graph retroactively changed Programme.

## Chat 4 / Chat 5 / Chat 6 contracts

Creation-side community state is read/display only from owning systems:

- Chat 4: chat/Q&A/polls/reactions/viewer presence when authoritative contracts are available.
- Chat 5: Gift display/eligibility/goal state only; Chat 7 never debits Coins or calculates payouts.
- Chat 6: participant/Battle identity/state only; Chat 7 never creates score/timer authority.

Incoming community/Gift/Battle events never mutate creative project content merely by arriving. Any viewer suggestion must become a separate creator-approved project action through normal project edit/undo/audit controls.

## Create -> showcase state model

Chat 7 keeps presentation mode separate from live-session identity. Supported intent modes are studio-specific subsets of:

- creating
- tutorial
- review
- rehearsal
- performance
- premiere
- showcase
- gallery
- listening_party
- brb
- detached

Changing mode does not start a second hidden Shared Sky session. Chat 3 remains authoritative for the actual Programme scene/source change.

## Emergency hide / BRB boundary

The creation workspace can immediately record BRB/emergency-hide intent. Chat 7 never claims an on-air cut occurred unless Chat 3 commits that Programme change.

The current Chat 3 contract does not yet expose a single purpose-built `hide this exact live source now / switch to approved BRB scene` API for Chat 7. Therefore immediate Programme replacement remains a neighbouring-contract follow-up; source graph hiding alone is not represented as an on-air cut.

## Recording/highlight return

Returned recording/highlight state is reconciled against Chat 2 when that recording authority is available. Chat 7 retains project/live/source/timestamp/asset provenance and deduplicates repeated return-import IDs.

Chat 7 does not invent a local binary file when the authoritative shared media resolver has not materialised a tenant-safe asset. Processing/incomplete/recovered/failed states remain explicit.

## Security invariants

- all creative project/source access is creator/member scoped;
- Shared Sky project/broadcast/scene access is server-authorised by user ownership;
- Chat 7 source backing references remain server-only;
- no arbitrary URL/server-file injection is accepted as a project source;
- no provider secrets or credentials enter Chat 3 source config;
- Chat 3 Preview materialisation requires project/session identity agreement;
- repeated source registration is idempotent;
- stale source/editor mutations retain optimistic-version protections;
- detachment/revocation never deletes creative project data;
- Chat 3 Programme truth is read-only from the committed snapshot.

## Tests and fixtures

Focused Chat 7/Chat 3 reconciliation tests live in:

`tests/test_creation_live_chat3_bridge.py`

They verify:

- canonical Chat 3 type mapping;
- exact adapter matching in immutable Programme snapshots;
- a LIVE transport/broadcast does not make a different source ON AIR;
- idempotent Preview materialisation;
- no server backing path enters Chat 3 config;
- detach hides the live graph without falsifying an already-committed Programme snapshot;
- blocked rights and missing control-room sessions fail closed.

Existing Chat 7 route/privacy/authority tests and Chat 3 control-room tests remain part of the repository-wide suite.

## Current known boundaries for Chat 10/11

- browser/system capture capabilities still vary by browser/device and must be reported truthfully;
- Music has real project audio outputs but does not yet expose one universal browser master-bus MediaStream across every DAW path;
- Video/Image use ready project outputs where canonical application canvas capture handles are unavailable;
- immediate emergency replacement of an already-on-air source requires a dedicated Chat 3 BRB/source-hide Programme action;
- binary post-live media import still depends on the canonical tenant-safe shared media resolver;
- production media-plane/provider/runtime evidence remains outside Chat 7 and must be release-gated by Chat 10/11.
