# Chat 2 Shared Sky First-Party Media Runtime — Wave 2

This document extends the canonical `Chat 2 Shared Sky Transport Integration Contract` with
the concrete first-party HLS and local recording runtime.

## Why this wave exists

The transport control plane already issued signed playback descriptors, but a configured
playback URL was not itself proof that a media origin existed. Wave 2 removes that false-positive
readiness state for the first-party Shared Sky path.

Internal playback is `ready` only when all of these are true:

- `SHARED_SKY_PLAYBACK_BASE_URL` is configured;
- `SHARED_SKY_PLAYBACK_SIGNING_SECRET` is configured;
- HTTPS is used outside the explicitly insecure development mode;
- `SHARED_SKY_INTERNAL_MEDIA_ENABLED=1`;
- `SHARED_SKY_INTERNAL_MEDIA_ROOT` is configured;
- FFmpeg is available;
- contribution ingest is configured through `SHARED_SKY_INGEST_BASE_URL`.

If those prerequisites are absent, preflight returns a truthful capability blocker. It never
represents a URL string as a running origin.

## Runtime modules

- `aura_music_studio/shared_sky_internal_media.py`
  - process supervisor for first-party HLS and local programme recording;
  - safe output-root confinement;
  - FFmpeg/FFprobe capability probing;
  - adaptive rendition process creation;
  - recording checksum/size/duration evidence.
- `aura_music_studio/shared_sky_transport_media.py`
  - starts and reconciles media jobs inside the broadcast lifecycle;
  - makes actual media-job launch authoritative for internal playback success.
- `aura_music_studio/shared_sky_transport_media_lifecycle.py`
  - stops internal playback/recording inside the durable idempotent stop boundary;
  - coordinates stale-session media cleanup.
- `aura_music_studio/shared_sky_transport_local_recording.py`
  - allows a configured first-party local recording root to satisfy recording preflight;
  - avoids requiring duplicate storage configuration.
- `aura_music_studio/shared_sky_internal_media_api.py`
  - signed HLS asset origin route;
  - owner media-runtime health diagnostics.

## First-party HLS packaging

The self-host runtime creates independent H.264/AAC HLS rendition jobs from the single Shared
Sky contribution input. Default renditions are 720p and 480p. Supported bounded presets are:

- 1080p — target video bitrate 4.5 Mbps;
- 720p — 2.5 Mbps;
- 480p — 1.2 Mbps;
- 360p — 700 kbps.

A profile can request at most three renditions in this web-process runtime. FFmpeg produces
short HLS segments with independent-segment flags, a bounded sliding window and programme date
time. The master playlist is written atomically.

This is an actual first-party self-host media runtime. It is not represented as a distributed
transcoder fleet and does not claim automatic cluster failover. Chat 10 may replace the process
supervisor with dedicated workers while preserving the same durable transport/media-job
contracts.

## Signed origin contract

Playback assets are served at:

`GET /shared-sky/media/{broadcast_id}/{asset_path}`

The request requires:

`Authorization: Bearer <short-lived Shared Sky playback token>`

The token is verified for signature, expiry and broadcast binding. Playback assets are confined
to the configured broadcast media root. Only HLS playlist/segment extensions are accepted.
Directory traversal and arbitrary file reads are rejected.

For this built-in origin, configure:

`SHARED_SKY_PLAYBACK_BASE_URL=https://<public-host>/shared-sky/media`

The public reverse proxy/TLS layer remains deployment infrastructure; the application does not
manufacture HTTPS or CDN availability.

## Local programme recording

When both of these are configured:

- `SHARED_SKY_INTERNAL_MEDIA_ENABLED=1`
- `SHARED_SKY_RECORDING_LOCAL_ROOT=<private server path>`

the first-party local recorder can satisfy the recording storage preflight without also
requiring `SHARED_SKY_RECORDING_STORAGE_URI`.

Programme recording is written as Matroska (`.mkv`) using stream copy where possible, which is
more resilient to interrupted finalisation than MP4. When stopped, the runtime records:

- SHA-256 checksum;
- byte size;
- duration when FFprobe can measure it;
- terminal recording state;
- asset reference/provenance relation to the broadcast.

The physical storage path is never returned in the public recording payload.

Object-storage recording continues to use the existing storage-handoff contract. This wave does
not pretend local disk is a distributed object store.

## Durable internal media jobs

Table:

`shared_sky_internal_media_jobs`

Tracks:

- job ID;
- broadcast/user ownership;
- job kind (`hls`, `recording:<kind>`);
- rendition;
- durable state;
- PID evidence;
- worker mode;
- private output path;
- reason code;
- timestamps.

A process exit is reconciled into transport state. If internal playback disappears while an
external destination remains healthy, the broadcast becomes `degraded`; if no delivery path
remains, it becomes `failed`.

After a web-process restart, persisted jobs whose process is no longer owned by the current
supervisor are represented as `orphaned`, not falsely reported as running. Dedicated worker
recovery/failover remains a Chat 10 infrastructure responsibility.

## Owner diagnostics

`GET /owner/shared-sky/api/internal-media/status`

Returns only safe runtime evidence:

- enabled/configured flags;
- FFmpeg/FFprobe availability;
- whether local recording root is configured;
- active process count;
- runtime mode.

It does not expose the media root and explicitly does not claim cluster failover.

## Environment variables

- `SHARED_SKY_INTERNAL_MEDIA_ENABLED` — default off;
- `SHARED_SKY_INTERNAL_MEDIA_ROOT` — private HLS working/output root;
- `SHARED_SKY_RECORDING_LOCAL_ROOT` — optional private local recording root;
- `SHARED_SKY_FFMPEG_BIN` — FFmpeg executable, default `ffmpeg`;
- `SHARED_SKY_FFPROBE_BIN` — FFprobe executable, default `ffprobe`;
- `SHARED_SKY_HLS_SEGMENT_SECONDS` — bounded 1–6 seconds, default 2;
- `SHARED_SKY_HLS_WINDOW_SEGMENTS` — bounded 3–30, default 6;
- existing `SHARED_SKY_PLAYBACK_BASE_URL`;
- existing `SHARED_SKY_PLAYBACK_SIGNING_SECRET`;
- existing `SHARED_SKY_INGEST_BASE_URL`.

## Security boundary

- no `shell=True` process invocation;
- no secret-bearing media URLs are logged or returned by diagnostics;
- broadcast IDs are constrained before filesystem use;
- resolved playback and recording paths are confined under configured roots;
- playback tokens are short-lived and bearer-authenticated;
- arbitrary file extensions are rejected by the origin route;
- media/runtime absence fails closed.

## Acceptance

Wave 2 is mergeable only after exact-head:

1. production source completeness audit;
2. Python compile;
3. full repository pytest suite;
4. self-host topology and production-route smoke;
5. committed-secret scan and Aura Sec trust gates;
6. merge-tree ancestry validation against the current `development/full-site-build` head.

Final production deployment remains Chat 11's responsibility.
