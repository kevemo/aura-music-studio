# Chat 4 Shared Sky Watch Player Wave 2

## Purpose

This extension makes the first-party Shared Sky `/watch/{broadcast_id}` surface resilient, accessible and truthful without taking ownership of media transport, Coin/Gift financial state or Battle scoring.

## Canonical route

`GET /watch/{broadcast_id}` is provided by `aura_music_studio.shared_sky_live_watch_ui_v2.watch_page_v2`.

`shared_sky_live_bootstrap.install_shared_sky_live_community(...)` mounts the Wave 2 Watch router before the legacy Chat 4 community router. Route-signature deduplication therefore retains one canonical GET Watch route while keeping the legacy implementation as compatibility code.

## Playback truth

Chat 4 consumes the canonical playback/replay projection already registered by the Shared Sky neighbour adapters.

The Watch player does not:

- construct HLS manifests;
- sign media tokens;
- move Bearer credentials into URLs;
- claim custom-header HLS works through a native HTML video element;
- fabricate quality renditions;
- fabricate DVR windows;
- fabricate captions or replay assets.

Safe relative or HTTP(S) playback URLs are accepted only when the canonical descriptor says they are available. Credential-bearing URLs, script/data schemes, protocol-relative URLs, control characters and missing media URLs fail closed.

Replay compatibility accepts the canonical Chat 2 `replay_url` handoff as well as `manifest_url`/`url` when present.

A manual quality choice is rendered only when a canonical rendition has its own safe media URL. Otherwise the player remains Auto-only.

The DVR jump-to-live control is available only when `dvr=true` is supplied by the canonical descriptor.

The browser detects whether native HLS is available before claiming an `.m3u8` source is playable. Browsers without native HLS remain fail-closed until the Shared Sky browser HLS runtime or another documented browser-safe transport mode is merged.

## Viewer/community behaviour

Wave 2 retains the existing server-authoritative Chat 4 contracts for:

- presence leases and viewer counts;
- chat history and writes;
- reactions;
- truthful share intent tracking;
- reporting;
- following;
- realtime SSE events;
- polls and voting;
- Q&A submission and moderation state.

Anonymous viewers may watch, join presence, react, vote where allowed, share and report according to the existing access rules. Member-only Follow, chat-send and Q&A-send controls are disabled in the rendered page when there is no authenticated member.

Approved/answered public Q&A projection intentionally omits the raw asker user ID.

Anonymous presence resume tokens used to resolve poll-vote state are sent in the `X-Shared-Sky-Presence` request header. They are not appended to the interactives URL/query string.

## Player controls

The Wave 2 UI adds:

- play/pause;
- mute/unmute and volume;
- Picture in Picture where supported;
- fullscreen;
- keyboard controls (`Space`/`K`, `M`, `F`, and `L` for live edge when DVR exists);
- portrait/landscape media adaptation;
- native captions when canonical tracks are supplied;
- bounded playback refresh with exponential retry;
- authoritative LIVE/ENDED and viewer-count refresh;
- responsive mobile layout;
- reduced-motion CSS support.

## Gift and Battle ownership

Cosmic Creation Coin Gifts remain display-only in Chat 4. Gift debit/send, catalogue financial truth, balances, liabilities, reversals and eligibility remain Chat 5-owned.

Battle rendering remains read-only and absent when a canonical Chat 6 Battle display adapter is unavailable. Chat 4 does not create Battles, run timers or calculate scores.

## Current external dependency

Chat 2 Wave 2 media-runtime work is still expected to provide a browser-consumable authorization/runtime contract for first-party HLS. The current merged Chat 4 browser adapter intentionally fails closed for a descriptor requiring a separate `Authorization: Bearer` header.

## Tests

`tests/test_shared_sky_live_watch_ui_v2.py` verifies:

- safe playback/replay URL projection;
- credential/protocol rejection;
- presence token header transport;
- public Q&A identity minimisation;
- viewer HTML escaping and signed-out control state;
- no presence token in interactives query URLs;
- truthful HLS-runtime messaging;
- Wave 2 route precedence over the legacy Watch route.

## Release boundary

This extension targets `development/full-site-build`. It is not a production-release authorization. Chat 10 owns distributed production hardening and Chat 11 owns final repository-wide acceptance and release.
