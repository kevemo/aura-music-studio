# Chat 2 Shared Sky Transport Integration Contract

Status: Chat 2 transport control-plane implementation for `development/full-site-build`.

## Scope boundary

This contract owns durable broadcast transport state, programme-source handoff, preflight, independent destination runs, idempotent start/stop/retry, internal playback descriptors, normalized transport health events, recording handoff metadata, and destination adapter capability truth.

It does not own studio composition/mixer UI, Live Now discovery/player UI, Gifts, Battles/scoring, editor UX, global infrastructure hardening, or final release acceptance.

## Canonical imports

```python
from aura_music_studio.shared_sky_transport_domain import (
    BroadcastState,
    DestinationState,
    SharedSkyTransportStore,
    transport,
)
from aura_music_studio.shared_sky_destination_adapters import CapabilityState
from aura_music_studio.shared_sky_transport_api import router as shared_sky_transport_router
```

The existing Shared Sky studio domain remains in:

```python
from aura_music_studio.shared_sky_streaming_studios import shared_sky
```

Chat 2 composes that existing store rather than creating a competing user/auth/project/destination system.

## Durable state

Chat 2 adds these SQLite tables to the repository's canonical `LSS_DB_PATH` database:

- `shared_sky_programme_sources`
- `shared_sky_transport_sessions`
- `shared_sky_destination_runs`
- `shared_sky_transport_idempotency`
- `shared_sky_transport_events`
- `shared_sky_transport_rate_limits`
- `shared_sky_recordings`

`shared_sky_transport_sessions.version` is an optimistic-concurrency version. The session also persists contribution ingest identity, programme/rendition references, correlation/trace IDs, start/end timestamps, validation evidence and terminal/recovery reason codes. Consequential transitions update using the observed version and fail on concurrent mutation.

## Broadcast lifecycle

Canonical Chat 2 states:

`draft -> configuring -> validating -> ready -> starting -> live`

Recovery/degradation states:

- `degraded`
- `reconnecting`
- `stopping`

Terminal states:

- `ended`
- `failed`
- `cancelled`

An external destination has its own lifecycle. A failed destination does not make healthy destinations fail automatically. The aggregate session becomes `degraded` or `reconnecting` while a usable delivery path remains.

## Programme-source contract — Chats 3, 6, 7 and 8

Register an upstream programme source:

`POST /shared-sky/api/programme-sources`

Body:

```json
{
  "project_id": "shared-sky-project-id",
  "source_type": "studio_program",
  "source_ref": "studio://project/programme/main",
  "state": "ready",
  "capabilities": {
    "landscape": true,
    "portrait": true,
    "audio": true
  }
}
```

Allowed source types:

- `studio_program` — Chat 3 programme output
- `browser` — browser contribution contract
- `external_encoder` — RTMP/RTMPS/SRT-style encoder contribution
- `music_project` — Chat 7
- `video_project` — Chat 8
- `game_project` — Chat 8
- `battle_program` — Chat 6 participant/composite transport hook

The response source ID is the stable handoff ID. The source is tenant/project scoped.

Configure a broadcast to use the source:

`PUT /shared-sky/api/broadcasts/{broadcast_id}/transport`

```json
{
  "source_id": "src_...",
  "internal_playback": true,
  "rendition_profile": {
    "landscape": "1080p30",
    "portrait": "1080x1920p30"
  },
  "recording_enabled": true,
  "ingest_session_id": null
}
```

## Preflight — Chat 3

`GET /shared-sky/api/broadcasts/{broadcast_id}/transport/preflight`

Response shape:

```json
{
  "ready": false,
  "blocking_errors": [
    {
      "code": "internal_playback_unconfigured",
      "scope": "internal_playback",
      "message": "Internal playback origin/signing is not configured"
    }
  ],
  "warnings": [],
  "destinations": [],
  "internal_playback": {
    "capability_state": "credentials_missing",
    "reason_code": "internal_playback_unconfigured"
  },
  "correlation_id": "corr_...",
  "trace_id": "trace_..."
}
```

