# Chat 2 Shared Sky Transport Integration Contract

Status: Chat 2 transport control-plane implementation for `development/full-site-build`.

## Scope boundary

This contract owns durable broadcast transport state, programme-source handoff, preflight,
independent destination runs, idempotent start/stop/retry, internal playback descriptors,
normalized transport health events, recording/replay handoff metadata, destination presets,
transport capacity evidence, stale-session recovery, and destination adapter capability truth.

It does not own studio composition/mixer UI, Live Now discovery/player UI, Gifts,
Battles/scoring, editor UX, global infrastructure hardening, or final release acceptance.

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

The existing Shared Sky studio domain remains:

```python
from aura_music_studio.shared_sky_streaming_studios import shared_sky
```

Chat 2 composes that store rather than creating a competing user/auth/project/destination
system.

## Durable state

Chat 2 adds these SQLite tables to the canonical `LSS_DB_PATH` database:

- `shared_sky_programme_sources`
- `shared_sky_transport_sessions`
- `shared_sky_destination_runs`
- `shared_sky_transport_idempotency`
- `shared_sky_transport_events`
- `shared_sky_transport_rate_limits`
- `shared_sky_recordings`
- `shared_sky_destination_presets`
- `shared_sky_highlight_markers`

`shared_sky_transport_sessions.version` is an optimistic-concurrency version. Sessions
persist contribution-ingest identity, programme/rendition references, correlation/trace IDs,
start/end timestamps, validation evidence, health state and terminal/recovery reason codes.
Consequential transitions update using the observed version and fail on concurrent mutation.

## Broadcast lifecycle

Canonical states:

`draft -> configuring -> validating -> ready -> starting -> live`

Recovery/degradation:

- `degraded`
- `reconnecting`
- `stopping`

Terminal:

- `ended`
- `failed`
- `cancelled`

Each external destination has its own lifecycle. A failed/unavailable destination does not
terminate another healthy destination or healthy internal Shared Sky playback. When at least
one delivery path is usable, destination-local failures are non-fatal preflight warnings and
the aggregate session can start `degraded`.

## Programme-source contract — Chats 3, 6, 7 and 8

Register an upstream programme source:

`POST /shared-sky/api/programme-sources`

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

The source ID is the stable tenant/project-scoped handoff ID.

Configure transport:

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

Blocking errors are server-authoritative. Source ownership/readiness, conflicting active
session, recording storage requirements and all-delivery-path-unavailable conditions remain
fatal. Destination-local inability is demoted to a warning only when another independent
internal/external delivery path is actually ready.

Representative response:

```json
{
  "ready": true,
  "blocking_errors": [],
  "warnings": [
    {
      "code": "youtube_live_scope_missing",
      "scope": "destination",
      "destination_id": "dest_...",
      "non_fatal_delivery_path_failure": true
    }
  ],
  "internal_playback": {
    "capability_state": "ready",
    "reason_code": "ready"
  },
  "correlation_id": "corr_...",
  "trace_id": "trace_..."
}
```

## Idempotent go-live operations — Chat 3

Start:

`POST /shared-sky/api/broadcasts/{broadcast_id}/transport/start`

Stop:

`POST /shared-sky/api/broadcasts/{broadcast_id}/transport/stop`

Both require:

`Idempotency-Key: <unique-operation-key>`

The key is reserved durably before execution. A concurrent duplicate gets HTTP 409; a
completed duplicate gets the stored response. Reusing a key with a different request is
rejected.

Stop order is deterministic by destination ID: media relay is stopped first, then the provider
resource is closed. A provider-close failure is recorded as a warning and does not prevent
other destination cleanup or authoritative terminal state.

Retry one destination:

`POST /shared-sky/api/broadcasts/{broadcast_id}/destinations/{destination_id}/retry`

Retry is bounded by `SHARED_SKY_DESTINATION_MAX_RETRIES` (default 5), with exponential
backoff capped at 300 seconds.

## Destination provider resource recovery

Provider external broadcast/stream IDs are persisted before FFmpeg relay startup. If the
provider publish succeeds but local relay startup fails, a retry receives the existing provider
IDs. The YouTube adapter re-queries the existing `liveStream` ingest information and reuses
that resource rather than creating duplicate remote broadcasts/streams.

## Destination presets — Chat 3

Create/upsert a tenant-scoped preset:

`POST /shared-sky/api/destination-presets`

```json
{
  "name": "Main Launch",
  "destination_ids": ["dest_a", "dest_b"]
}
```

List:

`GET /shared-sky/api/destination-presets`

Apply to a non-active broadcast:

`POST /shared-sky/api/broadcasts/{broadcast_id}/destination-presets/{preset_id}/apply`

Applying a preset to an active `starting/live/degraded/reconnecting/stopping` transport is
rejected.

## Transport status and diagnostics — Chats 3 and 10

`GET /shared-sky/api/broadcasts/{broadcast_id}/transport`

The response contains durable aggregate state/version, correlation/trace IDs, independent
destination runs, normalized health data, recording handoffs, recent events, playback
capability/descriptor and relay runtime health.

