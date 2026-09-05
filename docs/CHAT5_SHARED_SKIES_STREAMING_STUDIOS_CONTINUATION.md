# Chat 5 — Shared Skies Streaming Studios continuation

Authoritative programme: seven-chat build

Integration target: `development/full-site-build`

This document records the ownership reconciliation for Shared Skies Streaming Studios after the previous eleven-chat programme was superseded. Historical module/PR names such as “Chat 2 transport”, “Chat 3 control room”, “Chat 4 community”, “Chat 5 economy” and “Chat 6 Battles” describe implementation lineage only. They do not retain separate ownership authority under the seven-chat programme.

## Canonical authorities retained

Chat 5 continues and integrates the already-merged canonical implementations rather than replacing them:

- `shared_sky_transport_domain` — broadcast transport state, destinations, preflight, playback/recording handoff and truthful runtime state.
- `shared_sky_control_room` and extensions — Preview/Programme, scenes/sources, transitions, operator controls, layouts, graphics, ingest handoff and transport console.
- `shared_sky_live_*` — Live Now, Watch, viewer presence, community/chat/Q&A/polls/reactions, upcoming events/reminders, access rules, blocking and bounded moderation.
- `shared_sky_battles` / `shared_sky_battle_api` — multi-host participant lifecycle, invitations/join requests, green room/stage, deterministic append-only Battle scoring, plans/challenges/rematches/series, finalisation, correction/reconciliation and viewer-safe projections.
- `aura_live_overlay_effects` / `aura_live_overlay_interactives` — legacy-named executable bounded LIVE overlay/effect catalogue. Rhiannon-facing integration consumes Chat 1 intelligence authority rather than creating a second assistant authority.
- Creation Live / Game Forge LIVE adapters — safe creative source registration and Preview materialisation while Shared Skies retains transport and public runtime truth.

## Financial boundary

Under the seven-chat programme, Chat 6 owns authoritative Coin/Gift/payment truth. Shared Skies may display Gift catalogue/eligibility/balance state and submit Gift sends only through the canonical server-authoritative economy contract. Shared Skies and Battle clients must never directly debit/credit wallets, mutate payout liability or treat a client counter as financial truth.

Battle Gift scoring consumes committed/reversed Gift events through the typed Battle adapter. Battle score state never becomes wallet authority.

## Moderator boundary

Agent status alone grants no LIVE moderation capability.

Delegated moderation requires both:

1. a current Owner-enabled global Moderator permission; and
2. an explicit assignment to the specific LIVE.

Creator/Owner retain their own bounded authority. Delegated Moderator access remains limited to the existing moderation action set and never grants transport, Battle score, Coin/Gift, payout, Agent CRM, Owner/Admin or provider-credential authority.

## Truth boundary

Repository control-plane code is not evidence that external/deployed media capability exists.

Do not claim any of the following without runtime/provider evidence:

- WebRTC/SFU guest media;
- distributed transcoder fleet or CDN playback;
- production contribution-ingest termination merely because signed ingest credentials can be issued;
- provider OAuth/API approval or provider chat capability;
- production RTMP/SRT/RIST destinations;
- real provider credentials/stream keys;
- automatic reminder/push/email delivery where only a server hook exists.

Readiness must remain fail closed and blockers must be safe projections. Stream keys, tokens, raw provider errors and private backing paths must not be exposed.

## Current Chat 5 continuation changes

### Canonical multi-host capacity

`SharedSkyTransportStore.participant_capacity(live_session_id)` is now the canonical admission-capacity seam consumed by the existing multi-host/Battle store.

- explicit `SHARED_SKY_MULTIHOST_MAX_PARTICIPANTS` configuration is required;
- values are hard-capped to eight total participants including host;
- unknown and terminal canonical transport sessions return zero;
- invalid/missing configuration returns zero;
- this is an admission ceiling, not a claim that WebRTC/SFU media is deployed.

### Emergency Programme source hide

`POST /shared-sky/studio/api/sessions/{session_id}/emergency/hide-source/{source_id}` provides the previously missing purpose-built on-air safety action.