Blocking errors are server-authoritative. The client cannot override them.

## Idempotent go-live operations — Chat 3

Start:

`POST /shared-sky/api/broadcasts/{broadcast_id}/transport/start`

Stop:

`POST /shared-sky/api/broadcasts/{broadcast_id}/transport/stop`

Both require:

`Idempotency-Key: <unique-operation-key>`

The key is reserved in the database before execution. Sensitive operations are also tenant-scoped through a durable SQLite fixed-window limiter (start 10/minute, stop/retry 20/minute, destination validation 30/minute, health events 240/minute). A concurrent duplicate sees HTTP 409 rather than initiating a second provider publish/relay. Reusing a completed key returns the stored response. Reusing the same key with a different request is rejected.

Retry one destination:

`POST /shared-sky/api/broadcasts/{broadcast_id}/destinations/{destination_id}/retry`

This also requires `Idempotency-Key`. Retry is bounded by `SHARED_SKY_DESTINATION_MAX_RETRIES` (default 5) with exponential backoff capped at 300 seconds.

## Transport status and diagnostics — Chats 3 and 10

`GET /shared-sky/api/broadcasts/{broadcast_id}/transport`

The response contains:

- durable aggregate session state/version
- correlation ID and trace ID
- independent destination runs
- normalized health payloads
- recording handoffs
- recent transport events
- internal playback capability/descriptor
- relay runtime health

Normalized measurable metric keys accepted by the event contract include:

- `input_bitrate_kbps`
- `output_bitrate_kbps`
- `frame_rate`
- `dropped_frames`
- `late_frames`
- `processing_lag_ms`
- `audio_present`
- `packet_loss_percent`
- `jitter_ms`
- `buffer_ms`
- `queue_depth`
- `reconnect_count`
- `end_to_end_latency_ms`
- `region`
- `relay_id`

Unknown metrics are not persisted into the normalized metric payload. The contract does not invent CPU, GPU, packet-loss, jitter, bitrate, or latency values.

Health-event write endpoint:

`POST /shared-sky/api/broadcasts/{broadcast_id}/transport/health`

## Internal playback — Chat 4

`GET /shared-sky/api/broadcasts/{broadcast_id}/playback`

When the deployment has both `SHARED_SKY_PLAYBACK_BASE_URL` and `SHARED_SKY_PLAYBACK_SIGNING_SECRET`, the response contains:

```json
{
  "capability_state": "ready",
  "mode": "ll-hls",
  "manifest_url": "https://origin/.../{broadcast_id}/master.m3u8",
  "authorization": {
    "scheme": "Bearer",
    "token": "short-lived-signed-token",
    "expires_at": "..."
  },
  "broadcast_id": "...",
  "state": "live"
}
```

The signed token is not embedded in the manifest query string. If the origin/signing deployment is absent, the API returns a truthful `credentials_missing`/runtime capability state instead of a fake URL.

Chat 4 should use `session.state` plus transport events for stream-start/end state. Replay/post-production should use recording asset IDs, not opaque blob copies.

## Destination adapter contract

Adapter boundary:

```python
class DestinationAdapter(Protocol):
    provider_id: str
    def capability(...): ...
    def prepare(...): ...
    def stop(...): ...
```

Implemented adapter modes:

### Custom RTMP/RTMPS/SRT/RIST

Creator-supplied lawful endpoints use the existing encrypted Shared Sky vault. Before first relay use Chat 2 validates:

- approved schemes only
- hostname presence
- no user/password embedded in the URL
- no localhost/private/link-local/reserved destination IP
- DNS resolution does not resolve to unsafe internal addresses

Credentials are appended only server-side after vault decryption and are never returned by the API.

### YouTube Live Streaming API

