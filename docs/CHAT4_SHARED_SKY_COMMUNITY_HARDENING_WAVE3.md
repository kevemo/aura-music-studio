# Chat 4 Shared Sky Community Hardening Wave 3

## Purpose

This extension closes two correctness gaps in the Shared Sky viewer/community layer without changing domain ownership:

1. follower LIVE notifications must be retryable when Aura notification delivery fails;
2. a viewer must commit at most one final vote per poll even when duplicate or conflicting requests arrive concurrently.

It is an additive compatibility hardening layer over the existing Chat 4 `LiveCommunityStore` and can later be folded directly into the core store after the parallel build wave stabilises.

## LIVE notification delivery contract

The legacy `shared_sky_notification_emissions` row is retained for compatibility, but it is no longer treated as proof of delivery before the Aura notification is actually created.

Wave 3 adds `shared_sky_notification_delivery` with one row per:

`(broadcast_id, user_id, kind)`

States are:

- `pending` — durable intent exists but no attempt currently owns delivery;
- `sending` — one request has claimed delivery;
- `failed` — the latest delivery attempt failed and may be retried;
- `sent` — Aura notification creation was confirmed or an already-created matching Aura notification was found.

The row records attempt count, optional Aura notification ID, bounded last error, claim timestamp and update timestamps.

### Concurrency and retries

Delivery claims use SQLite `BEGIN IMMEDIATE` so two concurrent reconciliation calls cannot both become the active sender for the same follower/broadcast/kind.

A recent `sending` claim is not stolen. A claim older than the bounded stale interval may be reclaimed so a crashed process cannot permanently suppress the notification.

A delivery exception moves the receipt to `failed`; the legacy emission row is not written. A later reconciliation can therefore retry.

After successful Aura notification creation, the delivery receipt moves to `sent` and the legacy `shared_sky_notification_emissions` row is inserted for backward compatibility.

### Crash-window recovery

There is an unavoidable small process-crash window after Aura notification creation but before Chat 4 can mark its delivery receipt sent. Before creating another notification, Wave 3 checks the canonical `aura_notifications` store for the same user, `shared_sky_live` kind/resource and broadcast ID. If one already exists, that notification ID is adopted and delivery is finalised without creating a duplicate.

If a custom notifier does not expose the canonical notification store, this recovery probe safely returns unavailable rather than assuming delivery.

### Historical migration

Existing `shared_sky_notification_emissions` rows are migrated into `shared_sky_notification_delivery` as `sent`. This deliberately favours avoiding duplicate historical notifications after upgrade.

## Poll vote contract

Wave 3 adds `shared_sky_poll_vote_receipts` with exactly one receipt per:

`(poll_id, voter_key)`

The receipt stores the first committed idempotency key, committed option IDs and timestamp.

New vote submissions use `BEGIN IMMEDIATE` and re-check, inside the same write transaction:

- poll existence;
- poll state and expiry;
- single-choice vs multiple-choice rules;
- option membership;
- whether a final voter receipt already exists.

For a new vote, the poll-level receipt and all option vote rows commit atomically. For a single-choice poll this makes two competing requests deterministic: whichever obtains the transaction first establishes the final choice; the other returns the already-committed result rather than creating a second vote.

Only the first committed submission emits `poll.voted`.

## Retry and idempotency semantics

If a final receipt already exists, any later request for the same poll/viewer returns the committed poll state regardless of whether the caller repeats the same idempotency key or submits a conflicting stale key. This preserves the product rule that a viewer gets one final vote unless a future owning specification explicitly introduces vote changes.

A committed retry does not consume additional poll-vote rate-limit budget.

## Legacy vote migration

If pre-Wave-3 `shared_sky_poll_votes` rows exist without a poll-level receipt, the first Wave 3 access backfills one receipt from those rows and returns that legacy result. It does not add another vote.

## Ownership boundaries

This module does not change:

- Chat 2 media transport/playback ownership;
- Chat 5 Coin/Gift financial authority;
- Chat 6 Battle scoring/lifecycle authority;
- Chat 9 creator-profile authority;
- Chat 10 distributed queue/cache/rate-limit infrastructure;
- Chat 11 final release authority.

The current SQLite locking strategy is the authoritative single-node compatibility implementation. Chat 10 may replace the execution primitive with distributed infrastructure while preserving the same one-delivery/one-final-vote contracts.

## Installation

`shared_sky_live_bootstrap.install_shared_sky_live_community(...)` calls `install_live_community_hardening()` after neighbour/browser adapter registration and before serving viewer routes.

The installer is idempotent. It replaces only these two `LiveCommunityStore` method implementations:

- `_notify_followers_once`
- `vote_poll`

No duplicate business domain is introduced.

## Tests

`tests/test_shared_sky_live_hardening.py` covers:

- notification failure -> failed receipt -> successful retry -> one final delivery;
- historical emission migration without redelivery;
- competing single-choice vote requests committing exactly one choice/receipt/event;
- legacy vote backfill without a second vote;
- idempotent hardening installation.

Existing Chat 4 community tests remain authoritative regression coverage for ordinary notification deduplication and poll idempotency.

## Release boundary

This extension targets `development/full-site-build`. It does not authorize public production release. Final exact-tree integration, external production evidence and release remain Chat 11-owned.
