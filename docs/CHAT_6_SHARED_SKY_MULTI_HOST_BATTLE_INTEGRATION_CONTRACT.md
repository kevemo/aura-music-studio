# Chat 6 Shared Sky Multi-Host & Battle Integration Contract

Status: authoritative participant/co-host/Battle control-plane implementation for `development/full-site-build`, rebased onto integration SHA `1a6976a32a0deb832aca2ef983b899811ee1f92b` after Chat 2 transport merged.

## Scope and authority

Chat 6 owns participant membership/readiness/stage authority and Battle lifecycle/scoring truth. It deliberately does not own media ingest/relay (Chat 2), studio canvas/mixer (Chat 3), viewer chat/likes/reactions (Chat 4), Creation Coin wallets/Gift settlement/payout liabilities (Chat 5), or repository-wide release acceptance (Chat 11).

The canonical live identity remains `shared_sky_broadcasts.id` from `aura_music_studio.shared_sky_streaming_studios`; Chat 6 does not create a second LIVE-session namespace. All durable Battle tables use the repository's canonical SQLite database (`esp.db_path` / `LSS_DB_PATH`).

## Canonical imports

```python
from aura_music_studio.shared_sky_battles import (
    MAX_PARTICIPANTS,
    BattleDomainError,
    CommittedGiftEvent,
    ReversedGiftEvent,
    EngagementScoreEvent,
    SharedSkyBattleStore,
)
from aura_music_studio.shared_sky_battle_api import battle_store
from aura_music_studio.shared_sky_battle_worker import SharedSkyBattleFinalizer
```

Production HTTP routes are composed through `aura_music_studio.esp_creator_plan_overlay` alongside the existing Shared Sky router.

## Persistence / migration contract

The repository currently uses idempotent SQLite schema evolution at store initialisation rather than a separate migration framework. `SharedSkyBattleStore._init_schema()` creates/indexes these production tables safely with `CREATE TABLE/INDEX IF NOT EXISTS`:

- `shared_sky_participants`
- `shared_sky_participant_invitations`
- `shared_sky_join_requests`
- `shared_sky_battle_rulesets`
- `shared_sky_battles`
- `shared_sky_battle_teams`
- `shared_sky_battle_members`
- `shared_sky_battle_rounds`
- `shared_sky_battle_score_events`
- `shared_sky_battle_scores`
- `shared_sky_battle_results`
- `shared_sky_battle_integrity_flags`
- `shared_sky_battle_audit`
- `shared_sky_battle_events`

The worker additionally creates `shared_sky_battle_worker_heartbeats`. Authoritative participant, timer and score state is never kept only in localStorage or process memory.

## Participant / green-room APIs — Chat 3 and Chat 9

All routes require canonical ESP membership; creator participation is rechecked against the canonical membership model. Host/producer/moderator operations are server-authorised from durable participant/live ownership state, not client role flags.

- `POST /shared-sky/api/broadcasts/{live_session_id}/participants/host`
- `GET /shared-sky/api/broadcasts/{live_session_id}/participants` — control-plane only; host/producer/moderator authority required
- `POST /shared-sky/api/broadcasts/{live_session_id}/invitations`
- `POST /shared-sky/api/invitations/{invitation_id}/respond`
- `POST /shared-sky/api/invitations/{invitation_id}/revoke`
- `POST /shared-sky/api/broadcasts/{live_session_id}/join-requests`
- `POST /shared-sky/api/join-requests/{request_id}/decision`
- `PUT /shared-sky/api/participants/{participant_id}/readiness`
- `PUT /shared-sky/api/participants/{participant_id}/stage`
- `PUT /shared-sky/api/participants/{participant_id}/controls`
- `POST /shared-sky/api/participants/{participant_id}/disconnect`
- `POST /shared-sky/api/participants/{participant_id}/reconnect`
- `POST /shared-sky/api/broadcasts/{live_session_id}/transfer-host`
- `POST /shared-sky/api/participants/{participant_id}/remove`

Invitation links/tokens are user-bound, random, expiring and stored only as SHA-256 hashes. Pending duplicates are deduplicated; revoked/expired/used invitations cannot be replayed. Invitation creation is rate-limited and both invite creation and acceptance enforce the current transport-confirmed capacity. Join requests are separate durable objects with a fixed-window abuse limit and are capacity/eligibility checked again at approval.

A connected/backstage participant is not automatically on programme. `readiness_state`, `stage_state` and `join_state` are distinct. Stage/control mutations can use `expected_version` for stale-client rejection.

## Capacity and Chat 2 boundary

Shared Sky's product ceiling is eight participants. Chat 6 also requires the current transport path to confirm a safe capacity; it will not claim eight-person support if transport has not done so.

Compatibility order:

1. if the canonical Chat 2 transport object later exposes `participant_capacity(live_session_id)`, Chat 6 consumes it;
2. otherwise deployment may explicitly set `SHARED_SKY_MULTIHOST_MAX_PARTICIPANTS` to a measured safe value from 1–8;
3. absent either, participant admission fails closed with `capability_unavailable`.

`SHARED_SKY_PARTICIPANT_RECONNECT_GRACE_SECONDS` controls the server-side reconnect grace (bounded by the implementation). Reconnect reuses the same participant ID/slot and preserves Battle membership/team/score.

Chat 2 may register Chat 6 composite output using its documented `battle_program` programme-source type. Chat 6 does not create or control SFU/transcoder/relay infrastructure.

## Battle / ruleset APIs

Owner rulesets:

- `POST /owner/shared-sky/api/battle-rulesets`
- `POST /owner/shared-sky/api/battle-rulesets/{ruleset_id}/activate`

Creator/operator controls:

- `POST /shared-sky/api/broadcasts/{live_session_id}/battles`
- `GET /shared-sky/api/battles/{battle_id}` — control-plane snapshot only
- `POST /shared-sky/api/battles/{battle_id}/start`
- `POST /shared-sky/api/battles/{battle_id}/pause`
- `POST /shared-sky/api/battles/{battle_id}/resume`
- `POST /shared-sky/api/battles/{battle_id}/teams`
- `POST /shared-sky/api/battles/{battle_id}/rounds/finalise`
- `POST /shared-sky/api/battles/{battle_id}/rounds/next`
- `POST /shared-sky/api/battles/{battle_id}/void`

Owner evidence/reconciliation controls:

- `POST /owner/shared-sky/api/battles/{battle_id}/adjust`
- `GET /owner/shared-sky/api/battles/{battle_id}/reconciliation`
- `POST /owner/shared-sky/api/battles/{battle_id}/rebuild-scores`
- `GET /owner/shared-sky/api/battles/{battle_id}/audit`

Supported structural modes are `1v1`, `2v2`, `3v3`, `4v4`, `multi_team`, `free_for_all`, `host_challengers`, and `collaborative`. Exact fixed-team participant counts are validated server-side. Teams use stable IDs and cannot be silently reassigned after start.

## Ruleset semantics

A ruleset is a durable key/version plus immutable Battle reference. Configuration includes round duration/count, late-event grace, tie policy, pause permission and explicit eligible source weights/caps. Production score values are not inferred from a competitor or from Coin cost. If an Owner-approved score profile is absent, no production ruleset should be activated.

Implemented tie policies are `declare_tie` and `extra_round`. `sudden_death` currently fails closed rather than pretending to support event-finalisation semantics that are not yet defined. No random winner is ever selected.

## Authoritative timer / worker

Battle and round `starts_at`, `ends_at` and `scoring_closes_at` timestamps are committed by the server. Clients may derive a display countdown from `server_now` plus the persisted end timestamp; browser `setInterval` is never competitive truth.

`SharedSkyBattleFinalizer` can finalise due rounds with zero connected clients and writes durable worker heartbeat evidence. Runtime gates:

- `SHARED_SKY_BATTLE_WORKER_ENABLED=1`
- `SHARED_SKY_BATTLE_WORKER_POLL_SECONDS=<bounded seconds>`

The package script is `shared-sky-battle-worker`.

## Chat 5 Gift boundary

Chat 6 accepts only typed authoritative source evidence:

```python
CommittedGiftEvent(
    event_id: str,
    transaction_id: str,
    recipient_user_id: str,
    gift_definition_id: str,
    occurred_at: str,
    risk_state: str = "allow",
    correlation_id: str = "",
)

ReversedGiftEvent(
    event_id: str,
    reverses_event_id: str,
    occurred_at: str,
    reason: str = "",
    correlation_id: str = "",
)
```

Integration methods:

```python
battle_store.apply_committed_gift(battle_id, event)
battle_store.reverse_gift(battle_id, reversal)
```

A Gift request, animation or optimistic UI action is not a score event. Chat 6 never imports the Coin wallet, never debits/credits Coin balances and never calculates creator payout/liability. The same canonical Gift source event ID can contribute at most once globally, preventing replay into multiple Battles. Reversal creates a compensating append-only score event; it never erases score history.

The final Chat 5 integration should call these methods from its committed/reversed Gift delivery boundary or a shared signed event consumer. Do not expose a browser-callable endpoint that lets a viewer forge `CommittedGiftEvent`.

## Chat 4 engagement boundary

Deterministic engagement input:

```python
EngagementScoreEvent(
    event_id: str,
    event_type: str,       # like_batch | reaction_batch
    recipient_user_id: str,
    occurred_at: str,
    count: int = 1,
    risk_state: str = "allow",
    correlation_id: str = "",
)
```