- operates on the immutable committed Programme snapshot, not Preview/project state;
- hides exactly one selected Programme source;
- enforces the existing no-secret invariant;
- uses optimistic Studio versioning;
- commits through the existing authoritative transport adapter;
- fails closed if transport rejects the Programme commit;
- already-hidden retries are harmless;
- emits a bounded Studio audit/event record;
- does not stop transport, touch Gift/Coin state, mutate Battle score or execute arbitrary commands.

Existing contribution-ingest revoke and Creation Live detach flows remain separate canonical actions. Emergency Programme hide does not replace them.

### Private Auto Cue

`GET /shared-sky/live/auto-cue` is a member-only browser-local prompter.

- script text is typed/pasted only into the page DOM and is never submitted to a Shared Skies endpoint;
- the page uses no `localStorage`, `sessionStorage`, Beacon or fetch/XHR persistence path;
- includes start/pause/reset, bounded speed and text size, three-second countdown, mirror, fullscreen, keyboard control and browser-local second-screen mode;
- response is `no-store`, `no-referrer`, `nosniff` and uses a nonce-scoped Content Security Policy;
- the second-screen copy is created inside the browser and does not create a public LIVE overlay or server record.

### Rhiannon LIVE Guardian

`GET /shared-sky/live/api/watch/{broadcast_id}/rhiannon-guardian/readiness` provides a creator/Owner-only safe readiness contract for Chat 1 Rhiannon intelligence.

- consumes existing Shared Skies moderation and effective-assignment truth;
- reports active LIVE state and an effective assigned-moderator count without granting authority;
- preserves the dual Moderator rule: Agent alone is never Moderator, and delegated moderation requires both Owner-enabled global permission and explicit LIVE assignment;
- exposes only advisory capabilities such as surfacing queue context and suggesting bounded moderation actions;
- explicitly prohibits permission grants, LIVE assignments, provider moderation writes, transport mutations, Battle score mutation, Coin/Gift finance mutation and arbitrary commands;
- external provider moderation write remains `ready=false` unless a separately authorised provider adapter is evidenced; Guardian does not manufacture provider authority;
- Chat 1 remains the Rhiannon intelligence authority. Chat 5 supplies only the LIVE context/readiness boundary.

## Acceptance status

The following capabilities are already represented by merged canonical implementations and remain subject to regression validation on every current integration candidate:

- transport lifecycle/preflight/destination retry and truthful readiness;
- internal first-party playback/recording runtime with documented deployment boundaries;
- Preview/Programme control room and one-to-eight layouts;
- Live Now/Watch/community/Q&A/polls/access/blocking;
- upcoming LIVE events and reminder persistence/deduplication;
- multi-host invitations, join requests, green-room/stage and reconnect lifecycle;
- deterministic append-only Battle scoring, finalisation, reconciliation/corrections and viewer projections;
- dual-permission Moderator authority;
- executable bounded LIVE overlay/effects library;
- Music/Video/Image/Game Forge creation-source adapters consuming Shared Skies transport truth.

This continuation adds regression coverage for singular emergency/assist route composition, emergency snapshot isolation/no-secret behavior, canonical configured participant capacity, browser-local Auto Cue privacy controls, and Rhiannon Guardian least-authority projections.

## Remaining external/runtime gates

These are not converted into “complete” merely by repository code:

- deployed/verified multi-party media path (for example an SFU where that is the chosen architecture);
- production ingress/egress capacity and failure testing;
- CDN/public playback deployment evidence where required;
- provider app review, Live scopes, account eligibility and real credential validation;
- provider-backed moderation writes for Rhiannon Guardian;
- production monitoring/alerting/backup/rollback/security evidence owned by Chat 7;
- final Coin/Gift/payment production evidence owned by Chat 6;
- exact release-candidate CI/Security/Self-Host evidence after all seven chat branches reconcile.

## Merge discipline

Target only `development/full-site-build`.

Before merge:

1. re-read the current integration head;
2. confirm this branch has no unexpected behind count or overlapping authority;
3. require fresh exact-head Elevate Souls Command Center CI, Security Gates and Command Center Self-Host Smoke;
4. inspect failures rather than inheriting older green checks;
5. do not represent provider/deployment gates as passed without evidence.