Health-event write endpoint:

`POST /shared-sky/api/broadcasts/{broadcast_id}/transport/health`

Normalized measurable keys include:

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

Unknown metrics are not persisted. The contract does not invent CPU/GPU/network/media values.

### Capacity evidence

Member-scoped:

`GET /shared-sky/api/transport/capacity`

Owner/global:

`GET /owner/shared-sky/api/transport/capacity`

Snapshot fields include active broadcasts, live/reconnecting/failed destination runs, maximum
active fan-out, active recordings, recent destination-failure event count, measured queue/buffer
peaks, FFmpeg active outputs/runtime mode, and Chat 10 declared media-node capacity when its
canonical table exists. `cpu_gpu_values_fabricated` is always false; absence of measurement is
reported as absence rather than synthetic telemetry.

## Internal playback — Chat 4 / origin service

`GET /shared-sky/api/broadcasts/{broadcast_id}/playback`

When `SHARED_SKY_PLAYBACK_BASE_URL` and `SHARED_SKY_PLAYBACK_SIGNING_SECRET` exist, the
response contains a short-lived LL-HLS-style manifest descriptor and separate Bearer token.
The token is never embedded into the manifest query string.

Origin/CDN authorization can reuse the exact verifier:

```python
from aura_music_studio.shared_sky_transport_domain import transport

claims = transport.verify_playback_token(
    token,
    expected_broadcast_id=broadcast_id,
    expected_user_id=user_id,
)
```

The verifier uses HMAC constant-time comparison, validates expiry and binds the token to the
expected broadcast/member. This is the authorization contract; a deployed LL-HLS origin/CDN
remains an infrastructure prerequisite rather than being claimed by Chat 2.

## Destination adapter contract

```python
class DestinationAdapter(Protocol):
    provider_id: str
    def capability(...): ...
    def prepare(...): ...
    def stop(...): ...
```

### Custom RTMP/RTMPS/SRT/RIST

Creator-supplied lawful endpoints use the existing encrypted Shared Sky vault. Validation
requires approved schemes, hostname, no embedded user/password and no localhost/private/
link-local/reserved IP. DNS resolution is checked before relay use. Credentials are appended
only server-side and never returned by the API.

`POST /shared-sky/api/destinations/validate` performs the same server validation boundary.

### YouTube Live Streaming API

The adapter uses the repository's encrypted `SocialOAuthVault`; it does not create a second
OAuth store. It requires destination metadata `oauth_credential_id`, one of:

- `https://www.googleapis.com/auth/youtube`
- `https://www.googleapis.com/auth/youtube.force-ssl`

and deployment gate `SHARED_SKY_YOUTUBE_LIVE_ENABLED=1` after app verification/account
eligibility have been validated.

When ready it uses official YouTube `liveBroadcast`, `liveStream`, bind and stream-query
operations. Current repository-wide social OAuth requests upload/read scopes, so existing
accounts truthfully report `scope_insufficient` until reauthorized through the canonical OAuth
layer.

### Other providers

Other providers remain capability-only until their official APIs/ingest access and app/account
eligibility are implemented and verified. They return explicit `approval_pending`,
`credentials_missing`, `account_ineligible`, `scope_insufficient` or `unsupported` states and
never simulate LIVE success.

Capability matrix:

`GET /shared-sky/api/destination-capabilities`

## Ingest compatibility — Chat 10

Chat 2 deliberately does not duplicate Chat 10's signed-ingest media-plane PR. Browser or
external-encoder sources are fail-closed unless a canonical `shared_sky_ingest_sessions` row is
present, tenant/broadcast bound, issued, unrevoked and unexpired. An arbitrary client-supplied
ID is not accepted as proof.

Until Chat 10's canonical module lands, preflight emits the explicit compatibility blocker/
warning instead of inventing media termination.

External relay still requires `SHARED_SKY_INGEST_BASE_URL` when external destinations are used.

## Recording and replay handoff — Chats 3, 4, 7 and 8

Request recording metadata:

`POST /shared-sky/api/broadcasts/{broadcast_id}/recordings/{kind}`

Kinds:

- `programme`
- `clean_feed`
- `isolated_source`
- `audio_tracks`

Finalize:

`PUT /shared-sky/api/broadcasts/{broadcast_id}/recordings/{kind}`

Stores asset ID, checksum, size, duration and status references; editors receive references
rather than copied opaque blobs. Client-facing storage paths are masked.

Create replay/highlight/chapter/clip markers:

`POST /shared-sky/api/broadcasts/{broadcast_id}/markers`

```json
{
  "offset_ms": 15000,
  "label": "Big moment",
  "marker_type": "highlight"
}
```

List ordered markers:

`GET /shared-sky/api/broadcasts/{broadcast_id}/markers`

Supported marker types: `highlight`, `chapter`, `clip`, `replay`. These timestamp references
are the handoff for recording/replay/highlight processors; Chat 2 does not implement editing.

## Stale-session recovery — Owner / Chat 10

Owner-only recovery action:

