# Chat 4 Shared Sky Upcoming Events & Reminders Extension

This document extends the **Chat 4 Shared Sky Live Now & Community Integration Contract** with privacy-safe scheduled LIVE publication and viewer reminders.

## Authority boundary

The studio table `shared_sky_schedules` is creator-operational state. It is **not** a public discovery index. Chat 4 never exposes a schedule merely because it exists.

A schedule becomes viewer-visible only when its creator explicitly creates a record in `shared_sky_schedule_publications`. The public record contains only viewer-safe metadata and references the canonical schedule ID.

## Persistence

Added tables:

- `shared_sky_schedule_publications` — explicit public/follower/member/unlisted/restricted publication sidecar;
- `shared_sky_schedule_reminders` — explicit signed-in viewer reminder preference and lead time;
- `shared_sky_schedule_reminder_emissions` — claim/dedup/retry state for server-side notification delivery.

No destination credentials, project-private state, transport secrets or provider tokens are duplicated into the publication layer.

## Publication API

- `GET /shared-sky/live/api/events`
- `GET /shared-sky/live/api/events/{schedule_id}`
- `PUT /shared-sky/live/api/schedules/{schedule_id}/publication`
- `DELETE /shared-sky/live/api/schedules/{schedule_id}/publication`

Creator publication metadata includes:

- description;
- category;
- tags;
- language;
- visibility;
- suitability;
- optional HTTP/HTTPS thumbnail;
- optimistic publication version.

Public responses include creator identity, title, start time, category/tags/language, suitability, visibility, thumbnail and reminder state where signed in. They intentionally exclude project ID, destination IDs, destination configuration and other studio-operational fields.

Only schedules still in canonical `scheduled` state can be published. Publication requires a timezone-aware ISO-8601 start time so reminder calculations are not based on ambiguous local time.

## Visibility

Server-authoritative event access mirrors the viewer-network model:

- `public` — discoverable;
- `unlisted` — direct URL only;
- `followers` — requires canonical Chat 4 follow relationship until Chat 9 replaces/migrates it;
- `members` — requires signed-in active member identity;
- `restricted` — fails closed until canonical suitability/age assertions exist.

Creator blocks are applied before event discovery/direct access. A private operational schedule with no publication sidecar is always undiscoverable.

## Reminder API

- `PUT /shared-sky/live/api/events/{schedule_id}/reminder`
- `GET /shared-sky/live/api/events/{schedule_id}/reminder`

Reminder subscription is explicit and signed-in. `lead_minutes` is bounded from 1 minute to 7 days.

Reminder delivery is re-authorised at emission time; if access has been revoked, no notification is sent.

## Delivery hook and truthfulness

- `POST /owner/shared-sky/live/api/reminders/emit-due`

This endpoint is owner-authorised and invokes the same server function intended for a future canonical worker. It returns:

`runtime=server_hook_manual_or_worker_invocation_required`

This wording is deliberate. The current Shared Sky studio truthfully reports scheduler execution as `worker_adapter_required`; Chat 4 therefore does **not** claim that reminders run automatically in production yet.

When a worker/infrastructure scheduler is supplied by Chat 10/11, it should call `LiveEventsStore.emit_due_reminders()` on an appropriate cadence without changing the persistence or dedup contract.

## Notification semantics

Due reminders use the canonical in-app Aura notification store only:

- `kind=shared_sky_schedule_reminder`;
- `resource_kind=shared_sky_schedule`;
- `resource_id=<schedule_id>`.

No email or push delivery is claimed without canonical consent/channel infrastructure.

Emission rows use a composite identity of schedule, viewer, exact schedule start timestamp and lead time. Successful deliveries are never re-emitted for that identity. Processing claims are concurrency-protected using SQLite `BEGIN IMMEDIATE`; failed deliveries remain retryable.

## Tests

`tests/test_shared_sky_live_events.py` covers:

- private schedules never entering viewer discovery without explicit publication;
- no project/destination metadata leakage;
- creator-only publication and optimistic version conflicts;
- timezone-aware start requirement;
- unlisted/followers/members/restricted access semantics;
- creator block enforcement;
- explicit reminder preference;
- due reminder delivery through Aura notifications;
- success deduplication;
- disabled/unpublished reminder suppression;
- retry after notification failure.

## Neighbour handoff

Chat 3/9 may present creator-facing publication controls using the Chat 4 API rather than writing publication tables directly.

Chat 10/11 may schedule `emit_due_reminders()` through canonical worker infrastructure, add metrics/retention policy, and validate production cadence. They must preserve access rechecks and emission identity/dedup semantics.

Final production release remains Chat 11-owned.
