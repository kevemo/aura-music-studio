# Chat 4 Shared Sky Wave 4 — Current-Base Browser Playback & Battle Viewer Bridge

## Exact base

This Wave 4 continuation is built from integration commit `736b2835773a28844c4ee5f01f9c9df43c8ae835` after Chat 11 integrated the signed Shared Sky media-plane/browser bridge work.

The previous Wave 4 design used a Chat 4 POST endpoint to exchange a Chat 2 bearer for a media cookie. That design is now obsolete and must not be merged: the current integration tree already has a Chat 2-owned browser bridge and media bootstrap route.

## Canonical Chat 2 browser playback authority

Chat 2 now owns:

- `Chat2CookieBootstrapPlaybackAdapter`;
- token-free `/shared-sky/media/{broadcast_id}/bootstrap` descriptors;
- Chat 4 access checks at bootstrap time;
- short-lived playback-token minting;
- Secure/HttpOnly/SameSite=Strict broadcast-scoped cookies;
- token-free redirect to the actual HLS manifest;
- media-asset authorization and serving.

Chat 4 does not mint, inspect, persist, forward or place the bearer in HTML/JavaScript/URLs.

## Viewer-layer capability correction

The bootstrap URL does not end with `.m3u8`, but it redirects to HLS. The existing Watch v2 player previously used the URL suffix alone to decide whether native HLS support was required. It also emitted the bootstrap URL directly into the initial `<video src>` before JavaScript capability checks ran.

Wave 4 adds a narrow `shared_sky_live_watch_bridge_guard` mounted before the v2 Watch route. It delegates the complete page to v2 and intervenes only when the inert initial descriptor declares `browser_authorization_mode=cookie_bootstrap_redirect`.

For that mode the guard:

1. removes the eager initial `<video src>` and leaves the video hidden until capability evaluation;
2. treats cookie-bootstrap playback as HLS even though its URL does not end in `.m3u8`;
3. preserves the existing truthful unsupported-browser status when native HLS is absent;
4. prevents cookie-mode quality options from selecting a cross-origin rendition URL;
5. removes eager caption-track requests from the cookie-bootstrap initial player;
6. fails closed if the expected v2 capability hook drifts;
7. preserves the token-free bootstrap URL only inside inert initial JSON so a supported browser can apply it after capability validation.

No packaged HLS JavaScript runtime is claimed. Browsers without native HLS remain truthfully unsupported until a reviewed runtime is bundled.

## Chat 6 viewer Battle bridge

`shared_sky_live_battle_bridge` is read-only and dynamically activates only if Chat 6 publishes:

`shared_sky_battle_api.viewer_live_battle(live_session_id)`

Chat 4 will not query Chat 6 private tables to discover an active Battle ID. Until that public seam exists, the existing unavailable Battle adapter remains authoritative.

When available, the bridge validates `snapshot.battle.live_session_id` equals the current Watch broadcast and projects only viewer-safe Battle state: battle ID/status/mode, staged participants, teams, scores, score version, event cursor, remaining time, current round and result.

It does not create Battles, control participants, run timers, calculate scores, apply Gift events or mutate Chat 6 state.

## Battle engagement scoring boundary

Ordinary room likes/reactions remain Shared Sky community engagement. Chat 6's score input requires an explicit recipient user ID, so Chat 4 must not guess which competitor receives a room reaction. Any future score-eligible Battle reaction must explicitly target an on-stage participant and produce a durable canonical source event for Chat 6. Score values remain Chat 6 ruleset authority.

## Acceptance

This unit must pass fresh exact-head:

- Elevate Souls Command Center CI;
- Security Gates;
- Command Center Self-Host Smoke.

The integration branch must be rechecked immediately before merge. Any integration movement requires reconciliation and fresh validation. Final production release remains Chat 11-owned.
