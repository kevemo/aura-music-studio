# Chat 4 Shared Sky Limited Moderator Actions — Wave 5

## Least-privilege objective

A valid Moderator permission does not make the user a substitute Creator, Owner, Agent administrator or show producer. Wave 5 separates identity/assignment authority from the specific actions a delegated Moderator can perform.

## Delegated Moderator actions

A non-owner/non-creator user with both the global Owner-enabled Moderator permission and a per-LIVE assignment may perform the following session-scoped moderation actions:

- delete/remove a LIVE chat message;
- temporarily timeout a user, which prevents that user from chatting for the configured period and serves as the bounded LIVE chat mute;
- remove a viewer from the current LIVE session;
- approve, reject or remove pending Q&A submissions;
- view the current LIVE moderation/report queue;
- escalate a viewer report for further review;
- flag the current stream for review, including an urgent review severity.

All of these remain auditable server-side operations.

## Creator/Owner-only actions

A delegated Moderator is denied the following broader actions:

- persistent creator-level block/unblock relationships;
- changing LIVE-wide chat enabled/followers-only/members-only/slow-mode configuration;
- pinning/unpinning messages as show/programming controls;
- creating viewer polls;
- selecting Q&A for display or marking Q&A as answered as a show-control action.

The LIVE creator retains those creator controls. Owner-authorised operations retain Owner authority where the underlying route supports Owner sessions.

## Moderation queue privacy

`GET /shared-sky/live/api/watch/{broadcast_id}/moderation-queue` returns viewer reports and stream-review flags only after creator/owner/assigned-Moderator authorization.

The delegated queue deliberately does not expose `reporter_user_id`. It provides the target/message/category/reason/state/audit identifier and bounded evidence required to understand the report while reporting `reporter_identity_exposed=false`.

## Report escalation

`POST /shared-sky/live/api/watch/{broadcast_id}/reports/{report_id}/escalate`

- requires creator/owner/assigned-Moderator authority;
- requires a bounded reason and idempotency key;
- serializes the claim with `BEGIN IMMEDIATE`;
- updates the report to `escalated`;
- records a durable `shared_sky_report_escalations` row;
- emits a moderator-audience `moderation.action` event;
- retries with the same actor/idempotency key return the prior escalation.

## Stream-review flags

`POST /shared-sky/live/api/watch/{broadcast_id}/flag-stream`

- requires creator/owner/assigned-Moderator authority;
- supports `review` and `urgent` severity;
- requires a bounded reason and idempotency key;
- serializes duplicate protection with `BEGIN IMMEDIATE`;
- persists `shared_sky_stream_review_flags` with an audit ID;
- emits a moderator-audience `moderation.action` event.

A flag requests review. It does not automatically terminate a stream, change transport state or claim a safety verdict. Consequential stream enforcement remains with the appropriate Owner/safety/transport authority.

## No duplicate moderation authority

The Wave 5 wrappers capture and delegate to the existing Chat 4 moderation methods after enforcing capability scope. They do not create a competing chat-ban/message/Q&A mutation system.

The new report-escalation and stream-review tables are workflow/audit records only; they do not replace `shared_sky_reports` or transport state.

## UI handoff

Chat 9 may expose a Moderator panel only when the separate Moderator permission is effective. Suitable controls are:

- moderation queue;
- delete comment;
- timeout/mute;
- remove viewer;
- Q&A approve/reject/remove;
- escalate report;
- flag stream for review.

The Agent panel must not render Creator/Owner-only moderation controls merely because the same person is also an Agent.

## Release boundary

These controls remain part of Chat 4's viewer/community moderation domain. Chat 10 retains distributed infrastructure/rate-limit hardening and Chat 11 retains final release acceptance. Fresh exact-head CI, Security Gates and Self-Host Smoke are required before merge.
