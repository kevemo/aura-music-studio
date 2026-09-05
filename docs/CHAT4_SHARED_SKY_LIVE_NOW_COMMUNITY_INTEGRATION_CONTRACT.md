# Chat 4 Shared Sky Live Now & Community Integration Contract

Status: Chat 4 integration contract. Target branch: `development/full-site-build`.

## Authority and ownership

Chat 4 owns first-party Shared Sky discovery, Watch, viewer presence, internal realtime community state, chat, reactions, share intent, polls, Q&A, follows/LIVE notification preference, viewer-facing moderation/report plumbing and replay/watch-history hand-off.

Chat 4 does **not** own transport/ingest/transcoding, professional studio production controls, Creation Coin or Gift financial truth, or Battle lifecycle/scoring. The canonical LIVE truth remains `shared_sky_broadcasts` in `aura_music_studio.shared_sky_streaming_studios.SharedSkyStore`.

The viewer directory is an index over canonical broadcasts. A `shared_sky_live_directory` row can never make a broadcast LIVE. Discovery always joins `shared_sky_broadcasts.state='live'`, and a playback adapter must also report the session playable before it is listed.

## Import paths

Primary implementation:

- `aura_music_studio.shared_sky_live_community`
  - `router`
  - `community`
  - `LiveCommunityStore`
  - `PlaybackAdapter`
  - `GiftDisplayAdapter`
  - `BattleDisplayAdapter`
  - `ExternalChatAdapter`
  - `register_playback_adapter`
  - `register_gift_display_adapter`
  - `register_battle_display_adapter`
  - `register_external_chat_adapter`
- `aura_music_studio.shared_sky_live_bootstrap.install_shared_sky_live_community`
- production bootstrap: `aura_music_studio.shared_sky_owner_ops`

Tests: `tests/test_shared_sky_live_community.py`.

## Public routes

HTML:

- `GET /live-now`
- `GET /watch/{broadcast_id}`

JSON/SSE:

- `GET /shared-sky/live/api/directory`
- `GET /shared-sky/live/api/watch/{broadcast_id}`
- `PATCH /shared-sky/live/api/watch/{broadcast_id}/metadata`
- `POST /shared-sky/live/api/watch/{broadcast_id}/presence`
- `POST /shared-sky/live/api/watch/{broadcast_id}/presence/heartbeat`
- `DELETE /shared-sky/live/api/watch/{broadcast_id}/presence/{presence_id}`
- `GET /shared-sky/live/api/watch/{broadcast_id}/events` — SSE
- `GET /shared-sky/live/api/watch/{broadcast_id}/chat`
- `POST /shared-sky/live/api/watch/{broadcast_id}/chat`
- `POST /shared-sky/live/api/watch/{broadcast_id}/reactions`
- `POST /shared-sky/live/api/watch/{broadcast_id}/share`
- `PUT /shared-sky/live/api/creators/{creator_user_id}/follow`
- `PUT /shared-sky/live/api/creators/{creator_user_id}/notifications`
- `POST /shared-sky/live/api/watch/{broadcast_id}/polls`
- `POST /shared-sky/live/api/polls/{poll_id}/vote`
- `POST /shared-sky/live/api/watch/{broadcast_id}/qa`
- `POST /shared-sky/live/api/watch/{broadcast_id}/qa/{question_id}`
- `POST /shared-sky/live/api/watch/{broadcast_id}/report`
- `POST /shared-sky/live/api/watch/{broadcast_id}/moderation`
- `GET /shared-sky/live/api/watch/{broadcast_id}/external-chat`
- `DELETE /shared-sky/live/api/watch-history`

`/live-now`, `/watch/*` and `/shared-sky/live/api/*` are public only at the membership middleware envelope so anonymous public viewing can function. Each state-changing handler independently performs canonical optional/required membership resolution. Global security and cross-site request middleware remain active.

## Authoritative LIVE directory

`LiveCommunityStore.reconcile()` reads `shared_sky_broadcasts WHERE state='live'`, creates missing metadata index rows and never starts/stops a transport session. Stale index rows are retained only for ended/replay references and are not discoverable.

Directory fields are derived from canonical broadcast + user identity plus Chat 4 metadata:

- broadcast ID, creator user ID, creator display name;
- title/description/project reference;
- category/tags/language;
- visibility/suitability;
- authoritative start time and derived duration;
- current presence count;
- thumbnail/caption declaration/scheduled event reference;
- playback readiness from Chat 2 adapter;
- follow state;
- deterministic rank score.

Initial ranking model is explicitly named `deterministic_follow_plus_freshness_v1`: followed creator + deterministic freshness, then current internal presence as a tie-breaker. It is not represented as ML.

## Viewer count definition

