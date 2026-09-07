# Chat 2 — Shared Sky Native Watch Browser Bridge

This contract closes the first-party browser playback handoff between Chat 2 transport/media and
Chat 4 Watch without moving viewer access policy into Chat 2 or exposing playback credentials in
media URLs.

## Ownership boundary

Chat 2 owns:

- first-party HLS packaging and origin;
- short-lived signed playback credentials;
- playback-cookie issuance and verification;
- token-free bootstrap redirects;
- media-route authorization and path confinement;
- the adapter that converts Chat 2's server playback descriptor into a browser-safe Watch URL.

Chat 4 continues to own:

- Live Now / Watch discovery and UI;
- viewer visibility/access policy;
- public, unlisted, follower, member, blocked and restricted decisions;
- native-video capability detection and player UX;
- any future packaged cross-browser HLS/MSE runtime.

## Native Watch flow

1. Chat 4 asks the registered playback adapter for the current broadcast descriptor.
2. The Chat 2 browser bridge reads the canonical Chat 2 playback state.
3. If Chat 2 advertises its `cookie_exchange` browser credential mode, the bridge returns:
   - `available=true`;
   - `manifest_url=/shared-sky/media/{broadcast_id}/bootstrap`;
   - no `Authorization` value;
   - no playback token;
   - `browser_authorization_mode=cookie_bootstrap_redirect`.
4. Native video requests the bootstrap URL.
5. The bootstrap route calls Chat 4's canonical `community.access(..., direct=True)` before any
   playback token is minted.
6. Denied viewers receive an authorization failure and no playback credential.
7. Allowed viewers are rate-limited and Chat 2 mints a fresh short-lived playback descriptor.
8. The bootstrap verifies that the canonical manifest belongs to the built-in same-origin
   `/shared-sky/media/{broadcast_id}/` origin.
9. The response stores the bearer only in an HttpOnly, broadcast-path-scoped, SameSite=Strict,
   Secure cookie outside explicitly enabled insecure development mode.
10. The response redirects to `/shared-sky/media/{broadcast_id}/master.m3u8` with no credential in
    the location, query string or fragment.
11. Playlist and segment requests carry the scoped cookie automatically and the media route verifies
    the signed token and exact broadcast binding on every asset request.

## Public middleware boundary

`install_chat2_browser_playback_bridge()` adds `/shared-sky/media/` to the application's public
membership-middleware prefixes only after the Chat 2 playback integration is registered. This does
not make media public: the bootstrap runs Chat 4 viewer access policy and every HLS asset requires a
valid Chat 2 bearer/cookie.

## Fail-closed cases

The Watch adapter remains unavailable when:

- Chat 2 playback is not ready;
- the transport session is not live/degraded/reconnecting;
- the browser credential exchange is absent;
- the bootstrap viewer fails Chat 4 access policy;
- the playback origin is not the built-in Shared Sky media path;
- the signed credential is absent/expired/invalid;
- the media asset is outside the configured broadcast root.

## Browser capability truth

The current Chat 4 Watch V2 player deliberately uses native HTML video and checks native HLS support.
The secure bridge therefore makes first-party HLS usable in browsers with native HLS support; it does
not claim universal Chromium/Firefox HLS playback. A packaged HLS/MSE runtime remains a Chat 4 player
capability and should consume the same token-free bootstrap URL when implemented.

## Production composition

The media router is snapshotted through `esp_creator_plan_overlay`. Later, the canonical Shared Sky
owner/bootstrap composition installs Chat 4 integrations, applies Chat 4's generic browser fail-closed
adapter, then installs the Chat 2 cookie-bootstrap bridge as the more capable adapter when its
contract is present. This preserves deterministic route installation and parallel-chat ownership.

Final production deployment and release acceptance remain with Chat 11.
