# Chat 9 Product Workflow Integration Contract

Status: integration branch `chat9-product-workflows`

This document defines the role-specific product/workflow boundary for Creator, Agent/Mentor, Owner/Admin, Social, Support and moderation-facing product surfaces. It is intentionally an integration contract, not a replacement for canonical systems owned by Chats 1–8 or 10–11.

## 1. Authority and commercial separation

- Canonical account/session identity remains the existing account and membership stack until Chat 1 replaces or formalises it.
- ESP organisational authority and product subscription entitlement are independent dimensions.
- Current build commercial baseline: Free £0, Basic £4.99/month, Unlimited Pro £9.99/month. Cosmic Creation Coins are £5 per 1,000 Coins. Chat 9 does not calculate Coin/Gift ledgers or invent promotional Coin pricing.
- The legacy `EspStore.decide()` / `EspStore.revoke()` methods still contain historical subscription coupling, but application startup installs `install_esp_access_subscription_separation()` from `esp_level_up.py`, which snapshots/restores subscription state around ESP role changes. This compatibility policy is authoritative for the current branch; removing the legacy coupling is technical debt for the shared identity refactor.
- Owner authority must come from the server-side Owner session / ESP Owner grant. Display names are presentation only and never grant authority.

## 2. New Chat 9 routes

### Creator / public identity

- `GET /command-center/api/workflows/capabilities`
  - authenticated active ESP member;
  - returns truthful status of Chat 9 capabilities and neighbouring-chat contracts.
- `GET /command-center/api/workflows/creator-profile`
  - Creator, Both or Owner context;
  - returns the caller's durable Creator workflow profile; missing profile is represented as missing, not invented defaults.
- `PUT /command-center/api/workflows/creator-profile`
  - Creator, Both or Owner context;
  - optimistic concurrency through `expected_version`;
  - emits `chat9.creator_profile_updated` to the canonical audit writer currently represented by `AuditLedger`.
- `GET /command-center/onboarding`
  - Creator, Both or Owner context;
  - accessible server-rendered onboarding/public-profile surface.
- `POST /command-center/onboarding`
  - same role boundary;
  - performs version-checked durable update; stale writes are returned to the UI for reload/review.
- `GET /shared-sky/public/creators/{creator_user_id}`
  - public read model only;
  - returns 404 unless the creator profile is discoverable and the ESP Creator role remains active;
  - excludes goals, mentoring context, equipment, acknowledgements, support/compliance, financial and other private fields;
  - this is an identity handoff for Chat 4 discovery/Watch surfaces, not a replacement for Live Now/session truth.

### Creator evidence / analytics provenance

- `POST /command-center/api/workflows/evidence`
  - caller may attach evidence only to their own authorised Creator record, an explicitly assigned Creator record, or an Owner-authorised Creator record;
  - stores source/provider/period/captured/imported/uploader/raw evidence reference and metric-level confidence;
  - does not claim direct TikTok LIVE Backstage access.
- `GET /command-center/api/workflows/evidence/{creator_user_id}`
  - same creator/assignment/Owner boundary;
  - returns provenance and freshness labels.
- `PATCH /command-center/api/workflows/evidence/metrics/{metric_id}`
  - human correction with `expected_version`, correction reason and immutable correction record.
- `PATCH /command-center/api/workflows/evidence/{batch_id}/status`
  - version-checked review/confirmation/rejection lifecycle.

Evidence source values: `screenshot`, `csv`, `xlsx`, `pdf`, `manual`, `provider_api`, `shared_sky`.

Evidence status values: `draft`, `reviewed`, `confirmed`, `rejected`.

Imported screenshot/CSV/XLSX/PDF/manual evidence is never labelled realtime. TikTok LIVE Backstage remains external unless a future official authorised integration supplies a real capability contract.

### Recruitment CRM

- `GET /command-center/api/workflows/leads`
  - Agent/Both/Owner role;
  - ordinary Agent response is scoped to leads assigned to that Agent.
- `POST /command-center/api/workflows/leads`
  - Agent/Both/Owner role;
  - current implementation supports manual/public/authorised discovery inputs only;
  - new Agent-created lead is assigned to that Agent;
  - global platform+normalised-handle deduplication prevents repeated records/outreach.
- `PATCH /command-center/api/workflows/leads/{lead_id}`
  - optimistic concurrency;
  - ordinary Agents can mutate only leads assigned to them;
  - `do_not_contact` forces the state to `do_not_contact` and blocks later outreach-state transitions until the restriction is explicitly cleared through an authorised workflow.
