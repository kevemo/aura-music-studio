# Pulsar-Frequency House — Social Media Management Expansion

## Purpose

Pulsar-Frequency House will include a first-class Social Media Management House alongside music, video, image, voice, Aura and the private ESP Creator Network Hub.

The feature direction is informed by public research into modern social-media management products, including Rella, but the implementation, data model, interface, branding and workflows must remain independently designed for Pulsar-Frequency House.

The goal is not to clone another product. The goal is feature-class parity and then to go beyond it by connecting social planning directly to Pulsar-Frequency House's own music, video, image, voice, creator-network and Aura systems.

## Publicly observed Rella feature classes researched in August 2026

Rella publicly describes itself as an all-in-one workspace combining social scheduling, project/task management, analytics, AI assistance and content approvals. Its support material also documents multi-brand workspaces, calendar/board/table views, media libraries, feed previews, approvals, social-account connection, automated publishing, analytics reports, project/task/notes systems, an Instagram feed planner, AI-assisted planning and repurposing, integrations, and a social inbox.

### 1. Multi-brand / multi-client workspaces

Comparable Pulsar-Frequency implementation:

- Social Houses: isolated workspaces per creator, artist, business, client, campaign brand or ESP-managed account.
- independent connected social accounts per Social House;
- independent brand persona, media library, hashtags, content pillars, statuses and workflow rules;
- independent analytics and reports;
- independent approval links and collaborators;
- per-space roles and permissions;
- fast account/brand switching from one master dashboard.

### 2. Content planning

Implement:

- calendar view;
- Kanban/board view;
- table/list view;
- unscheduled-content queue;
- drafts;
- scheduled posts;
- recurring content;
- content campaigns/projects;
- content pillars and tags;
- custom statuses;
- assignees;
- due dates;
- notes;
- post-level tasks/checklists;
- reusable task templates;
- all-day placeholders;
- drag-and-drop rescheduling;
- bulk actions;
- duplicated posts/campaigns;
- platform-specific variants.

### 3. Supported social destinations

Plan and compatibility-check content for:

- TikTok video and photo posts;
- Instagram posts, Reels and Stories;
- Facebook posts, Reels and Stories;
- YouTube long-form and Shorts;
- LinkedIn personal and organization content;
- Pinterest;
- Threads;
- X;
- podcasts;
- Google Business Profile;
- custom planning-only platforms;
- future platforms through an adapter registry.

Platform constraints must be stored as data and updated independently of the UI. The system should validate caption limits, media counts, aspect ratios, duration, file formats and platform-specific publishing requirements before a post can enter an auto-publish queue.

### 4. Scheduling and auto-publishing

Implement an official-API publishing adapter layer with truthful capability reporting.

A post can be:

- idea;
- draft;
- in production;
- pending approval;
- approved;
- scheduled;
- publishing;
- published;
- failed;
- archived.

The scheduler must:

- support exact time and timezone;
- support all-day placeholders;
- support multi-platform scheduling;
- support different captions/dates/settings by platform;
- enforce approval gates before auto-publishing;
- verify connection/token health before queueing;
- perform platform validation before posting;
- retry safely where the target API permits it;
- never mark content as published until the platform API confirms it;
- preserve a full publishing audit trail.

### 5. Repurposing

One Pulsar-Frequency creative asset can fan out into multiple platform-native posts.

Aura should be able to convert:

- long-form video → Shorts/Reels/TikToks;
- music video → teasers/trailers/stories;
- song → lyric cards, cover art, behind-the-scenes posts and launch campaign;
- livestream recording → highlights, clips, quotes and promotional assets;
- YouTube video → LinkedIn post, Threads post, X post, carousel outline and newsletter copy;
- image/poster → alternate aspect ratios and platform-specific layouts.

Each repurposed variant should remain linked to the parent creative asset while having independent caption, hashtags, schedule, cover, crop and publishing state.

### 6. Media library

Implement a private media library inside every Social House:

- folders;
- campaign folders;
- batch upload;
- search;
- tags;
- favourites;
- media metadata;
- image/video/PDF support;
- generated Pulsar-Frequency assets automatically available without downloading/re-uploading;
- import from authorised cloud storage;
- media previews;
- download/export;
- crop and aspect-ratio transformations;
- version history;
- duplicate detection;
- rights/provenance data;
- usage history so users can see where an asset has been posted.

### 7. Visual feed and platform previews

Implement:

- Instagram grid/feed planner;
- platform-native preview modes;
- TikTok preview;
- Reels/Stories preview;
- YouTube thumbnail/title preview;
- LinkedIn preview;
- X/Threads preview;
- carousel preview;
- mobile/desktop variants where useful;
- drag/reorder feed planning where the platform supports a visual feed concept.

### 8. Collaboration and approvals

Implement:

- internal/private chat;
- external/public feedback thread;
- activity history;
- @mentions;
- assignees;
- notification preferences;
- public review links that do not require an account;
- post-level, project-level and Social-House-level sharing;
- configurable view/download/comment/approve permissions;
- expiring links;
- client asset-upload requests;
- approval status changes;
- approval rules preventing unapproved auto-posting;
- internal comments kept invisible to external reviewers;
- dynamic preview links that reflect the newest approved draft.

### 9. Project and task management

Implement:

- campaign projects;
- task lists;
- task templates;
- recurring tasks;
- due dates;
- priority;
- assignees;
- custom statuses;
- board/table/calendar views;
- notes and briefs;
- dependencies;
- automated status changes;
- roadmaps and launch checklists;
- campaign completeness indicators.

### 10. Analytics and reporting

Implement an analytics data-normalisation layer above official platform APIs.

