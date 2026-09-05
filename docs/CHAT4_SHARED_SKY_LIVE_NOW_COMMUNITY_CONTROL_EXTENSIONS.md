# Chat 4 Shared Sky Live Now & Community Integration Contract — Control Extensions

This document is an additive part of the repository contract in `CHAT4_SHARED_SKY_LIVE_NOW_COMMUNITY_INTEGRATION_CONTRACT.md`. It records the control APIs added after the core contract was written. Authority and scope boundaries are unchanged: Chat 4 owns viewer/community state, not media transport, Coin/Gift financial truth, or Battle scoring.

## Import path

`aura_music_studio.shared_sky_live_controls`

The router is mounted by `aura_music_studio.shared_sky_live_bootstrap.install_shared_sky_live_community` from an import-time route snapshot so late FastAPI compatibility composition cannot silently consume the source router.

## Additional routes

All routes continue to pass through the repository's global security/cross-site request envelope. Identity/creator/moderator authority is resolved server-side by the handlers.

- `GET /shared-sky/live/api/preferences`
  - authenticated member required;
  - returns the current viewer safety/accessibility preferences.
- `PUT /shared-sky/live/api/preferences`
  - authenticated member required;
  - patch-style final state for `reduced_motion`, `hide_reactions`, `hide_gift_animations`, and `profanity_filter`.
- `POST /shared-sky/live/api/polls/{poll_id}/state`
  - creator/moderator authority required;
  - body state is `ended` or `cancelled`;
  - idempotency key required;
  - emits durable `poll.closed` or `poll.cancelled` event.
- `GET /shared-sky/live/api/watch/{broadcast_id}/keyword-filters`
  - creator/moderator authority required.
- `PUT /shared-sky/live/api/watch/{broadcast_id}/keyword-filters`
  - creator/moderator authority required;
  - replaces the configured term set with normalized/deduplicated values;
  - idempotency key required;
  - emits a moderator-audience `chat.settings` event.
- `GET /shared-sky/live/api/watch-history`
  - authenticated member required;
  - returns only history entries the user is still authorised to access.
- `PATCH /shared-sky/live/api/watch-history/{broadcast_id}`
  - authenticated member required;
  - stores replay progress only for an authoritative ended LIVE session and only while access remains valid.
- `DELETE /shared-sky/live/api/watch-history/{broadcast_id}`
  - authenticated member required;
  - removes one history item.

The existing `DELETE /shared-sky/live/api/watch-history` route remains the clear-all operation.

## Additional persistence

The control module creates the following idempotent tables in the same canonical application database:

- `shared_sky_live_user_preferences`
- `shared_sky_keyword_filters`
- `shared_sky_control_receipts`

`shared_sky_control_receipts` makes high-impact creator/moderator control requests replay-safe for the covered operations.

No Coin ledger, Gift transaction, Battle score/team state, playback manifest, destination OAuth credential, or provider token is persisted by these tables.

## Keyword-filter truth boundary

The keyword-filter API is intentionally reported as `enforcement: "configuration_hook"` in this Chat 4 build. It persists and distributes creator/moderator configuration, but does not claim a production policy/filter engine has blocked a message until a canonical moderation/filter evaluator is wired. Chat 9/10 or a later Chat 4 follow-up may consume this configuration. The first-party chat path still performs its implemented validation, rate limiting, timeout/block checks, safe-link extraction and output-encoding boundary independently.

Likewise, `profanity_filter` is a viewer preference state, not a claim that a language classifier or provider moderation model is already active.

## Accessibility preference truth boundary

The API persists `reduced_motion`, `hide_reactions`, and `hide_gift_animations`. The current Watch surface independently honours the operating-system/browser `prefers-reduced-motion` setting. Per-account preference application to every overlay remains a UI-integration handoff; Gift animation behaviour must continue to be driven through Chat 5's display adapter rather than a Chat 4 financial/Gift engine.

## Tests

`tests/test_shared_sky_live_controls.py` covers:

- creator-authorised, idempotent poll close;
- viewer permission bypass rejection for poll lifecycle;
- persisted safety/accessibility preferences;
- moderator-only, idempotent keyword-filter configuration;
- Watch History access filtering;
- ended-LIVE replay progress;
- per-item Watch History deletion.

The core failure-path suite remains `tests/test_shared_sky_live_community.py`.

## Neighbouring-chat handoff additions

- **Chat 3:** may call poll state controls and read viewer preferences for studio overlays; it must not duplicate the poll store.
- **Chat 5:** may honour `hide_gift_animations` in its display contract; Chat 4 does not mutate Gift financial state.
- **Chat 9:** may surface keyword configuration, reports, or history privacy controls in creator/admin/profile workflows.
- **Chat 10:** should provide distributed rate/presence/realtime infrastructure and retention/privacy policy; it may also supply the production keyword/profanity evaluator if that becomes infrastructure-owned.
- **Chat 11:** acceptance must treat configuration hooks as hooks, not as proof of filter enforcement, and must browser-test account-level overlay preference application once all neighbouring integrations are merged.