`viewer_count` means: **the number of current, unique, unexpired Shared Sky viewer presence leases for one canonical broadcast**.

Current compatibility semantics:

- lease: 45 seconds;
- heartbeat recommendation: 20 seconds;
- authenticated viewers deduplicate across tabs to `user:{user_id}` for a broadcast;
- anonymous viewers receive an opaque resume token and reconnect to the same lease when that token is retained;
- only a SHA-256 hash of the resume token is persisted;
- IP and user-agent values are retained only as hashes for abuse hooks;
- expired leases are removed during aggregation;
- no API accepts a client-supplied viewer total.

This is a current-presence metric, not total views, impressions or external platform viewers.

## Realtime event envelope

Persistence: `shared_sky_realtime_events`.

Fields:

- `seq` — server-assigned monotonic SQLite sequence for reconnect cursor;
- `event_id` — canonical unique event ID;
- `schema_version` — currently `1`;
- `broadcast_id`;
- `actor_user_id` where applicable;
- `event_type`;
- `occurred_at`;
- `correlation_id`;
- `idempotency_key` where applicable;
- `audience` (`room`, `moderators`, etc.);
- validated JSON payload;
- durable flag;
- moderation state.

Durable types include chat, polls, Q&A, moderation and LIVE-directory/end state. High-volume reaction/share intent events may be ephemeral and can be coalesced/dropped by a future distributed realtime adapter without losing durable community truth.

SSE endpoint accepts `after=<seq>` and emits SSE `id` equal to the persisted sequence. The Watch client reconnects using its latest cursor. The current SQLite polling transport is a deterministic compatibility implementation; Chat 10 may replace fan-out with Redis/pub-sub/WebSocket infrastructure without changing the event schema.

## Persistence / migration surface

The module uses idempotent `CREATE TABLE IF NOT EXISTS` startup migrations against the canonical application DB. Added tables:

- `shared_sky_live_directory`
- `shared_sky_follows`
- `shared_sky_blocks`
- `shared_sky_presence`
- `shared_sky_realtime_events`
- `shared_sky_chat_settings`
- `shared_sky_chat_messages`
- `shared_sky_live_moderators`
- `shared_sky_live_bans`
- `shared_sky_engagement_receipts`
- `shared_sky_reaction_totals`
- `shared_sky_polls`
- `shared_sky_poll_options`
- `shared_sky_poll_votes`
- `shared_sky_qa`
- `shared_sky_reports`
- `shared_sky_moderation_actions`
- `shared_sky_watch_history`
- `shared_sky_notification_emissions`
- `shared_sky_rate_limits`

No Coin ledger, Gift transaction, Battle participant/score, ingest manifest or external-provider token table is introduced by Chat 4.

## Chat and community semantics

Chat messages are signed-in, persisted with durable IDs, server ordered, idempotent per sender, history-pageable, reply-capable, removable/pinnable, slow-mode aware and followers/member mode aware. Control characters are removed; detected links are limited to HTTP/HTTPS; browser rendering uses text nodes (`textContent`) rather than HTML insertion. Moderation roles and identity are never supplied by the client.

Reaction types currently supported: `like`, `star`, `spark`, `applause`, `heart`, `wow`. Reactions use rate-limited idempotent receipts and aggregate counters. They do not calculate a Battle score.

Share analytics records the actual action (`copy_link`, `share_sheet_open`, `destination_open`) and always reports `external_post_confirmed=false` unless a future provider confirmation contract supplies stronger evidence.

Poll totals are derived from server-side vote rows. Q&A state transitions are creator/moderator-authoritative.

## Visibility and safety

Supported policy states: `public`, `unlisted`, `followers`, `members`, `restricted`.

- Public: discoverable/playable when canonical LIVE + playback-ready.
- Unlisted: direct URL only, never directory-discoverable.
- Followers: requires server-side follow relationship.
- Members: requires an active Command Center member identity.
- Restricted: currently fails closed for ordinary viewers until a canonical age/suitability assertion is supplied by Chat 1/9.

Creator blocks are enforced before direct Watch access and remove the blocked viewer's follow/presence relationship. Direct Watch APIs re-run access policy; hiding a discovery card is never the security boundary.

Moderation actions are permission checked and persisted in `shared_sky_moderation_actions` with correlation/idempotency identity. Reports receive immutable report/audit IDs and an optional bounded message evidence snapshot.

## Playback adapter — Chat 2

Contract:

```python
class PlaybackAdapter(Protocol):
    def descriptor(self, broadcast_id: str, viewer_user_id: str | None) -> dict: ...
    def replay(self, broadcast_id: str, viewer_user_id: str | None) -> dict: ...
```

Register with:

```python
register_playback_adapter(chat2_adapter)
```