`POST /owner/shared-sky/api/transport/cleanup-stale`

```json
{"stale_after_seconds": 300}
```

Only stale transitional `starting` and `stopping` sessions are touched. Persisted relay output
IDs are torn down; stale starts become `failed` with `stale_start_cleanup`, stale stops become
`ended` with `stale_stop_cleanup`. Healthy live/degraded/reconnecting sessions are never swept
by this action.

This is a domain recovery hook. Chat 10 may call/schedule it from hardened infrastructure.

## Existing scheduled LIVE contract — Chat 3 / Chat 10

Chat 2 reuses `SharedSkyWorker` with database leases/retry counts. It does not add a second
scheduler.

Existing owner routes:

- `GET /owner/shared-sky/api/scheduler/status`
- `POST /owner/shared-sky/api/scheduler/run-due`

Pre-recorded playout remains fail-closed until a dedicated playout worker exists.

## Chat 5 handoff

Gift transactions reference the authoritative Shared Sky broadcast/live identity and creator
recipient identity only. Chat 2 exposes lifecycle/start/end state; no Coin arithmetic is here.

## Chat 6 handoff

Battles use the stable broadcast ID and `battle_program` source registration. Chat 2 owns only
media transport state.

## Chat 9 handoff

Use destination capability/status and broadcast history references. Do not duplicate OAuth or
role systems.

## Chat 10 handoff

Consume:

- `shared_sky_transport_events`
- `shared_sky_destination_runs`
- `shared_sky_transport_sessions`
- `GET /owner/shared-sky/api/transport/capacity`
- `POST /owner/shared-sky/api/transport/cleanup-stale`
- relay health from `shared_sky_relay`
- correlation/trace IDs

Chat 10 may replace process-local FFmpeg supervision with a hardened worker/service without
changing these domain contracts.

## Chat 11 acceptance handoff

Release acceptance should verify:

1. exact integration ancestry;
2. schema initialization on existing databases;
3. concurrent start/stop/retry idempotency;
4. partial-LIVE behavior when one delivery path is unavailable;
5. provider-resource reuse after relay startup failure;
6. deterministic media-before-provider shutdown;
7. stale transition cleanup never sweeps healthy LIVE sessions;
8. IPv4/IPv6/private/link-local/reserved/DNS SSRF cases;
9. credentials never appear in API/log fixtures;
10. playback Bearer token signature/expiry/broadcast/member binding;
11. internal playback remains capability-blocked without a real origin;
12. scheduled worker remains fail-closed for pre-recorded playout;
13. Chat 10 signed-ingest PR reconciles without duplicate schemas/routes;
14. production media-plane/origin/relay capacity evidence exists before production-scale claims.

Acceptance must distinguish coding completeness from external capability. A green Chat 2
control plane does not turn an unapproved provider, undeployed CDN/origin, absent SFU, or
unconfigured media termination service into an available production feature. Those remain
explicit capability blockers for Chat 10/11 and deployment operations.

## Environment/configuration points

- `LSS_DB_PATH`
- `SHARED_SKY_VAULT_SECRET`
- `SHARED_SKY_RELAY_ENABLED`
- `SHARED_SKY_FFMPEG_BIN`
- `SHARED_SKY_INGEST_BASE_URL`
- `SHARED_SKY_DESTINATION_MAX_RETRIES`
- `SHARED_SKY_PLAYBACK_BASE_URL`
- `SHARED_SKY_PLAYBACK_SIGNING_SECRET`
- `SHARED_SKY_ALLOW_INSECURE_PLAYBACK` — development only
- `SHARED_SKY_RECORDING_STORAGE_URI`
- `SHARED_SKY_YOUTUBE_LIVE_ENABLED`
- canonical social OAuth variables owned by `esp_social_oauth.py`

No secret belongs in source control, browser logs, analytics, fixtures or API error bodies.

## Truth boundary

This implementation is a production-facing transport control plane and adapter layer. It does
not claim a deployed SFU, RTMP/SRT termination cluster, LL-HLS origin/CDN, dedicated transcoder
fleet, recording writer, or external provider approval merely because contracts exist. Those
remain deployment/provider capability gates and are surfaced truthfully.

## Official provider references verified for this implementation

Verified against current official documentation on 5 September 2026:

- YouTube `liveBroadcasts.insert`: https://developers.google.com/youtube/v3/live/docs/liveBroadcasts/insert
- YouTube broadcast/stream implementation guide: https://developers.google.com/youtube/v3/live/guides/implementation/broadcasts-and-streams
- YouTube Live API errors/eligibility: https://developers.google.com/youtube/v3/live/docs/errors
- TikTok Developer Guidelines/app review: https://developers.tiktok.com/doc/our-guidelines-developer-guidelines

Provider-specific behaviour must be rechecked before future endpoint/scope changes.

Final Chat 2 merge acceptance is evidence-based: only exact-head Command Center CI, Security
Gates and Self-Host Smoke results on the current PR head qualify. Earlier cancelled/superseded
runs are not acceptance evidence.