Integration method:

```python
battle_store.apply_engagement(battle_id, event)
```

Chat 4 must supply stable durable batch/event identity and authoritative counts. Raw visual reaction bursts or client-generated cumulative counters are not accepted as competitive score truth. The same source ID is globally replay-protected.

## Deterministic score engine

`shared_sky_battle_score_events` is append-oriented evidence. Current score materialisation in `shared_sky_battle_scores` is an integer cache that can be rebuilt from evidence. Ruleset application uses integer arithmetic only.

Core invariants:

- one canonical source event contributes at most once;
- event-time determines round eligibility;
- recipient must have been an eligible Battle member at source-event time;
- score materialisation is reconstructable from score events;
- reversal/adjustment is append-only;
- final result remains versioned so corrected/voided outcomes do not erase the original record;
- no Battle operation mutates a Coin wallet.

Reconciliation:

```python
battle_store.reconcile(battle_id)
battle_store.rebuild_scores(battle_id)
```

When canonical Chat 4/5 event-query adapters land, Chat 11/Owner reconciliation can additionally compare their source stores against the Battle evidence store. Chat 6 currently cannot truthfully query source stores that do not yet exist on the integration branch.

## Chat 4 viewer-safe contract

No public browser route exposes internal fraud/moderation/payment evidence. Chat 4 should consume these service functions from its authorised viewer layer:

```python
from aura_music_studio.shared_sky_battle_api import (
    viewer_battle_snapshot,
    viewer_battle_events,
)
```

`viewer_battle_snapshot(battle_id)` contains only public/on-programme participants, teams, current round, authoritative timestamps/remaining-time metadata, scores, rules summary, result, score version and event cursor.

`viewer_battle_events(battle_id, after_cursor=...)` exposes only a minimal monotonic cursor stream (`battle_id`, `event_type`, optional participant ID, correlation ID, created time). It is deliberately a durable compatibility event log, not a second WebSocket/pub-sub system. Chat 4 should project it onto the repository's canonical realtime bus when that bus contract lands.

## Chat 3 studio contract

Chat 3 owns layouts/programme switching. It should consume stable participant IDs plus:

- `participant_control_state(live_session_id, actor_user_id)` for authorised green-room/control-room state;
- `control_snapshot(battle_id, actor_user_id)` for Battle state, teams, timer, score and allowed controls;
- `realtime_events(...)` for incremental Battle domain changes;
- participant `stage_state`, `slot_index`, `readiness_state`, `connection_state`, `media_ref`, `muted`, `camera_enabled`.

Chat 3 must not recalculate score or appoint participants locally.

## Chat 9 Owner/Admin/support contract

Chat 9 can use the Owner ruleset, adjustment, void, audit and reconciliation routes above. Score corrections require a reason and create evidence; no silent editable score field exists. Support history can use `battle_store.history(live_session_id)` and the versioned result/audit records.

A global ranking algorithm is intentionally not invented because the authoritative product specification does not define one. Finalised Battle history/win-loss views can be layered later from valid result records.

## Security / privacy / moderation boundaries

Implemented:

- authenticated canonical member/Owner guards at API boundaries;
- creator eligibility recheck for participation;
- live-session IDOR prevention through durable ownership/participant authority;
- hashed, expiring, account-bound invitation tokens;
- invite/request rate limiting and deduplication;
- server-side capacity enforcement under `BEGIN IMMEDIATE` plus unique slot constraints;
- optimistic stale-version checks on stage/participant controls/team mutations;
- global source-event replay protection;
- append-only moderation/removal/Battle evidence;
- backstage participants omitted from viewer snapshots;
- no payment/payout/fraud detail in viewer payloads.

Open cross-chat dependency: the current integration branch does not expose a canonical creator-to-creator block/privacy relationship service. Chat 6 therefore does not invent a second block list. Before production admission, Chat 1/4/9 must supply a canonical relationship eligibility callback and Chat 6 must enforce it for invitations/requests.

## Error contract

`BattleDomainError.code` is mapped by the API into structured `HTTPException.detail` with a correlation ID. Important codes include:

- `unauthorised`
- `creator_ineligible`
- `participant_capacity_reached`
- `participant_already_joined`
- `invite_expired`
- `invite_revoked_or_used`
- `rate_limited`
- `participant_not_ready`
- `stale_session_version`
- `invalid_team`
- `invalid_participant_set`
- `ruleset_unavailable`
- `ruleset_unconfigured`
- `battle_already_active`
- `battle_not_active`
- `source_event_duplicate`
- `source_event_ineligible`
- `source_event_outside_scoring_window`
- `moderation_restriction`
- `capability_unavailable`

