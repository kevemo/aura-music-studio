# Chat 4 Shared Sky Moderator Permission Wave 5

## Purpose

Shared Sky LIVE moderation is a limited operational permission. It is not implied by Agent, Creator, Admin, subscription, plan or ordinary ESP membership state.

This wave makes the intended separation server-authoritative:

1. an Owner (Kev/Mary owner session) enables the user's global **Moderator** permission;
2. the LIVE creator or an Owner explicitly assigns that already-enabled Moderator to a specific LIVE;
3. only when both conditions remain true can that non-owner/non-creator user perform LIVE moderation actions.

The LIVE creator remains authoritative for their own LIVE. Owner sessions retain owner override authority.

## Agent separation

`Agent` by itself grants **zero** Shared Sky moderation actions.

An Agent may receive LIVE moderation controls only if the separate Moderator permission is enabled by an Owner and the Agent is then assigned to that LIVE. Removing Agent status does not create Moderator status, and granting Agent status does not create Moderator status.

The same rule applies to Admin or any other non-owner role: no role is silently translated into Moderator authority.

## Global Moderator permission

Owner API:

- `GET /owner/shared-sky/live/api/moderator-permissions`
- `PUT /owner/shared-sky/live/api/moderator-permissions/{user_id}`

The write requires an explicit reason and an Owner-authorized session.

Global permission rows are stored in `shared_sky_moderator_permissions`. Changes are recorded in `shared_sky_moderator_permission_audit`.

Only active accounts can receive or exercise Moderator permission. If an account becomes inactive, the runtime permission immediately fails closed even if the configured grant remains recorded for Owner review.

Revoking global Moderator permission deletes all current per-LIVE assignments for that user. This prevents an old session assignment from unexpectedly becoming effective after a future global re-grant.

## Per-LIVE assignment

Creator/Owner APIs:

- `GET /shared-sky/live/api/watch/{broadcast_id}/moderators`
- `PUT /shared-sky/live/api/watch/{broadcast_id}/moderators/{user_id}`

A creator can assign or remove Moderators only for their own LIVE. An Owner can assign/remove on an authorised owner session.

Assignment is rejected unless the target has an active Owner-enabled global Moderator permission. The grant check and assignment write are performed under the same SQLite `BEGIN IMMEDIATE` transaction so a concurrent global revocation cannot race an assignment into existence.

Assignment changes are recorded in `shared_sky_live_moderator_assignment_audit`.

## Runtime authority rule

For `LiveCommunityStore.moderator_allowed(...)`:

- Owner-authorised request: allowed;
- LIVE creator: allowed;
- unauthenticated user: denied;
- every other user: allowed only when both:
  - active global Moderator permission exists; and
  - `shared_sky_live_moderators` contains an assignment for that broadcast and user.

This rule applies to existing Chat 4 moderation operations, including chat settings, message moderation, timeout/remove/block actions, Poll administration and Q&A moderation paths that use `moderator_allowed`.

## Legacy migration behavior

Existing `shared_sky_live_moderators` rows are **not** automatically converted into global Moderator permission.

A legacy session row without an explicit new Owner-enabled global permission becomes ineffective after this guard is installed. This is intentional: automatically promoting historical rows would manufacture a new global privilege without Kev/Mary approval.

Owners can explicitly enable Moderator permission for a user and then the creator/Owner can assign them to the relevant LIVE.

## Limited role boundary

This wave does not grant:

- Owner/Admin settings;
- Agent CRM or mentor functions;
- financial/Coin/Gift authority;
- Battle scoring or Battle control authority;
- transport/start-stop/recording authority;
- creative-project access;
- provider credentials;
- arbitrary staff administration.

It only strengthens the authorization boundary used by Shared Sky LIVE moderation functions.

## Owner/Admin UI handoff

This wave provides the canonical backend/permission contract. Chat 9 may surface the Owner controls and an optional Moderator section in an Agent/staff panel, but those UI surfaces must consume this authority rather than inferring moderation from Agent role.

For an Agent UI, the moderation section should appear only when the user has the separate effective Moderator permission. Agent role alone must keep it hidden.

## Acceptance

Wave 5 must be reconciled onto the exact current `development/full-site-build` head after Wave 4 integration and then pass fresh exact-head:

- Elevate Souls Command Center CI;
- Security Gates;
- Command Center Self-Host Smoke.

No stacked/inherited green result is sufficient. Final release/deployment remains Chat 11-owned.