Minimum descriptor keys consumed by Chat 4:

- `available: bool`
- `state: ready | degraded | ended | unavailable | failed`
- `manifest_url: str | None`
- `token_expires_at: str | None`
- `renditions: list`
- `captions: list`
- `dvr: bool`

Default adapter fails closed and Live Now shows no session without authoritative playback readiness. Chat 4 never builds/guesses a transport manifest.

Replay hand-off uses `PlaybackAdapter.replay`; no replay is assumed to exist.

## Gift display adapter — Chat 5

Contract:

```python
class GiftDisplayAdapter(Protocol):
    def state(self, broadcast_id: str, viewer_user_id: str | None) -> dict: ...
```

Register with `register_gift_display_adapter(chat5_adapter)`.

Chat 4 renders only returned display/eligibility/send-result state. There is intentionally no Coin balance mutation, debit, price calculation, creator liability, refund or payout implementation in this module.

## Battle display adapter — Chat 6

Contract:

```python
class BattleDisplayAdapter(Protocol):
    def state(self, broadcast_id: str, viewer_user_id: str | None) -> dict: ...
```

Register with `register_battle_display_adapter(chat6_adapter)`.

Chat 4 consumes participant/team/round/timer/score/result state only. It does not create Battles or calculate scores from reactions/Gifts.

If Chat 6 elects to consume eligible engagement, it should consume canonical reaction event references and perform scoring inside Chat 6.

## External chat adapter — Chat 2/provider boundary

Contract:

```python
class ExternalChatAdapter(Protocol):
    def capability(self, broadcast_id: str, platform_id: str, viewer_user_id: str | None) -> dict: ...
```

Default behaviour exposes capability truth only and returns zero fabricated messages. Where a creator has configured a lawful HTTP/HTTPS destination chat link, unsupported/unauthorised providers receive `open_destination_chat` as a hand-off. Read/reply/moderate must remain false unless an authorised provider adapter proves the capability.

## Chat 3 hand-off

Chat 3 may consume:

- realtime `chat.*`, `poll.*`, `qa.*`, `reaction.aggregate` events;
- creator/moderator community action endpoints;
- current presence count and chat settings;
- poll/Q&A state.

Chat 3 must not create a second viewer presence/chat store or media transport.

## Chat 9 hand-off

Chat 9 should supply/confirm canonical creator profile identity and, when merged, may replace the compatibility `shared_sky_follows` relationship through a narrow adapter/migration. Chat 4 currently references canonical `users.id` and never duplicates account authentication.

Chat 9 may consume report/audit IDs and moderation action records for broader case/support workflows.

## Chat 10 hand-off

Scale-sensitive compatibility components deliberately expose replaceable boundaries:

- SQLite presence aggregation -> distributed lease/presence backend;
- SQLite SSE polling -> pub/sub/WebSocket/SSE fan-out;
- SQLite rate buckets -> distributed rate limiter;
- event metrics: event rate, reconnects, presence lag, chat write latency, player failures;
- retention hooks for events/chat/reports/watch history;
- bot/scraper/fraud telemetry from presence/reaction/report rate controls.

Replacing infrastructure must preserve canonical event IDs, ordering cursor semantics, authorization and idempotency.

## Chat 11 acceptance fixtures

`tests/test_shared_sky_live_community.py` covers:

- authoritative add/reconcile/end removal;
- playback fail-closed discovery;
- deterministic search/filter/follow ranking;
- followers visibility and creator block;
- lease expiry/reconnect/tab dedup and opaque token hashing;
- realtime ordering/idempotency/replay cursor;
- durable chat retry, control-character/link safety;
- moderation bypass/timeout and stale settings concurrency;
- reaction retry and truthful share semantics;
- poll vote idempotency;
- Q&A moderation authority;
- LIVE notification deduplication;
- watch-history deletion;
- Gift/Battle non-ownership assertions;
- public route bootstrap;
- final-state follow retry semantics.

Chat 11 should additionally exercise browser/mobile accessibility against a real Chat 2 playback adapter and production-like distributed realtime/presence infrastructure before final release acceptance.

## Known compatibility gates

These are explicit gates, not simulated capabilities:

1. Chat 2 must register production playback/replay descriptors before Live Now can list playable sessions.
2. Chat 5 must register Gift display/send-result state before a Gift tray can be considered operational.
3. Chat 6 must register Battle state before Battle UI can be considered operational.
4. Provider-authorised external chat adapters require provider/API approval and credentials.
5. Restricted/age-gated viewing remains fail-closed pending canonical age/suitability assertions.
6. SQLite realtime/rate/presence is suitable for deterministic integration and single-node operation; distributed scale validation belongs to Chat 10.
7. Final production release remains owned by Chat 11.