- `GET /command-center/agent/leads`
  - Agent/Both/Owner server-rendered CRM surface.
- `POST /command-center/agent/leads`
  - manual lead intake surface; no scraping, mass-DM, auto-follow, engagement manipulation or bypass of provider controls.

Lead status values: `discovered`, `review`, `assigned`, `contacted`, `replied`, `interested`, `follow_up`, `applied`, `accepted`, `activated`, `declined`, `not_suitable`, `do_not_contact`.

### Announcements

- `GET /command-center/api/workflows/announcements`
  - active ESP member;
  - returns only announcements matching the caller's role/region/individual audience.
- `POST /command-center/api/workflows/announcements`
  - Owner only;
  - draft creation is allowed without confirmation;
  - scheduled/published creation is a consequential action and requires explicit `confirm_publish=true`.
- `POST /command-center/api/workflows/announcements/{announcement_id}/acknowledge`
  - caller must be within the announcement audience;
  - acknowledgement is durable and idempotent.

Audience values: `everyone`, `creators`, `agents`, `both`, `region`, `individual`.

## 3. Durable Chat 9 schema

The repository currently initialises SQLite schema in domain stores rather than using a standalone migration framework. Chat 9 follows that existing pattern on this branch and creates the following additive tables with `CREATE TABLE IF NOT EXISTS`:

- `esp_creator_workflow_profiles`
  - one record per user;
  - public display/profile fields plus private onboarding/development fields;
  - `discoverable`, `onboarding_status`, `version`, timestamps.
- `esp_creator_evidence_batches`
  - creator, source/provider, reporting period, capture/import timestamps, uploader, immutable raw evidence reference, review status/version.
- `esp_creator_evidence_metrics`
  - metric name/value/unit/confidence, review flag and version.
- `esp_creator_evidence_corrections`
  - prior/new value, unit, confidence, actor, reason and timestamp.
- `esp_recruitment_leads`
  - SHA-256 global dedupe key, provider/handle/public URL, region/niche/source, assigned Agent, status/follow-up/notes, DNC, conversion reference, version.
- `esp_recruitment_lead_events`
  - append-only lead workflow history.
- `esp_announcements`
  - targeted announcement content/lifecycle/version.
- `esp_announcement_acknowledgements`
  - durable user acknowledgement.

Foreign keys reference the existing canonical `users` table. Agent/Creator authorisation reuses `esp_agent_creator_assignments` rather than creating a second assignment engine.

## 4. Public/private Creator field contract

Public Shared Sky identity may return only:

- canonical creator user ID;
- public display name;
- avatar/banner application references;
- public bio;
- optionally published region/languages;
- primary/secondary niche;
- explicitly selected public social links;
- discoverable marker/source identifier.

The Chat 9 public endpoint must never return:

- development goals;
- equipment inventory;
- LIVE experience/mentor context;
- policy acknowledgements;
- private schedule/onboarding record;
- mentor/private notes;
- support or moderation data;
- evidence files/raw analytics uploads;
- risk/compliance flags;
- financial/economy data;
- unreleased projects.

Live state, scheduled Live state, replay/highlight state, follow state and notification state are Chat 2/4 contracts and must be joined by canonical creator ID rather than copied into this profile table.

## 5. Existing modules reused rather than duplicated

### Creator / Agent

- `esp_command_center.py`: current ESP membership/resource shell; legacy role/subscription coupling is neutralised at application startup by `esp_level_up.py`.
- `esp_niche.py`: active ESP role access and no-poaching Social access boundary.
- `esp_creator_plan.py`: durable Creator plan/action path.
- `esp_creator_reviews.py`: Creator review workflow.
- `esp_agent_roster.py`: explicit Agent→Creator assignment boundary.
- `esp_agent_operations.py`: durable check-ins and Creator success plans.
- `esp_agent_health.py`: explainable Creator health read model.

### Support

`esp_support_center.py` already provides durable private support cases, evidence and activity history. Chat 9 does not create a second ticket store. The current support implementation is retained as the canonical product surface for case creation/triage while future work can extend user-visible replies, internal-note visibility classes, SLA configuration and stronger category-specific permissions.

### Social

