# ESP Social Management — Niche-Specific Creator & Agent Hub

## Architectural boundary

Social-media management is **not** a public Pulsar-Frequency House Creative Studio feature.

It belongs exclusively to the private **Elevate Souls Productions Creator & Agent Hub** and is available only after ESP membership/role approval and completion of the member's ESP niche profile.

The Creative Studio and the ESP Creator & Agent Hub are intentionally separate product surfaces:

- **Pulsar-Frequency House Creative Studio** — music, video, image, voice and other professional creation workflows available according to normal studio membership/entitlements.
- **ESP Creator & Agent Hub** — private Creator Network training, creator operations, agent operations, niche coaching and ESP social-media management.

A normal Creative Studio subscriber does not gain ESP social-management access by buying a studio plan.

## No-poaching rule

ESP Social Management is not a recruitment or poaching tool.

The server-side authorization model requires all of the following before social-management routes can be used:

1. authenticated Pulsar-Frequency House account;
2. active ESP membership status;
3. ESP role of Creator, Agent, Creator+Agent, or Owner;
4. completed ESP niche profile;
5. affiliation confirmation that the relevant account is not represented by another Creator Network.

If the member declares that the account is represented by another Creator Network, ESP Social Management remains locked. The interface should use neutral wording and direct the person to an ESP owner only if their recorded affiliation is incorrect. It must not encourage them to leave another network.

This protection is enforced in the API, not merely by hiding a button.

## Niche Select experience

When an approved ESP creator/agent enters the ESP Hub for the first time, the hub routes them through **Niche Select**.

The niche profile stores:

- primary niche;
- optional sub-niche;
- audience description;
- creator goals;
- Creator Network affiliation status.

The niche then drives:

- hub colour/theme treatment;
- Aura coaching context;
- training priorities;
- content pillars;
- suggested LIVE formats;
- short-form video strategy;
- campaign planning;
- social-management prompts and workflows;
- future analytics interpretation and recommendations.

Current niche catalogue includes music/performing arts, gaming, beauty, fashion, fitness, food, travel, education, business, technology/AI, art/design/crafts, comedy/entertainment, spirituality/mindful community, lifestyle, family/parenting, talk/podcast, battles/interactive entertainment, sports, automotive, books/writing, pets/animals, wellness/self-care, and a custom/other path.

## Rella research: feature-class benchmark only

Public research into Rella is used as a feature-class benchmark. Pulsar-Frequency House/ESP must not copy Rella's proprietary code, protected interface assets or private implementation.

The objective is independent implementation of comparable social-management capabilities, then expansion through ESP niche training and Aura intelligence.

### Feature classes to match or exceed

#### Multi-brand / multi-account workspaces

ESP Social Houses support the architectural concept of separate creator/brand/campaign spaces with their own:

- connected platform accounts;
- content calendar;
- projects/campaigns;
- tasks and notes;
- media references;
- Persona/brand voice;
- approvals;
- analytics/reporting state;
- activity history.

Agents can eventually manage multiple **approved ESP** creator workspaces according to their role. This must not become a mechanism for managing creators belonging to another network.

#### Content planning

Target capabilities:

- calendar view;
- Kanban/board view;
- table/list view;
- unscheduled idea queue;
- drafts;
- custom statuses;
- platform variants;
- content pillars and tags;
- projects/campaigns;
- recurring content concepts;
- assignees;
- due dates;
- tasks and dependencies;
- notes;
- reusable campaign templates.

#### Platform-specific content

Architectural support exists for:

- TikTok;
- Instagram;
- Facebook;
- YouTube;
- LinkedIn;
- Pinterest;
- Threads;
- X;
- podcast workflows;
- Google Business Profile;
- custom channels.

Each platform variant can carry its own caption, hashtags, media references, cover, aspect ratio, schedule, timezone, publishing state and external-post metadata.

Platform limits and available publishing features change over time; they must be verified against official APIs rather than hard-coded forever.

#### Scheduling and publishing

Target flow:

1. create content;
2. create platform-native variants;
3. attach approved media;
4. pass required approval gates;
5. schedule by timezone;
6. validate against the destination platform;
7. place due content into the private production queue;
8. publish only through an authorised official integration;
9. record provider confirmation/external post ID;
10. ingest subsequent analytics where permitted.

