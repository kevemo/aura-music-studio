# Chat 4 Shared Sky Wave 6 — Watch Gift Sending & Live Battle Viewer State

## Purpose

Wave 6 closes the current first-party Watch-page engagement gap after the canonical Chat 5 Cosmic Creation Coin/Gift economy and Chat 6 Battle domain landed on `development/full-site-build`.

Chat 4 remains the viewer/community owner. This wave makes those already-authoritative neighbouring domains usable from the Watch page without duplicating their transaction, score, timer or lifecycle truth.

## Canonical route composition

`shared_sky_live_watch_engagement_wave6` is mounted as the single public `GET /watch/{broadcast_id}` route before older Watch routes.

The Wave 6 handler delegates the whole page to `watch_page_bridge_guard`, which in turn delegates to Watch v2. Therefore the existing Chat 2 browser-playback contract remains intact:

1. Watch v2 builds the viewer page and access-controlled descriptor.
2. Wave 4's bridge guard preserves the token-free Chat 2 cookie-bootstrap HLS flow and native-HLS capability gate.
3. Wave 6 adds only Gift/Battle viewer interaction to the already-hardened HTML.

If the expected Watch-v2 Gift marker is absent, Wave 6 returns the delegated page unchanged rather than guessing at a changed HTML contract.

## LIVE Gifts — Chat 5 remains financial authority

The viewer page refreshes Gift state only through:

`GET /shared-sky/live/api/watch/{broadcast_id}/gift-display`

That existing Chat 4 adapter projects Chat 5-owned catalogue, sender/creator eligibility, canonical recipient/live identity, current balance/spending state and whether sending is currently enabled.

A viewer Gift action is submitted only to the canonical Chat 5 endpoint:

`POST /economy/me/gifts/send`

The browser submits exactly the server-projected `recipient_creator_id` and `live_session_id` plus the selected `gift_id`, `gift_version` and `quantity=1`. Every user action carries a fresh `Idempotency-Key` header. The browser does not decrement balances, create receipts, calculate liabilities, decide eligibility, perform risk checks, reverse Gifts or infer transaction success from animation state.

After a send attempt, the Watch page refreshes canonical Gift state. A successful HTTP response means the authoritative economy route accepted the transaction; it is not a client-side ledger mutation.

### Battle attribution boundary

Wave 6 deliberately does **not** invent `battle_id` or `battle_round_id` for a Gift. Until the canonical Chat 5 live-session context and Chat 6 integration explicitly supply authoritative Battle attribution for the current Gift transaction, those fields remain absent. Chat 4 must not guess a score recipient or round from the visual state.

## Creator Battles — Chat 6 remains score/lifecycle authority

The Watch page refreshes viewer-safe Battle state through:

`GET /shared-sky/live/api/watch/{broadcast_id}/battle-display`

That endpoint is backed by the existing read-only `Chat6BattleDisplayAdapter`, which consumes Chat 6's explicit `viewer_live_battle(live_session_id)` compatibility seam.

Wave 6 renders current participant names, authoritative score projection, Battle status and remaining-time projection. It refreshes every two seconds while a Battle is available and every ten seconds otherwise.

This polling cadence is a viewer refresh mechanism only. It is **not** an authoritative clock. Chat 4 does not start/end rounds, run timers, accept/judge score events, create teams, resolve winners, mutate participants or finalise results.

## Security and privacy boundaries

- No Chat 2 bearer token is added to HTML, JavaScript, URLs or headers by Wave 6.
- No media URL, stream key, destination credential or storage URI is created by Wave 6.
- Gift catalogue and Battle payload data are rendered with DOM `textContent`/element creation rather than executable HTML insertion.
- Gift submission uses same-origin authenticated browser context and the canonical Chat 5 API.
- Failed or unavailable neighbour state degrades the relevant viewer panel without fabricating success.
- The page remains `Cache-Control: no-store`.
- Existing Shared Sky access checks, creator blocks, membership visibility rules and moderation controls remain authoritative.

## Truthful current limitations

Wave 6 does not claim:

- first-party Gift animations or audio are complete;
- Gift event animation equals financial settlement;
- Battle refresh is realtime push or an authoritative timer;
- Battle-targeted Gift scoring is wired until canonical attribution is published;
- a distributed SSE/WebSocket fanout layer exists;
- production browser/device/load acceptance is complete.

Those remain separate integration or production-readiness work.

## Acceptance gate

This wave may be integrated only after the exact current PR head, against the exact current `development/full-site-build` base, passes:

- Elevate Souls Command Center CI;
- Security Gates;
- Command Center Self-Host Smoke.

If the integration branch moves after validation, the branch must be reconciled and revalidated. Final production release/deployment remains Chat 11-owned.