The adapter uses the repository's encrypted `SocialOAuthVault`; it does not create a second OAuth/token store. It requires an OAuth credential linked in destination metadata under `oauth_credential_id`, and one of the official YouTube Live scopes:

- `https://www.googleapis.com/auth/youtube`
- `https://www.googleapis.com/auth/youtube.force-ssl`

Provider publish is additionally gated by `SHARED_SKY_YOUTUBE_LIVE_ENABLED=1`, which should only be enabled after app verification/account eligibility are validated.

When ready, the adapter uses official YouTube Live Streaming API operations to create a `liveBroadcast`, create a `liveStream`, bind them, obtain provider ingest information, and relay the Shared Sky programme to that ingest. Provider IDs are persisted in the destination run.

Current repository-wide YouTube social OAuth asks for upload/read scopes rather than a Live Streaming write scope, so existing accounts can truthfully report `scope_insufficient` until reauthorized through the canonical OAuth layer. The adapter follows the official create-broadcast → create-stream → bind flow and leaves account eligibility/provider approval as an evidence gate.

### Other providers

Other provider entries remain capability-only adapters until their official API/ingest access and app/account eligibility are implemented and verified. They return explicit states such as `approval_pending`, `credentials_missing`, `account_ineligible`, or `unsupported`; they do not simulate live connection success.

A creator-supplied permitted RTMP/RTMPS/SRT endpoint may still be transported through the custom endpoint adapter when the creator legitimately has that endpoint/key.

Capability matrix endpoint:

`GET /shared-sky/api/destination-capabilities`

## Ingest compatibility — Chat 10

Chat 2 deliberately does not duplicate Chat 10's open signed-ingest media-plane PR. For `browser` or `external_encoder` sources, it dynamically detects the canonical `shared_sky_media_plane` compatibility module when present. Until that PR lands, preflight emits `signed_ingest_contract_pending_merge`. Browser/external-encoder sources are fail-closed unless a canonical signed ingest session can be verified from `shared_sky_ingest_sessions`; an arbitrary client-supplied ingest ID is not accepted as proof.

External relay still requires `SHARED_SKY_INGEST_BASE_URL` when external destinations are selected.

Chat 10 media-node/ingest credentials remain the canonical ingest-security boundary after merge.

## Destination failure isolation

`shared_sky_destination_runs` has one row per broadcast/destination. Important fields:

- `state`
- `capability_state`
- `provider_external_id`
- `provider_stream_id`
- `output_id`
- `retry_count`
- `next_retry_at`
- `last_error_code`
- redacted/safe `last_error_safe`
- profile and health JSON
- start/end timestamps

A failure is normalized to a reason code and, when retryable, becomes `reconnecting` with bounded backoff. Healthy output processes remain untouched.

## Recording handoff — Chats 3, 4, 7 and 8

Request a recording transport asset:

`POST /shared-sky/api/broadcasts/{broadcast_id}/recordings/{kind}`

Kinds:

- `programme`
- `clean_feed`
- `isolated_source`
- `audio_tracks`

Requires `SHARED_SKY_RECORDING_STORAGE_URI`.

Finalize/handoff metadata:

`PUT /shared-sky/api/broadcasts/{broadcast_id}/recordings/{kind}`

```json
{
  "state": "complete",
  "asset_id": "asset_...",
  "checksum_sha256": "<64 hex chars>",
  "size_bytes": 123456789,
  "duration_ms": 3600000
}
```

Chat 2 stores provenance/status/checksum/size/duration references. It does not copy the media blob into editors. Storage paths are masked in client-facing status.

## Existing scheduled LIVE contract — Chat 3 / Chat 10

Chat 2 reuses the existing `SharedSkyWorker` durable scheduler with leases, retry count, and a fail-closed pre-recorded mode. Chat 2 does not add a competing scheduler.

Current routes already owned by the worker control layer:

- `GET /owner/shared-sky/api/scheduler/status`
- `POST /owner/shared-sky/api/scheduler/run-due`

