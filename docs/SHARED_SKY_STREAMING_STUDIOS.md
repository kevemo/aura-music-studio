# Shared Sky Streaming Studios

**Product:** Shared Sky Streaming Studios  
**Company:** Elevate Souls Productions  
**Access:** approved ESP Creator, Agent, Both and Owner accounts only  
**Primary route:** `/shared-sky`  
**Owner route:** `/owner/shared-sky`

Shared Sky is the private ESP professional live-production and multi-platform distribution surface. It is intentionally an original control plane, not a UI clone of OBS, Restream, StreamYard, TikTok LIVE Studio, Streamlabs or another broadcaster.

## What is implemented in this build

- Native Shared Sky page and 29-section internal menu.
- ESP role gate using the existing approved Creator/Agent/Owner membership boundary.
- Tenant-isolated persistent projects, scenes and sources.
- Scene graph with ordering, layouts, transition choice and source configuration JSON.
- Deep built-in catalogues for source types, visual effects, transitions, audio processors and layouts.
- Browser camera/microphone and screen-preview controls.
- Platform/destination registry with truthful implementation state rather than claiming unavailable provider access.
- Custom RTMP/RTMPS/SRT endpoint model.
- Encrypted destination credential vault using a deployment-owned secret.
- Destination secrets are never returned by Shared Sky APIs.
- Broadcast records, independent destination outputs, lifecycle events and health state.
- Fail-closed broadcast preflight.
- Independent FFmpeg relay process per destination so one destination failure need not terminate every output.
- Relay disabled by default until production infrastructure is configured.
- Schedule persistence for live and pre-recorded events.
- Owner operational status page and global emergency stop.
- Integration links to ESP Broadcast & Tech Desk and the existing Aura LIVE Overlay Studio.
- Tests for tenant boundaries, secret handling, fail-closed preflight, route coverage and catalogue depth.

## Architecture

Shared Sky separates the **control plane** from the **media plane**.

### Control plane

The FastAPI application owns:

1. identity and ESP role authorization;
2. project/scene/source state;
3. destination metadata;
4. encrypted connection credentials;
5. broadcast orchestration;
6. schedules;
7. runtime health and audit events;
8. owner emergency controls.

### Media plane

The media plane is designed to run independently from the ordinary web request process:

1. creator sends one contribution feed to a configured ingest service;
2. Shared Sky creates one supervised output process per selected destination;
3. outputs receive the same contribution feed independently;
4. a single destination failure can be restarted without taking the other destinations down;
5. advanced transcoding profiles can be moved to dedicated GPU/CPU relay workers as the service scales.

The current in-process FFmpeg supervisor is a safe development/single-worker runtime. Production should move the same orchestration contract to a dedicated relay service or worker pool before horizontal web scaling.

## Environment configuration

Never commit real credentials.

```dotenv
# Master switch. Off by default so streaming cannot accidentally start.
SHARED_SKY_RELAY_ENABLED=0

# A long, deployment-managed random secret. Required before any stream key/token is stored.
SHARED_SKY_VAULT_SECRET=

# Contribution ingest base. A broadcast ID is appended at runtime.
# Example shape only; use the endpoint of the ESP-operated ingest service.
SHARED_SKY_INGEST_BASE_URL=

# Optional override. Docker already installs ffmpeg.
SHARED_SKY_FFMPEG_BIN=ffmpeg
```

Production secret management should inject `SHARED_SKY_VAULT_SECRET`; it must not live in source code, screenshots, support notes or general project JSON.

## Platform integration rule

Shared Sky can integrate with a platform only through one of these authorised paths:

- official OAuth/API access granted to the ESP application;
- a creator-provided stream key or endpoint the platform has made available to that creator;
- another documented, permitted ingest mechanism.

Shared Sky must not bypass eligibility gates, reverse-engineer private endpoints or pretend an integration is active while app review/creator permission is still outstanding.

The registry deliberately uses statuses such as `adapter_required`, `platform_review_or_account_access_required`, `custom_rtmp_supported` and `framework_ready`.

## Security model

### Member access

`/shared-sky` and `/shared-sky/api/*` require:

1. a valid active site membership session; and
2. an ESP membership with status `active` or `owner`; and
3. Creator, Agent or Both role unless the user is an Owner.

### Owner access

`/owner/shared-sky` and its owner APIs use the existing owner-session authorisation boundary. The owner page never returns platform credentials.

### Credential storage

`SharedSkyVault` derives a Fernet key from `SHARED_SKY_VAULT_SECRET` and encrypts destination credentials before database persistence. If the secret is missing, secret storage fails closed.

### Relay process safety

- no `shell=True`;
- fixed FFmpeg argument list;
- URL scheme validation;
- no destination URL/stream key in application logs;
- one child process per output for fault isolation;
- relay is off by default;
- emergency stop can terminate active Shared Sky outputs.

## Persistent model

Shared Sky adds these tables to the existing ESP database:

- `shared_sky_projects`
- `shared_sky_scenes`
- `shared_sky_sources`
- `shared_sky_destinations`
- `shared_sky_broadcasts`
- `shared_sky_broadcast_outputs`
- `shared_sky_schedules`
- `shared_sky_events`

Every creator-owned record carries `user_id`; store lookups require ownership before mutation.

## API surface

### Studio/catalogue

- `GET /shared-sky`
- `GET /shared-sky/api/catalog`
- `GET /shared-sky/api/state`

### Projects

- `POST /shared-sky/api/projects`
- `GET /shared-sky/api/projects/{project_id}`
- `PUT /shared-sky/api/projects/{project_id}`
- `DELETE /shared-sky/api/projects/{project_id}`

### Scenes and sources

- `POST /shared-sky/api/projects/{project_id}/scenes`
- `PUT /shared-sky/api/scenes/{scene_id}`
- `DELETE /shared-sky/api/scenes/{scene_id}`
- `POST /shared-sky/api/scenes/{scene_id}/sources`
- `PUT /shared-sky/api/sources/{source_id}`
- `DELETE /shared-sky/api/sources/{source_id}`

### Destinations

- `POST /shared-sky/api/destinations`
- `PUT /shared-sky/api/destinations/{destination_id}`
- `DELETE /shared-sky/api/destinations/{destination_id}`

### Broadcasts

- `POST /shared-sky/api/broadcasts`
- `GET /shared-sky/api/broadcasts/{broadcast_id}/preflight`
- `POST /shared-sky/api/broadcasts/{broadcast_id}/start`
- `POST /shared-sky/api/broadcasts/{broadcast_id}/stop`
- `GET /shared-sky/api/broadcasts/{broadcast_id}/health`

### Schedules

- `POST /shared-sky/api/schedules`
- `DELETE /shared-sky/api/schedules/{schedule_id}`

### Owner

- `GET /owner/shared-sky`
- `GET /owner/shared-sky/api/status`
- `POST /owner/shared-sky/api/emergency-stop`

## Preflight contract

No broadcast starts unless all currently required checks pass:

- project exists and belongs to the requesting ESP member;
- at least one scene exists;
- at least one visible source exists;
- at least one valid enabled destination is selected;
- selected destination endpoints are configured;
- stream credentials exist for credential-based destinations;
- contribution ingest is configured;
- relay is enabled;
- FFmpeg is available;
- encrypted credentials can be decrypted by the configured vault.

A failed preflight returns reasons and does not start a relay.

## Existing ESP systems reused instead of duplicated

### ESP Pro Broadcast & Tech Desk

Shared Sky links to `/command-center/broadcast-tech` for consent-based device profile, checklist, network diagnostics, privacy checks and test-recording readiness.

### Aura LIVE Overlay Studio

Shared Sky links to `/live-overlay-studio` for secure browser-source widgets, alert boxes, goals, supporter leaderboards, spoken welcomes/TTS, captions and interaction overlays already present in the repository.

### Wider platform

The blueprint connects Shared Sky to the Asset Vault, Creation Studios, Video/Cinema post-production, Social Manager, Scene Forge, Game Forge, Creator Network and owner governance rather than creating duplicate stores for each area.

## What still requires external infrastructure or provider approval

Code cannot manufacture third-party permission. The following remain deployment/integration work rather than fake placeholders:

1. registering ESP developer applications with supported platforms;
2. obtaining required OAuth/app-review scopes;
3. individual creator authorisation;
4. production ingest service (for example an ESP-operated RTMP/SRT/WHIP-capable media server);
5. dedicated relay worker pool and autoscaling;
6. WebRTC/SFU guest media service;
7. provider-specific unified chat adapters;
8. provider-specific live analytics adapters;
9. production recording/ISO media workers;
10. scheduled playout worker for pre-recorded/live playlists;
11. transcoding ladders and GPU worker profiles where passthrough is insufficient;
12. load, soak, failover and cross-browser/device qualification.

Those dependencies are intentionally exposed in runtime health so the UI cannot misrepresent them as complete.

## Performance strategy

Shared Sky is designed to be lighter locally than a heavy desktop-only studio by:

- uploading one contribution feed where possible;
- fanning out in the cloud;
- allowing passthrough remuxing when destination codecs match;
- keeping each destination relay independent;
- lazy-loading advanced UI panels;
- using local browser camera/screen preview only on demand;
- reserving native companion code for capture functions browsers cannot reliably provide;
- allowing future GPU workers to handle expensive filters/transcoding rather than forcing them onto the creator's laptop.

## Build progression

The implemented control plane is the foundation for the complete feature blueprint stored in Google Drive. Future PRs should extend this same model rather than create parallel streaming products. Priority order:

1. production ingest + relay worker service;
2. destination connector/OAuth framework;
3. browser/native contribution publisher;
4. WebRTC guests and green room;
5. unified chat adapters;
6. recording/ISO pipeline;
7. scheduler and pre-recorded playout;
8. analytics aggregation;
9. executable visual/audio effect graph;
10. plugin sandbox and marketplace distribution;
11. end-to-end production acceptance and load/failover testing.