Private integrity thresholds/evidence are not included in ordinary user errors.

## Observability / audit

Consequential participant/Battle/score actions append to `shared_sky_battle_audit` with live/Battle/participant/correlation references. Battle-bound actions also emit a minimal cursor event into `shared_sky_battle_events`. Worker heartbeats expose whether server-time finalisation is actually running.

Chat 10 can derive metrics from audit/events and load-test these seams: admission contention, score-event lag/duplicate rate, reconnect rate, finalisation duration, reconciliation discrepancy count and worker health. Chat 6 does not fabricate bitrate/packet-loss/media metrics.

## No-wagering invariant

This domain has no wager, stake, paid-entry pool, odds, prediction market, random paid prize, pooled Gift redistribution or winner-takes-stake path. Gifts may be an Owner-configured deterministic score source only after Chat 5 commits the Gift; score has no payout semantics.

## Compatibility note for PR #569

PR #569 (`feature/shared-skies-live-network`) contains an earlier mixed Live/Gift/Battle prototype where client-supplied engagement amounts mutate counters, the same module mutates Coin wallets, and Battle score is derived from mutable `like_count`/`gift_coins`. Those Battle/co-host/scoring paths are non-canonical once this Chat 6 implementation lands and must be removed/rebased rather than retained as a competing Battle authority. Chat 4 discovery/player/community and Chat 5 Gift economy should retain only their own canonical responsibilities.

## Deterministic fixtures / tests

`tests/test_shared_sky_battles.py` uses a fake UTC clock and SQLite database. It covers capacity, invite theft/expiry/revocation, request limits, backstage/programme separation, reconnect and host transfer, server timer and idempotent start, fixed Battle modes, stale versions, concurrent admission, deterministic Gift/engagement scoring, global source replay protection, reversal compensation, late windows, tie/finalisation/correction, moderation evidence, reconciliation/rebuild, viewer privacy, no-wallet/no-wagering invariants and worker finalisation without clients.

## Current blockers before production Battle scoring

1. Chat 2 must expose/measurably configure real participant transport capacity and per-participant media health for the deployed media path.
2. Chat 4 must land the canonical deterministic engagement event/batch delivery contract and realtime projection.
3. Chat 5 must land canonical committed/reversed Gift event delivery/signature/schema; until then only typed deterministic fixtures are used.
4. Chat 1/4/9 must expose the canonical block/privacy relationship check used at invite/request admission.
5. Owners must approve production score values/ruleset versions; no competitor-derived production values are enabled by Chat 6.
6. `SHARED_SKY_BATTLE_WORKER_ENABLED` must be deliberately enabled and operationally monitored where automated timer finalisation is required.
7. Repository release-control/branch-protection and Chat 11 exact-head release gates remain external to Chat 6.

These are capability/integration gates, not reasons to duplicate another chat's subsystem.

## Continuation: scheduled Battles, challenges, rematches and series

Chat 6 now also exposes durable planning primitives. These are orchestration records only; reminder delivery remains owned by the canonical notification system and media transport remains Chat 2.

Routes:

- `POST /shared-sky/api/battle-plans`
- `GET /shared-sky/api/battle-plans`
- `PUT /shared-sky/api/battle-plans/{plan_id}/schedule`
- `POST /shared-sky/api/battle-plans/{plan_id}/cancel`
- `POST /shared-sky/api/battle-plans/{plan_id}/activate/{live_session_id}`
- `POST /shared-sky/api/battle-challenges`
- `POST /shared-sky/api/battle-challenges/{challenge_id}/respond`
- `POST /shared-sky/api/battles/{battle_id}/rematch`
- `POST /shared-sky/api/battle-series`
- `POST /shared-sky/api/battle-series/{series_id}/battles/{battle_id}`
- `GET /shared-sky/api/battle-series/{series_id}`

New durable tables:

- `shared_sky_battle_origins` — idempotent origin -> Battle mapping used when a planned Battle becomes live;
- `shared_sky_battle_plans` — scheduled/rescheduled/cancelled/converted Battle plans;
- `shared_sky_battle_challenges` — multi-party accept/decline/expiry state and rematch references;
- `shared_sky_battle_series` and `shared_sky_battle_series_battles` — bounded best-of parent state and independent Battle links.

Plan conversion revalidates that every planned creator is present, ready and moderation-clear in the target canonical Shared Sky broadcast. The origin mapping makes retries return the already-created Battle rather than creating a second Battle. Rematches always create a new Battle ID and never carry score-event IDs or totals forward.

Best-of series are deliberately limited to deterministic `1v1` and `free_for_all` participant outcomes in this release. Team-series identity needs a separate stable series-team contract before it can be enabled safely; Chat 6 does not infer team identity from transient per-Battle team IDs.