The production queue now supports timezone-aware planned/blocked/queued states, retries, approval checks and adapter-readiness checks. **Queued does not mean published.** The application records `published` only after a trusted provider adapter supplies a provider post ID.

The application must never pretend a platform is connected or a post is published when an OAuth/API integration has not been authorised and confirmed.

#### Approvals and collaboration

Target capabilities:

- internal creator/agent comments;
- owner/mentor review where appropriate;
- approval-required content;
- approved/rejected/revision states;
- external approval links where business/client workflows need them;
- activity history;
- publishing block until required approvals are satisfied.

#### Media library and previews

Target capabilities:

- private ESP member media library;
- folders and collections;
- campaign media;
- aspect-ratio preparation;
- cover selection;
- platform-native preview frames;
- Instagram-style feed planning;
- reusable approved assets;
- links to relevant creative outputs when the same user has access to both systems.

Linking an asset from the Creative Studio does **not** merge the products or expose ESP tools publicly. It is a controlled asset handoff available only to the authorised member.

#### Analytics and reporting

Target capabilities:

- account-level trends;
- post-level performance;
- LIVE/social campaign comparisons where official data permits;
- follower/audience trends;
- reach/impressions/views;
- engagement;
- watch-time/retention where available;
- conversion/CTA tracking where available;
- campaign reporting;
- exported reports;
- Aura summaries and next-action recommendations.

Aura should interpret performance through the member's selected ESP niche rather than giving generic advice.

#### Social inbox / community management

Where official APIs and permissions permit:

- comments;
- messages/DMs;
- mentions;
- reply queues;
- assigned conversations;
- moderation status;
- saved response guidance;
- escalation to mentor/agent where appropriate.

No feature should claim universal inbox support because platform APIs differ significantly.

#### Aura niche intelligence

Aura's ESP social role is broader than a generic caption generator. The target is a niche-aware creator coach that can:

- build a content strategy from the ESP niche profile;
- create a weekly/monthly calendar;
- identify calendar gaps;
- turn one idea into multiple platform variants;
- draft captions/CTAs in the creator's established voice;
- build content-pillar rotations;
- recommend LIVE-to-short-form repurposing;
- create campaign tasks;
- explain why a piece of content is recommended;
- interpret authorised performance data;
- update recommendations as results change;
- respect ESP training and professional standards.

## Separation from Creative Studio

There must be no public Social House navigation inside the general Creative Studio.

The canonical private paths are:

- `/command-center` — ESP Creator & Agent Hub;
- `/command-center/niche` — ESP Niche Select;
- `/command-center/social` — ESP Social Management;
- `/command-center/api/social/...` — private social-management API.

The old public-style `/social-house` route is intentionally removed.

## Current implementation status

Implemented foundation:

- persistent ESP niche profiles;
- niche-specific themes and training priorities;
- first-entry niche selection for active ESP members;
- explicit Creator Network affiliation attestation;
- server-side no-poaching access gate;
- ESP-only Social Management portal path;
- ESP-only Social Management API path;
- multi-Social-House storage;
- Persona model;
- platform variants;
- projects/campaigns;
- tasks;
- notes;
- content statuses;
- approval state;
- publishing-readiness foundation;
- connection capability state;
- per-member isolated social storage;
- rights/provenance-aware private social media library foundation;
- production publish-queue foundation;
- timezone-aware due-time evaluation;
- planned/blocked/queued/failed retry state handling;
- adapter/OAuth-secret readiness gates;
- provider-confirmed publication guard requiring an external provider post ID;
- client-side protection against injecting provider-managed published state.

Still requires live provider integration before it can truthfully claim end-to-end publishing/analytics/inbox operation:

- platform OAuth applications;
- official publishing adapters;
- trusted background scheduler/worker execution that consumes queued items;
- platform webhook/event ingestion where available;
- analytics ingestion;
- social inbox adapters;
- media-upload adapters;
- richer approval collaboration;
- full Aura social-strategy execution layer.

## Non-negotiable product principle

ESP social tooling exists to train, support and help **ESP's own approved creators and agents** operate professionally. It must not be marketed or technically exposed as a general social-media-management product for creators in other Creator Networks.
