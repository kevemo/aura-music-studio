# Chat 4 Shared Sky Neighbour Integration Wave 4

## Purpose

Wave 4 closes two viewer-network integration boundaries without taking ownership away from the neighbouring workstreams:

1. consume Chat 2's secure first-party browser playback cookie exchange when that exact contract is present;
2. consume Chat 6's viewer-safe Battle projection when Chat 6 exposes a canonical LIVE-session lookup helper.

Both integrations remain fail-closed until their owning modules and exact compatibility seams are merged into the integration tree.

## Chat 2 secure browser playback

### Ownership

Chat 2 remains authoritative for:

- transport state;
- HLS generation and media origin;
- playback bearer signing and verification;
- bearer expiry;
- the browser media-cookie exchange;
- media asset serving.

Chat 4 remains authoritative for viewer Watch access/visibility admission before a viewer can obtain a browser playback session.

### Viewer flow

The canonical Wave 4 Watch page is still the validated Wave 2 UI/state machine. Wave 4 decorates only its playback refresh path.

When Wave 2 requests:

`GET /shared-sky/live/api/watch/{broadcast_id}/playback`

Wave 4 first attempts:

`POST /shared-sky/live/api/watch/{broadcast_id}/browser-playback-session`

The POST performs the existing Chat 4 direct-Watch access check and then asks Chat 2 for its canonical raw playback descriptor. Chat 4 accepts the descriptor only if all of the following are true:

- playback capability is `ready`;
- transport state is LIVE/degraded/reconnecting;
- the media manifest is relative same-origin or exact same-origin HTTP(S);
- the canonical authorization is a bounded Bearer capability;
- `browser_authorization.mode == cookie_exchange`;
- the exchange method is POST;
- the exchange URL exactly matches the broadcast-scoped Chat 2 path;
- the cookie is declared HttpOnly and path-scoped;
- `token_in_manifest_url == false`.

Chat 4 then invokes Chat 2's canonical `authorize_shared_sky_media(...)` function server-side. Chat 2 verifies the signed bearer and creates the Secure, HttpOnly, SameSite=Strict, broadcast-path-scoped playback cookie.

Chat 4 forwards only the resulting `Set-Cookie` header and a safe playback descriptor to the browser. The bearer is not placed in:

- HTML;
- JSON response data;
- query strings;
- manifest URLs;
- JavaScript variables;
- localStorage/sessionStorage.

The cookie is cleared on Watch page exit where the Chat 2 DELETE exchange route is available.

### Same-origin requirement

Cookie-based native media delivery is accepted only for exact same-origin media URLs. Chat 4 rejects cross-origin manifests, credential-bearing URLs, protocol-relative URLs, unsupported schemes and control characters.

This is deliberate: a broadcast-path-scoped same-origin cookie must not be presented as authorization for a different media origin. If future Chat 2/10 infrastructure uses a CDN origin, that origin needs its own explicit browser credential/CORS contract rather than Chat 4 silently widening this one.

### Fallback

If the Chat 2 cookie-exchange module is absent or the playback descriptor does not advertise the exact secure exchange contract, the POST returns unavailable. Wave 4 then falls back once to the already-merged Wave 2 playback refresh, which remains fail-closed for unsupported Bearer-header native-video delivery.

No success is fabricated.

### HLS decoding remains a separate capability

The cookie exchange solves browser authorization, not HLS decoding. The Wave 2 player still checks native HLS support before claiming an `.m3u8` URL is playable. Browsers without native HLS remain fail-closed until a tested MSE/HLS runtime or another browser-native media path is merged.

## Chat 6 Battle display

### Ownership

Chat 6 remains authoritative for:

- participant/co-host lifecycle;
- Battle identity and state;
- rounds/timers;
- rulesets;
- deterministic score-event processing;
- score materialisation/reconciliation;
- result finalisation/correction;
- Battle event cursors.

Chat 4 only renders viewer-safe Battle state after Watch access has already been admitted.

### Required lookup seam

Wave 4 intentionally does not query Chat 6 private tables to discover a Battle ID.

The adapter registers only if Chat 6 exposes:

`shared_sky_battle_api.viewer_live_battle(live_session_id)`

That helper must return the current viewer-relevant Battle snapshot for the canonical `shared_sky_broadcasts.id`, or no value when no viewer Battle exists.

Until that explicit helper is merged, Chat 4's Battle display remains unavailable.

### Safe projection

When the helper exists, Chat 4 consumes only the viewer-safe snapshot fields needed for Watch:

- Battle ID/mode/status;
- on-stage participants;
- teams;
- current scores;
- score version;
- monotonic Battle event cursor;
- remaining round time;
- current round;
- final/corrected result.

Chat 4 verifies `battle.live_session_id` equals the Watch broadcast ID before rendering the snapshot.

## Battle engagement scoring boundary

Chat 4's ordinary room Like/reaction actions are not Battle score events because they have no participant recipient.

Chat 6 correctly requires `EngagementScoreEvent.recipient_user_id` for `like_batch` and `reaction_batch`. Therefore Wave 4 does not forward ordinary room engagement into Battle scoring.

A future targeted Battle-reaction workflow must:

- let the viewer explicitly choose an on-stage participant from the viewer-safe Battle snapshot;
- bind the event to that canonical participant user ID;
- use a durable idempotent Chat 4 source event ID;
- preserve authoritative occurrence time and correlation ID;
- pass through the Chat 6 score-event API/contract only;
- never calculate score values in Chat 4;
- preserve Chat 6 risk/ruleset/replay protections.

Until that workflow exists, room reactions remain community-only.

## Routes

Wave 4 adds:

- `POST /shared-sky/live/api/watch/{broadcast_id}/browser-playback-session`
- `GET /shared-sky/live/api/watch/{broadcast_id}/battle-display`
- the Wave 4 wrapper for `GET /watch/{broadcast_id}`

The Wave 4 Watch router is mounted before Wave 2. Route-signature deduplication keeps exactly one canonical GET Watch route while Wave 2 remains the underlying validated player implementation.

## Tests

`tests/test_shared_sky_live_neighbor_wave4.py` covers:

- same-origin media validation;
- credential/protocol/cross-origin rejection;
- server-side Bearer-to-cookie exchange;
- no bearer in safe JSON;
- required HttpOnly cookie evidence;
- malformed Chat 2 exchange fail-closed behaviour;
- Battle viewer-snapshot mapping;
- Battle LIVE-session mismatch rejection;
- no-active-Battle behaviour;
- Watch wrapper POST/fallback/clear-cookie wiring;
- canonical Wave 4 route precedence and neighbour route mounting.

## Release boundary

Wave 4 targets `development/full-site-build`. It does not authorize public production release. Chat 10 retains distributed infrastructure/media hardening and Chat 11 retains final exact-tree acceptance and production release authority.