Dashboard targets:

- views/reach/impressions;
- followers and follower growth;
- engagement;
- comments/shares/saves;
- watch time and retention where available;
- clicks/conversions where available;
- organic vs paid where APIs expose it;
- demographics where authorised and available;
- top-performing content;
- content-type comparisons;
- posting-time analysis;
- platform comparisons;
- campaign/project performance;
- creator/account trends;
- anomaly alerts;
- exportable/shareable reports;
- recurring scheduled reports;
- client-facing branded reporting.

Aura should interpret the metrics rather than merely displaying charts.

### 11. Aura social intelligence

Aura becomes the social-management counterpart to Rella's AI assistant, but integrated with the entire Pulsar-Frequency creative system.

Aura should be able to:

- learn a Social House brand persona;
- preserve tone, banned phrases, preferred vocabulary, CTA style and target audience;
- generate content calendars;
- identify gaps in the schedule;
- recommend content pillars;
- create captions;
- rewrite captions for each platform;
- suggest and evaluate hashtags/keywords;
- summarize uploaded videos and derive post ideas;
- repurpose existing content;
- analyze past performance;
- recommend posting times;
- recommend what content type to create next;
- search current public trends when requested;
- create tasks and campaign projects;
- change statuses and schedules through explicit user commands;
- produce monthly strategies and reports;
- remember brand-specific instructions separately for each Social House;
- use Pulsar-Frequency image/video/music tools to create missing campaign assets.

### 12. Social inbox / community management

Target a unified community inbox where official APIs permit access:

- comments;
- DMs/messages;
- story replies;
- mentions;
- filtering by account/platform/status;
- assignment to team members;
- saved replies;
- labels;
- response-time metrics;
- escalation flags;
- Aura-assisted response drafts;
- spam/safety filtering;
- creator-network escalation paths for ESP accounts.

Each platform must truthfully declare whether its APIs permit reading/replying to each message type.

### 13. Integrations

Target integrations for:

- Google Drive;
- Google Calendar;
- Canva where an approved integration is available;
- cloud asset storage;
- email notifications;
- webhooks;
- an MCP-style tool surface so authorised external AI/operator clients can manage a Social House;
- Pulsar-Frequency House internal Creative DNA projects;
- ESP Creator Network records where the user's role permits it.

### 14. Brand persona / memory

Every Social House receives an isolated Aura Social Persona containing:

- brand name;
- industry/niche;
- audience;
- goals;
- brand voice;
- vocabulary;
- prohibited language;
- content pillars;
- style references;
- CTA rules;
- hashtag/keyword banks;
- visual guidelines;
- creator presentation rules;
- posting cadence;
- platform priorities;
- historic successful content patterns.

### 15. Automations

Implement a workflow engine for events such as:

- when status becomes Approved → make post eligible for publishing;
- when media is missing → create a task;
- when approval is requested → send notification;
- when an approver comments → notify assignee;
- when a scheduled post fails → alert manager;
- when a campaign reaches a date → generate report;
- when analytics fall/rise beyond threshold → ask Aura for analysis;
- when a long-form video enters the library → propose repurposed clips;
- when a Pulsar-Frequency creative project is completed → offer to build a launch campaign.

### 16. Search and global command

Users should be able to search across:

- posts;
- campaigns/projects;
- tasks;
- notes;
- media;
- hashtags;
- tags;
- collaborators;
- analytics reports;
- social connections;
- Aura conversations.

Aura should support command-style operations over the same indexed data.

## Beyond Rella: native Pulsar-Frequency advantages

The social system should be more tightly integrated with creation itself:

1. A finished song can generate its release campaign automatically.
2. Aura can create the artwork, vertical video, teaser, captions, hashtags and schedule from the same project.
3. Every social post can link back to the exact Creative DNA assets that created it.
4. A user can ask Aura to revise the underlying image/video/music and automatically propagate a new approved version into pending posts.
5. ESP creators/agents can have role-specific social-management training and approved agency workflows without exposing ESP-only systems to ordinary subscribers.
6. Analytics can feed directly back into creation: e.g. “short acoustic clips are outperforming full-production teasers; create three new variants.”
7. Content strategy, asset production, scheduling, approvals and reporting therefore live in one product rather than being separate applications.

## Delivery order

### Phase S1 — Social House foundation

- tenant-isolated Social Houses;
- projects/campaigns;
- content cards;
- tasks/notes;
- statuses/tags;
- calendar/board/table data model;
- brand persona;
- internal media references to Pulsar-Frequency assets.

### Phase S2 — collaboration

- team roles;
- assignees;
- comments;
- approvals;
- share links;
- client asset requests;
- activity logs;
- notifications.

### Phase S3 — platform adapters

- OAuth/account links;
- platform capability registry;
- validation;
- scheduling;
- official-API publishing;
- post status/audit history;
- repurposing by platform.

### Phase S4 — analytics

- official API ingest;
- normalized metrics;
- dashboards;
- reports;
- scheduled reporting;
- Aura performance insights.

### Phase S5 — community management

- inbox/comment adapters where permitted;
- assignment;
- response workflows;
- Aura response assistance.

### Phase S6 — advanced Aura automation

- calendar creation;
- workflow automation;
- predictive recommendations;
- trend-assisted planning;
- campaign generation from Creative DNA;
- social strategy that learns from account performance.

## Truthful capability rule

Planning functionality can exist without a linked social account. Auto-posting, analytics, inbox and account-specific functions must only be marked available when the required official integration is configured and authorised.

No UI may claim a post was published, an inbox was read or analytics were imported unless an actual platform/API operation succeeded.