Pre-recorded playout remains a truthful runtime blocker until a dedicated playout worker exists.

## Chat 5 handoff

Gift transactions should reference the authoritative Shared Sky LIVE/broadcast identity plus creator recipient identity only. Chat 2 provides lifecycle/start/end state; no Gift ledger or Coin arithmetic is implemented here.

## Chat 6 handoff

Battles use the stable broadcast ID plus `battle_program` source registration. Chat 2 transports the programme/media state only; scoring, teams and battle logic remain Chat 6.

## Chat 9 handoff

Use destination capability/status and broadcast history references. Do not duplicate OAuth/user-role systems. The YouTube transport adapter resolves credentials through the canonical encrypted Social OAuth vault.

## Chat 10 handoff

Consume:

- `shared_sky_transport_events`
- `shared_sky_destination_runs`
- relay health from `shared_sky_relay`
- correlation/trace IDs from `shared_sky_transport_sessions`
- `signed_ingest_contract_pending_merge` compatibility warning until the media-plane PR lands

Chat 10 should replace the current web-process FFmpeg supervisor with hardened worker/service orchestration without changing these Chat 2 domain contracts.

## Chat 11 acceptance handoff

Release acceptance should verify:

1. exact integration-branch ancestry;
2. migrations initialize on an existing database;
3. start/stop/retry idempotency under concurrent requests;
4. SSRF cases for IPv4/IPv6/private/link-local/reserved/DNS resolution; DNS rebinding remains a deployment-layer hardening item for Chat 10;
5. provider credentials never appear in API/log fixtures;
6. YouTube official API tests remain mocked in ordinary CI and never contact creator accounts;
7. internal playback is capability-blocked until a real origin is configured;
8. scheduled worker remains fail-closed for pre-recorded playout;
9. Chat 10 signed-ingest PR is reconciled without duplicate schemas/routes;
10. production media-plane/origin/relay capacity evidence is supplied before claiming production scale.

## Environment/configuration points

- `LSS_DB_PATH`
- `SHARED_SKY_VAULT_SECRET`
- `SHARED_SKY_RELAY_ENABLED`
- `SHARED_SKY_FFMPEG_BIN`
- `SHARED_SKY_INGEST_BASE_URL`
- `SHARED_SKY_DESTINATION_MAX_RETRIES`
- `SHARED_SKY_PLAYBACK_BASE_URL`
- `SHARED_SKY_PLAYBACK_SIGNING_SECRET`
- `SHARED_SKY_ALLOW_INSECURE_PLAYBACK` (development only)
- `SHARED_SKY_RECORDING_STORAGE_URI`
- `SHARED_SKY_YOUTUBE_LIVE_ENABLED`
- canonical social OAuth variables already owned by `esp_social_oauth.py`

No secret values belong in source control, browser logs, analytics, fixtures, or API error bodies.

## Truth boundary

This Chat 2 implementation is a production-facing control plane and adapter layer. It does not claim the repository currently has a deployed SFU, RTMP/SRT termination cluster, LL-HLS origin/CDN, dedicated transcoder fleet, recording writer, or provider approvals merely because contracts/configuration exist. Those remain runtime/deployment/provider gates and are surfaced as capability blockers rather than fake success.


## Official provider references verified for this implementation

Verified against current official documentation on 5 September 2026:

- YouTube `liveBroadcasts.insert`: https://developers.google.com/youtube/v3/live/docs/liveBroadcasts/insert
- YouTube broadcast/stream implementation guide: https://developers.google.com/youtube/v3/live/guides/implementation/broadcasts-and-streams
- YouTube Live API errors/eligibility: https://developers.google.com/youtube/v3/live/docs/errors
- TikTok Developer Guidelines/app review: https://developers.tiktok.com/doc/our-guidelines-developer-guidelines

Provider-specific behaviour must be rechecked before future endpoint/scope changes.