Existing Social modules remain authoritative for Social House state, provider connection/capability status, publishing queue/adapters, analytics and access control. Chat 9 does not create an alternative OAuth/token store. Provider tokens remain server-side and a Social item is not to be represented as provider-published until an official adapter/provider receipt confirms success.

Relevant modules include:

- `social_management.py`
- `social_management_api.py`
- `social_management_access_control.py`
- `esp_social_oauth.py`
- `esp_social_provider_adapters.py`
- `esp_social_publish_queue.py`
- `esp_social_publish_worker.py`
- `esp_social_provider_analytics.py`
- `esp_social_approvals.py`
- `esp_social_media_library.py`

## 6. Permission matrix

| Operation | Member | Creator | Agent | Both | Owner |
|---|---:|---:|---:|---:|---:|
| Public Shared Sky creator read | Yes | Yes | Yes | Yes | Yes |
| Own Creator onboarding/profile | No | Yes | No | Yes | Yes (own context) |
| Own evidence import | No | Yes | Only if also Creator | Yes | Yes |
| Assigned Creator evidence view/correction | No | No | Assigned only | Assigned only | Yes |
| Recruitment CRM | No | No | Assigned lead pool | Assigned lead pool | Yes |
| Publish/schedule announcement | No | No | No | No | Yes + confirmation |
| View targeted announcement | No ESP access | Audience only | Audience only | Audience only | Yes |

A paid subscription does not appear anywhere in this matrix as a source of ESP authority.

## 7. Optimistic concurrency / conflict contract

The following Chat 9 records use integer versions and reject stale updates rather than silently overwriting concurrent work:

- Creator workflow profile;
- evidence batch review state;
- evidence metrics/corrections;
- recruitment leads;
- announcement schema includes version for the next admin lifecycle surface.

API stale writes return HTTP 409 with `code=stale_version` where exposed. UI onboarding redirects the operator to reload/review the latest durable version.

## 8. Audit actions

Chat 9 writes through the existing hash-chained `AuditLedger` for consequential workflow changes. Current action names:

- `chat9.creator_profile_updated`
- `chat9.evidence_imported`
- `chat9.evidence_metric_corrected`
- `chat9.evidence_status_changed`
- `chat9.lead_created`
- `chat9.lead_updated`
- `chat9.announcement_created`

Do not put provider secrets, passwords, raw private evidence contents or unnecessary private notes in audit details.

## 9. Error contract

Chat 9 maps current shared-compatible errors as follows:

- HTTP 401: unauthenticated through existing session/member middleware;
- HTTP 403: role/assignment/record visibility failure;
- HTTP 404: record/public profile not found;
- HTTP 409 `stale_version`: optimistic concurrency conflict;
- HTTP 409 `duplicate_lead`: global platform/handle duplicate;
- HTTP 409 `high_impact_confirmation_required`: attempted announcement publish/schedule without explicit confirmation;
- HTTP 400: validation/import reference errors.

Future Chat 1 canonical error envelopes should replace these response wrappers without changing the domain semantics.

## 10. Provider capability truthfulness

Chat 9 follows the existing provider capability model:

- `connected + supported`: official adapter exists with required scopes;
- `connected but scope missing`: read/write action remains unavailable;
- `configured/approval pending`: do not claim operational support;
- `disconnected` / `expired`: no provider action;
- `provider unsupported`: manual planning/reminder only;
- `temporarily unavailable`: retain durable job state and surface the provider failure.

TikTok LIVE Backstage is explicitly `external_not_connected` in the Chat 9 capability manifest. Screenshot/CSV/XLSX/PDF/manual evidence is a snapshot with provenance, never a realtime Backstage feed.

## 11. Cross-chat handoffs

### Chat 1 — shared architecture/auth/roles/audit

Consumes/requires:

- canonical user ID;
- role grants/revocations and Owner authority;
- entitlement dimension independent of role authority;
- capability/provider status registry;
- shared errors/idempotency/event envelopes;
- canonical audit writer.

Current compatibility adapters: existing `users`, `esp_memberships`, `require_esp_hub_member`, `AuditLedger`. Chat 1 may replace these behind the same Chat 9 semantic boundary.

### Chat 2 — Shared Sky transport/destination infrastructure

Read-only product handoff by canonical creator/session IDs:

- broadcast session state;
- scheduled session state;
- destination/transport health;
- recording/replay readiness where surfaced.

Chat 9 does not create transport or OAuth destination state.

### Chat 3 — professional studio control room

Handoff only:

- studio entry route/preset/session references;
- operator-safe return links/history.

Chat 9 does not implement Preview/Programme, scene switching, audio mixer or production source internals.

### Chat 4 — Live Now/Watch/community

The new public creator endpoint provides a discovery-safe identity read model keyed by canonical creator ID. Chat 4 remains authoritative for:

- Live Now / discoverability session state;
- Watch/replay URLs;
- follows/notifications;
- internal LIVE analytics;
- realtime chat/community moderation state.

Immediate suspension/revocation removes the Chat 9 public profile read model; Chat 4 must independently honour the shared moderation/discoverability contract for active sessions.

### Chat 5 — Coins/Gifts/economy

Owner/Admin product surfaces must consume Chat 5 APIs for:

- Coin wallet summaries;
- Gift receipt/liability state;
- refunds/reversals;
- risk/reconciliation;
- authorised economy corrections.

Chat 9 must never calculate or mutate ledger balances or invent creator Gift payout percentages.

### Chat 6 — co-host/Battle

Product surfaces may consume:

- scheduled collaborations/Battles;
- participants/co-host state;
- result/moderation references.

No local score/timer/round engine is permitted in Chat 9.

### Chat 7 — Music/Video/Image studios

Chat 9 Social/Creator workflows may receive approved project/asset references and Go Live & Create entry points. Private/unreleased media is not made public merely because it is referenced by a campaign or creator record.

### Chat 8 — Game Forge

Same pattern as Chat 7 for Game Forge project/build/showcase/social asset references; no game runtime/build pipeline duplication.

### Chat 10 — SLS/security/infrastructure

Owner/Admin UI consumes, but does not calculate:

- session/security incidents;
- service/queue/provider/storage health;
- force-logout/session revoke actions;
- rate-limit/abuse/maintenance state.

Chat 9 does not implement another security engine, queue, cache, storage backend or backup system.

### Chat 11 — integration/release

Chat 11 should verify:

- these additive schema initialisers run safely against current data;
- route composition has no duplicate/ambiguous path registration;
- access-control tests pass with canonical Chat 1 contracts;
- provider/economy/Battle/Shared Sky adapters resolve to merged neighbour modules;
- migrations/backfill strategy is production-approved before release;
- repository-wide CI, security and release gates are green.

## 12. Test contract

New Chat 9 tests cover:

- Creator public/private field separation;
- optimistic Creator profile conflicts;
- public profile disappears after role revocation;
- evidence provenance, missing value preservation and human correction history;
- global lead deduplication;
- do-not-contact state;
- assigned-Agent lead boundary;
- Owner announcement confirmation and targeting;
- expected route exposure.

Existing repository tests continue to cover Creator plans, Agent roster/operations, ESP role/subscription separation, Owner controls, Social provider/publishing behaviour and Support modules. Chat 11 should run the full suite after all chat branches converge.

## 13. Known blockers / intentionally unimplemented dependencies

- No fake TikTok LIVE Backstage API is added. A real integration requires official platform access, approved scopes and provider credentials.
- No fake external social publishing/inbox capability is added. Existing adapters/capability states remain authoritative.
- Chat 9 does not provide payment/Coin/Gift mutation because Chat 5 owns financial truth.
- Chat 9 does not provide LIVE transport, viewer realtime, Battle scoring, creative-engine or infrastructure truth.
- A repository-wide migration framework is not currently the dominant persistence pattern; production rollout of the new tables should be folded into Chat 10/11 deployment/migration controls.
- Legacy ESP role methods still contain historical subscription mutations that are neutralised by the startup compatibility policy. Refactoring those methods themselves is shared-auth technical debt, not a reason to duplicate identity logic in Chat 9.

## 14. Current completion boundary

This branch materially implements the highest-leverage missing Chat 9 foundations—Creator onboarding/public identity, evidence provenance/correction, lawful Agent lead CRM, targeted announcements and their role-aware surfaces—while reusing existing durable Creator planning, Agent mentoring, Support and Social systems.

It does not claim the entire Chat 9 master specification is complete. Remaining gaps include deeper Owner CRUD surfaces, versioned Training CMS administration, support reply/internal-note expansion, moderation administration integration, cross-domain universal task/search/report surfaces, richer Social campaign/approval UI integration, Owner operational dashboards over Chats 2–6/10, and full Aura typed-tool coverage. Those must be implemented against merged neighbour contracts rather than simulated locally.
