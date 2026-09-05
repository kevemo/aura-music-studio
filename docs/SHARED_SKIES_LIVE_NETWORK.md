# Shared Skies Streaming Studio — LIVE Network & Multistream Expansion

## Canonical product name

**Shared Skies Streaming Studio** is the multistreaming, collaborative LIVE and live-commerce/engagement product inside the **Elevate Souls Productions Content Creation Command Center**, powered by Aura AI.

Legacy code/database identifiers may continue to use `shared_sky_*` where migration risk makes renaming unsafe. Public UI, documentation and new APIs should use **Shared Skies Streaming Studio**.

## Product goal

Shared Skies is designed as a first-party LIVE destination as well as a production/multistream studio. A creator can create on the Command Center while broadcasting that creation process to Shared Skies and to authorised external destinations at the same time.

The private ESP Owner, ESP Agent and ESP Creator operational pages are excluded from Go Live & Create. Those areas contain organisational, mentoring, commercial or administrative data and must not be accidentally broadcast.

## First-party LIVE network

### Live Now

The public `/live-now` surface is the discovery feed for active Shared Skies broadcasts. Ranking can evolve from a simple live/viewer/gift ordering into a recommendation model using viewer preference, category, language, safety state, freshness and creator-follow relationships.

Required viewer interactions:

- watch LIVE;
- like;
- share;
- follow/subscribe when account/follow systems are enabled;
- first-party chat;
- Cosmic Creation Coin gifts;
- open authorised external-platform chat links where a provider does not expose an API;
- see battle/co-host state;
- jump to the creator's published music, games, videos or store items.

### Cosmic Creation Coin LIVE gifts

Viewers can send server-authoritative Cosmic Creation Coin gifts to a LIVE host. The gift transaction must debit the viewer and credit the recipient through the platform ledger; a client-side animation is never evidence that value moved.

Gift catalogue metadata should support:

- stable gift id;
- display name;
- Coin cost;
- animation/effect asset;
- rarity/tier;
- seasonal availability;
- battle scoring eligibility;
- safety/accessibility fallback;
- moderation status;
- versioning so old transaction records remain auditable.

Coin gifts are platform units unless/until a separately reviewed creator-cashout policy is introduced. Cashout, tax, AML/KYC, regional restrictions and payment-services obligations must not be implied by the internal Coin ledger.

### Battles and co-hosting

Shared Skies supports up to **8 simultaneous co-hosts** in the product model. Battle modes are designed for solo and team competition. Scoring modes can count likes, Cosmic Creation Coin gifts, or both. Future rule sets can add selected-gift challenges, timed rounds, streaks and goal bonuses without changing the ledger source of truth.

Production guest media requires a real WebRTC/SFU (or equivalent authorised media architecture), TURN capacity, admission controls, moderation controls, echo cancellation, active-speaker handling, quality adaptation and disconnect/rejoin behaviour. The API/state model does not pretend that a missing media plane exists.

### Chat and cross-platform chat

Shared Skies owns its first-party chat. External chat is capability-driven:

- if an external provider officially exposes read access, Shared Skies may display authorised incoming messages;
- if reply is officially permitted, the host may reply from the unified console;
- relay/mirroring is enabled only where provider terms and APIs permit it;
- when no authorised chat API exists, Shared Skies presents a safe external-chat link rather than scraping or claiming integration.

The interface should visually label each message with its source platform and distinguish first-party Shared Skies messages from external messages.

## Go Live & Create

The build exposes a common Go Live & Create capability for eligible creative surfaces:

- Music / Professional DAW;
- Video / Professional Video Editor;
- Image / Poster Studio;
- Game Forge;
- Book / Writing Studio;
- Voice House;
- Shared Skies production workspace.

The creator chooses a project, title, privacy, destinations, layout and microphone/camera/screen/game sources, then starts a Shared Skies session and the configured multistream relay. The live session retains a project reference so the finished creation can be published or sold after the broadcast.

The Go Live control must not be injected into Mary/Kev owner surfaces, ESP Agent Hub, ESP Creator Hub or other private organisational pages.

## Multistream production architecture

The existing Shared Sky relay, vault, scheduler and destination registry remain the outbound foundation. The expanded target architecture is:

1. browser/mobile/encoder ingest;
2. WebRTC contribution for host and guests;
3. ingest gateway and SFU/media router;
4. compositing/scene engine when server-side composition is required;
5. recording and isolated-track capture;
6. ABR/transcode ladder where required;
7. first-party Shared Skies playback/CDN output;
8. outbound RTMP/RTMPS/SRT/authorised-provider destinations;
9. unified engagement event bus;
10. chat adapters with explicit capability flags;
11. analytics, health, recording, clipping and post-LIVE repurposing.

Landscape and portrait outputs should be independently framed rather than naïvely cropping a single canvas. A dual-output project can maintain separate safe zones, overlays and scene variants while sharing the same source graph.

## Competitive reference findings

The design intentionally learns from established public LIVE patterns without copying branding or proprietary assets.

TikTok documents co-host LIVE and LIVE Match, where creators compete individually or in teams and likes/Gifts contribute points. Shared Skies adopts the general interaction pattern while using its own first-party Coin ledger, eight-person co-host ceiling and independently designed battle rules/UI.

YouTube documents live chat, fan-funding/gifts and both horizontal and vertical live experiences. Shared Skies therefore treats aspect ratio, discovery, gifts and chat as first-class but separable LIVE capabilities.

Twitch Stream Together demonstrates the value of browser-based guest collaboration and allowing collaborators to participate across their own channels. Shared Skies extends the concept into the multistream destination graph and its own first-party LIVE network.

StreamYard publicly supports guest studio workflows with up to ten participants and multistreaming. Shared Skies targets eight active co-hosts for the battle/live-room model, with additional backstage/green-room capacity able to be designed separately.

Restream documents unified cross-platform chat with read, reply and relay capabilities varying by provider. Shared Skies uses the same capability-aware principle: never show a control that the destination has not officially authorised.

## Advanced feature target

To compete at the top of the category, the Shared Skies roadmap includes:

- Live Now personalised and category feeds;
- 8-way co-host and team battles;
- green room and producer controls;
- scene collections and nested scenes;
- simultaneous portrait + landscape production;
- first-party gifts, goals, leaderboards and supporter streaks;
- unified chat with per-provider capability matrix;
- moderation queues, automod and moderator roles;
- live polls, Q&A, quizzes and audience challenges;
- alerts and browser-source overlays;
- multiview and confidence monitoring;
- isolated local/cloud recordings;
- instant replay and markers;
- automatic clips/highlights;
- captions, translation and accessibility modes;
- low-latency first-party playback;
- remote mobile cameras;
- NDI/SRT/RTMP/RTMPS ingest/output;
- stream health and destination-specific diagnostics;
- resilient failover and reconnect;
- scheduled and pre-recorded LIVE;
- collaborative producers/editors with scoped roles;
- brand kits, templates and marketplace assets;
- AI-assisted scene direction, clipping, titles, chapters and post-LIVE repurposing;
- creator storefront/published creation links in LIVE;
- game capture and Game Forge playtesting LIVE;
- DAW mix/master sessions LIVE;
- video-editing and film-production sessions LIVE;
- published game/music/video launch events;
- consent/provenance indicators for generated or likeness-based media;
- first-party analytics across Shared Skies plus authorised external providers.

## Security and provider boundaries

No feature may obtain external-platform credentials by scraping, browser theft or undocumented private APIs. OAuth tokens and stream keys remain encrypted/secret-backed and tenant scoped. The UI must disclose when a destination is using custom RTMP rather than an official API integration.

External platform chat, moderation, gifting, follower/subscriber information and analytics are enabled only where the external platform has granted the necessary access. Shared Skies first-party gifts and battles do not depend on TikTok, YouTube, Twitch or another provider.

## Implementation state added by this feature branch

The branch `feature/shared-skies-live-network` adds the first-party foundation for:

- `/live-now` discovery;
- LIVE session registry/start/stop;
- likes, shares and viewer counters;
- Cosmic Creation Coin LIVE gifting;
- battle rooms with up to 8 LIVE sessions;
- solo/team battle state and likes/gifts scoring;
- capability-gated external chat links;
- Go Live & Create discovery for Music, Video, Image, Game Forge, Book, Voice and Streaming surfaces;
- canonical public naming as **Shared Skies Streaming Studio** in the new work.

This is application/state foundation, not evidence that WebRTC/SFU capacity, external provider approvals, CDN playback, real destination adapters or production moderation infrastructure have already been provisioned.
