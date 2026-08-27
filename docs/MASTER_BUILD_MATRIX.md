# Elevate Souls Productions Content Creation Command Center — Master Build & Integration Control Matrix

**Master brand:** Elevate Souls Productions Content Creation Command Center  
**Endorsement:** Powered by Aura AI  
**Tagline:** Elevate Your Soul Through Purposeful Media  
**Primary integration branch:** `development/full-site-build`  
**Primary integration PR:** #25  
**Control-room branch:** `integration/control-room-v2`  
**Authoritative specification library:** Google Drive folder `11Ik_76RtVjMb9iWYNueCC7t6HOXZva5x`

This document is the release-control source of truth for the four-chat build. Google Drive defines the intended product specification; GitHub defines what is actually implemented and validated. Nothing is counted as complete merely because it is specified.

## Status legend

- **INTEGRATED** — present on `development/full-site-build` and part of the integrated application path.
- **ACTIVE PR** — implemented on a feature branch and under integration review.
- **PARTIAL** — meaningful implementation exists but the production workflow is incomplete.
- **EXTERNAL / INFRASTRUCTURE** — requires provider credentials, native binaries, GPU capacity, signing, independent testing or other infrastructure outside ordinary site code.
- **RELEASE GATE** — must be resolved before PR #25 can leave draft state or merge to `main`.

## Current baseline — 27 Aug 2026

- `development/full-site-build` head at this control-room refresh: `ce3dea1f835ea59c367e529d3ca54cc27b66162f`.
- PR #25 is open, draft and mergeable.
- PR #25 contains 349 commits, 223 changed files, 40,841 additions and 436 deletions relative to `main` at this snapshot.
- Current conservative programme estimate: **~80% implemented / ~70% production-ready**.
- Current repository visibility is **public**. If unreleased source is intended to remain proprietary/private, changing repository visibility remains a P0 release-control action.
- Vercel/production deployment remains intentionally gated during coordinated development.

## Four-chat ownership model

| Chat | Authoritative scope | Canonical branch | Current control-room state |
|---|---|---|---|
| Chat 1 — Core Platform + Aura | accounts, auth, sessions, subscriptions, payments, shared architecture, Aura Core, full 3D Aura, provider/runtime foundations | `feature/aura-platform-core` | Synced to current integration head at this refresh |
| Chat 2 — ESP Systems | Creator Hub, Agent Hub, Owner/Admin, Academy, creator intelligence, recruitment/CRM, Shop, social operations, finance/oversight | `feature/esp-command-centers` | Synced to current integration head at this refresh |
| Chat 3 — Creative Studios | Music, DAW, Voice, Image/Poster, Video editor/generation, Social creative tools, Game Forge, project/version editing | `feature/creative-studios-game-forge` | Synced to current integration head at this refresh |
| Chat 4 — Aura Sec | security centre, security control plane, native-agent protocol, device/vulnerability/recovery workflows, separately purchased security product | `feature/aura-sec` | PR #59 retargeted to integration; branch requires rebase/clean port before merge |

## Integration rules

1. `main` is the release branch, not a concurrent-development target.
2. All four chats integrate through `development/full-site-build`.
3. A feature branch that is behind integration must be rebased or cleanly ported before merge.
4. Shared high-conflict files — especially `app.py`, auth/session stores, subscriptions, billing, role models, project/version stores, dependency configuration, `vercel.json` and CI — require explicit integration review.
5. UI hiding never substitutes for server-side permission enforcement.
6. ESP Creator, Agent and Owner/Admin permissions remain independent of normal Free/Basic/Pro subscription entitlements.
7. Creative tools reuse shared accounts, project/version/media/audit services rather than creating isolated silos.
8. Aura actions that mutate user data require the same authorization boundary as the underlying feature.
9. Aura Sec browser approval must never be treated as native endpoint execution.
10. CI + Security Gates + integration tests + deployment-readiness checks must pass before release promotion.

# Programme status

## A. Brand, shell and navigation

| Capability | Status | Remaining gate |
|---|---|---|
| Command Center public identity | INTEGRATED | Continue removal of retired public copy without renaming risky internal identifiers |
| Cosmic black/gold/violet/magenta visual shell | INTEGRATED | Responsive/mobile visual QA |
| User-supplied Command Center artwork | INTEGRATED / CLEANUP | Current site has branded WebP/SVG assets; stale PR #70 still contains additional canonical-artwork and pre-inference Aura branding fixes that require clean-port review |
| Aura public identity | PARTIAL | Ensure model system prompt receives current brand before inference on every Aura path |
| Legacy product names | COMPATIBILITY ONLY | May remain in package/database/deployment identifiers where migration risk exists; not allowed as current public branding |
| Responsive navigation | PARTIAL | Device matrix QA |
| Accessibility | PARTIAL | Keyboard, focus, semantics, contrast, reduced motion and screen-reader release sweep |

## B. Accounts, authentication and privacy

| Capability | Status | Remaining gate |
|---|---|---|
| Shared account system | INTEGRATED | Production data-store configuration |
| Argon2id-first passwords + legacy migration | INTEGRATED | Ongoing security regression coverage |
| Login throttling | INTEGRATED | Production observability |
| Password recovery | INTEGRATED | Production mail configuration |
| Verified-email activation | INTEGRATED | Production mail/delivery monitoring |
| Session inventory + revocation | INTEGRATED | Final cross-device QA |
| Session-bound CSRF for destructive member actions | INTEGRATED | Continuous regression gate |
| Owner opaque session authorization | INTEGRATED | Final owner-only route sweep |
| Owner MFA/passkeys | INTEGRATED / PARTIAL | Recovery/support policy and final production validation |
| Personal-data export/delete | PARTIAL | Full retention/deletion policy and end-to-end privacy QA |
| Secret scanning | INTEGRATED | Provider-side secret rotation procedures remain operational policy |

## C. Billing, subscriptions, credits and finance

| Capability | Status | Remaining gate |
|---|---|---|
| Free / Basic £4.99 / Pro £9.99 baseline | INTEGRATED | Final commercial packaging review |
| Server-side entitlements | INTEGRATED | Full matrix regression tests |
| Pulsar/Command Center credit ledger | INTEGRATED | Naming cleanup can remain compatibility-safe internally |
| Stripe/PayPal verified payment evidence foundations | INTEGRATED | Production provider credentials/configuration |
| Credit top-up processing | INTEGRATED / PARTIAL | Reconciliation/refund/chargeback operational flows |
| Owner finance dashboard | ACTIVE PR (#83) | Branch materially behind integration; rebase before merge and run complete CI/Security Gates |
| Cancellation/refund/reconciliation lifecycle | PARTIAL | Production provider-specific end-to-end testing |
| Taxes/accounting exports | PARTIAL | Jurisdiction/accounting review |

## D. Aura Core and universal workspace

| Capability | Status | Remaining gate |
|---|---|---|
| Persistent Aura conversations | INTEGRATED | Production model credentials/routing |
| Fast/Auto/Deep/Creative reasoning modes | INTEGRATED | Cost/latency tuning |
| Project-aware context | INTEGRATED | Cross-media coverage QA |
| Ask / Plan / Agent work modes | INTEGRATED | Broader tool coverage and long-running workflow QA |
| Attachments and project knowledge | INTEGRATED | Large-file/retention production policies |
| Artifacts/tasks/notifications foundations | INTEGRATED | UX and delivery integration |
| Voice conversation | PARTIAL | True low-latency duplex STT/TTS provider integration |
| Multilingual/translation | PARTIAL | Production STT/TTS and locale QA |
| Web/connected-service research architecture | PARTIAL | Provider/connectors and permission boundaries |
| Exact cross-media conversational editing | PARTIAL | Complete command coverage across every editor |

## E. Full 3D Aura companion

| Capability | Status | Remaining gate |
|---|---|---|
| Browser 3D runtime | INTEGRATED | Production asset and performance tuning |
| Aura state/energy system | INTEGRATED | Final model material mapping |
| Lip movement / embodied state hooks | INTEGRATED / PARTIAL | Production realtime voice coupling |
| Production GLB/VRM validation pipeline | INTEGRATED | Final DCC/GPU output |
| Final likeness-grade Aura model | EXTERNAL / INFRASTRUCTURE | Reconstruction, facial rig, animation, manual visual approval |
| Full facial morph coverage | PARTIAL / EXTERNAL | Final authored model |
| Authored gestures/animations | PARTIAL / EXTERNAL | Final production asset |
| Mobile-optimized 3D packaging | PARTIAL | KTX2/Meshopt/final device testing |

## F. Shared Creative Project system

| Capability | Status | Remaining gate |
|---|---|---|
| Creative projects + workspace | INTEGRATED | Final UX consolidation |
| Creative Library | INTEGRATED | Storage/retention policy |
| Version history | INTEGRATED / PARTIAL | First-class visual history UX |
| Non-destructive editor operations | INTEGRATED | Continue regression coverage |
| Cross-media continuity | PARTIAL | Unified first-class song → artwork → video → social timeline |
| Semantic version mixing | PARTIAL | User-facing operation such as vocals V4 + chorus V7 |
| Reference media | INTEGRATED / PARTIAL | Ensure every generator consumes the same project reference model consistently |

## G. Music generation and DAW

| Capability | Status | Remaining gate |
|---|---|---|
| Prompt/lyrics/reference-to-song foundations | INTEGRATED | Production GPU/model capacity |
| ACE-Step / YuE/self-host adapters | INTEGRATED / EXTERNAL | Production compute, model licences and health monitoring |
| Song DNA | INTEGRATED | UX polish |
| Section regeneration | INTEGRATED | Real-renderer end-to-end validation |
| Lyrics alignment | INTEGRATED | Production performance QA |
| Recording/takes | INTEGRATED | Browser/device QA |
| MIDI/piano roll/quantise/transpose/CC/pitch bend | INTEGRATED | Richer automation lanes and synthesis depth |
| Mixer/routing | INTEGRATED | Professional performance/latency testing |
| Stem workflows | PARTIAL | Real model separation/reconstruction validation |
| Mixing/mastering | PARTIAL | End-to-end real-audio production QA |
| Word/phoneme-level melody-preserving vocal edits | PARTIAL | Renderer/model capability and professional UX |
| Advanced instrument reconstruction/replacement | PARTIAL | Production model pipeline |

## H. Voice House

| Capability | Status | Remaining gate |
|---|---|---|
| Voice asset/workflow APIs | INTEGRATED | Final UX |
| Consent/ownership records | INTEGRATED / CONTINUOUS GATE | Never weaken consent boundaries |
| Voice cloning | PARTIAL / EXTERNAL | Production singing/speech model integration |
| Singing voice conversion | PARTIAL / EXTERNAL | Renderer capacity, quality and abuse testing |
| Realtime voice conversion | EXTERNAL / PARTIAL | Low-latency production engine |

## I. Image & Poster Studio

| Capability | Status | Remaining gate |
|---|---|---|
| AI image/poster generation | INTEGRATED | Production provider/self-host capacity |
| Quotas/downloads | INTEGRATED | Cost policy review |
| Shared-project references | INTEGRATED / PARTIAL | Generator consistency |
| Layered professional editing | INTEGRATED / PARTIAL | Complete advanced editor lifecycle |
| Masks/effects/keyframes lifecycle | ACTIVE PR (#90) | Rebase from latest integration and complete CI; Security Gates already green at first snapshot |
| Inpainting/outpainting | PARTIAL | Production adapter + editor QA |
| Object/background replacement | PARTIAL | Final editor integration |
| Upscaling/restoration | PARTIAL | Production adapter validation |
| Brand kits/templates/text layout | PARTIAL | Professional template/editor depth |

## J. Video Studio

| Capability | Status | Remaining gate |
|---|---|---|
| Text/image/audio/song-to-video architecture | INTEGRATED / PARTIAL | Production neural-video compute/providers |
| Music Video Director | INTEGRATED / PARTIAL | Final end-user workflow polish |
| Lyric video / visualizer pipeline | INTEGRATED | Export/performance QA |
| Professional multitrack edit graph | INTEGRATED / PARTIAL | Continue renderer parity with editor state |
| Text/reverse/transform keyframes/speed-aware audio rendering | INTEGRATED or latest Creative increment | Keep regression-tested as editor grows |
| Masks/cutouts | PARTIAL | Renderer coverage |
| Effects/blend modes | PARTIAL | Renderer coverage |
| Motion/camera/planar tracking | PARTIAL / PLANNED | Professional implementation |
| Colour grading | PARTIAL | Full controls/scopes/export verification |
| Captions/beat/lyric sync | PARTIAL | UX/export QA |
| Digital twin / persistent character continuity | PARTIAL / EXTERNAL | Approved model provider/consent and continuity testing |
| Long-form scene continuity | PARTIAL | Production generation orchestration |

## K. Game Forge

| Capability | Status | Remaining gate |
|---|---|---|
| Game DNA / World DNA | INTEGRATED | Editor polish |
| Aura2D/Aura3D runtime | INTEGRATED | Performance/device QA |
| WebGL2 PBR renderer | INTEGRATED | WebGPU/high-end path remains future depth |
| HRTF spatial audio | INTEGRATED | Browser/device QA |
| Verified glTF/GLB ingestion | INTEGRATED | Broader asset authoring workflow |
| Asset provenance and integrity gates | INTEGRATED | Publication/export validation |
| Aura natural-language game tools | INTEGRATED | Broader tool coverage |
| Cinematics/accessibility/readiness metrics | INTEGRATED / PARTIAL | Final editor and production QA |
| Physics/destruction/IK/VFX/NPC/multiplayer/AAA depth | PARTIAL / PLANNED | Major future Game Forge depth; do not call AAA-complete yet |
| Full browser game editor UX | PARTIAL | Professional editor polish |

## L. ESP Creator Hub

| Capability | Status | Remaining gate |
|---|---|---|
| Private Creator access | INTEGRATED | Continuous role-isolation tests |
| Creator dashboard | INTEGRATED | Mobile/UX QA |
| Niche Discovery Lab | INTEGRATED | Expand approved niche content |
| LIVE Show Planner | INTEGRATED | UX/content QA |
| Creator Progress Intelligence | INTEGRATED | Continue evidence-grounded restraint |
| CSV/JSON/XLSX analytics import | INTEGRATED | Export-format compatibility |
| Backstage screenshot analysis | INTEGRATED | Human confirmation remains mandatory |
| Academy/training | INTEGRATED / PARTIAL | Verify complete Drive curriculum coverage |
| Incentives/rewards | PARTIAL | Rule-by-rule configuration against current ESP programmes |
| Collaborations/events | PARTIAL | Operational booking workflow |
| Brand/commercial opportunities | INTEGRATED / PARTIAL | Campaign lifecycle QA |
| Creator Shop | INTEGRATED / PARTIAL | Production provider credentials and complete operational UX |
| Wellbeing/safety resources | PARTIAL | Final native resource centre |

## M. ESP Agent Hub

| Capability | Status | Remaining gate |
|---|---|---|
| Private Agent access | INTEGRATED | Continuous role isolation |
| Creator ↔ Agent switch | INTEGRATED | Mobile UX QA |
| Assigned creator portfolio | INTEGRATED | Final usability |
| Creator 360 records | INTEGRATED / PARTIAL | Consolidated timeline UX |
| Creator Discovery CRM | INTEGRATED | Continue provenance/dedupe/no-poaching controls |
| Recruitment/follow-up pipeline | INTEGRATED | Provider-compliant outreach boundaries |
| Agent Recruitment Academy | INTEGRATED | Content/version QA |
| Creator development plans | INTEGRATED | Final UX |
| Agent performance/compensation review | INTEGRATED | Owner-configured policy/data QA |
| Creator reports/exports | INTEGRATED | Privacy/export tests |
| Creator Health Queue | PARTIAL | Unified triage experience |
| Technical/compliance escalation | PARTIAL | Complete specialist workflow |

## N. Mary & Kev Owner/Admin

| Capability | Status | Remaining gate |
|---|---|---|
| Owner identity selection | INTEGRATED | Final UX |
| Owner-only session auth | INTEGRATED | Complete route sweep |
| User/role administration | INTEGRATED | Final audit/reason workflows |
| Creator/Agent oversight | INTEGRATED | Dashboard polish |
| Operations intelligence | INTEGRATED | Additional cross-system reporting |
| Training oversight | INTEGRATED / PARTIAL | Complete content-management tooling |
| Finance overview | ACTIVE PR (#83) | Rebase + CI before merge |
| AI/provider cost analytics | PARTIAL | Production provider cost feeds |
| Feature flags/maintenance controls | PARTIAL | Consolidate owner UX |
| Security command centre | PARTIAL | Integrate Aura Sec owner-level operational visibility without bypassing member/privacy boundaries |

## O. Social Management

| Capability | Status | Remaining gate |
|---|---|---|
| Social workspaces | INTEGRATED | UX polish |
| OAuth/provider connections | INTEGRATED | Production credentials/app approvals |
| Publish queue | INTEGRATED | Platform-specific production validation |
| YouTube/TikTok analytics foundations | INTEGRATED | Provider limits and permission QA |
| Content calendars/tasks/approvals | INTEGRATED / PARTIAL | Rella-class workflow polish |
| External review links | INTEGRATED / PARTIAL | Permission/security QA |
| Social creative launch bridge | INTEGRATED | Cross-media UX |
| Unified inbox | EXTERNAL / PARTIAL | Only where official APIs permit it |
| Cross-platform campaign analytics | PARTIAL | Provider data normalization |
| Aura social strategy | INTEGRATED / PARTIAL | Evidence-grounded recommendations |

## P. ESP Shop / commerce operations

| Capability | Status | Remaining gate |
|---|---|---|
| Provider runtime/safety envelope | INTEGRATED | Production adapters/credentials |
| Shopify OAuth/HMAC/token refresh | INTEGRATED | Production secret backend |
| Shopify GraphQL foundation | INTEGRATED | Live provider validation |
| Async shipping-label reconciliation | INTEGRATED | Production provider testing |
| TikTok Shop adapter | PARTIAL / EXTERNAL | Official provider/API access |
| Shippo/ShipStation adapters | PARTIAL / EXTERNAL | Production integration |
| Approval/spend controls | INTEGRATED | Final owner/creator UX |

## Q. Aura Sec — Chat 4

| Capability | Status | Remaining gate |
|---|---|---|
| Member Security Center | ACTIVE PR (#59) | Rebase/clean port onto current integration |
| Device/enrolment/read models | ACTIVE PR | Native verified transport |
| Incident/threat workflow | ACTIVE PR | Native detection evidence |
| Vulnerability inventory/prioritisation | ACTIVE PR | Production threat feeds / native findings |
| Recovery evidence/readiness | ACTIVE PR | Native backup/restore implementation |
| Safe optimizer | ACTIVE PR | Native filesystem/device implementation |
| Approval gateway + WebAuthn step-up | ACTIVE PR | Production lifecycle/recovery/support policy |
| Native command bridge protocol | ACTIVE PR | Signed native clients |
| Separate security licence/SKU model | ACTIVE PR | Configured commercial SKU + verified checkout |
| Signed release-manifest contract | ACTIVE PR | Real platform signing/notarisation |
| Windows/macOS/Linux native clients | EXTERNAL / MAJOR BUILD | Privileged native endpoint implementation |
| Android/iOS clients | EXTERNAL / MAJOR BUILD | Platform-native implementation |
| Browser extension/web protection | PARTIAL / EXTERNAL | Production browser integrations |
| Malware/phishing/ransomware detection engine | EXTERNAL / MAJOR BUILD | Licensed/open detection components, rules, benchmarking |
| Threat intelligence feeds | EXTERNAL | Licensing, ingestion, provenance and update operations |
| Secure updater | EXTERNAL / MAJOR BUILD | Signed update channel and rollback/recovery |
| Device attestation | EXTERNAL / MAJOR BUILD | Platform verification transport |
| Independent penetration/malware benchmarking | RELEASE GATE | Third-party validation before security claims |

# Active integration hazards

## P0 — Repository visibility
GitHub currently reports this repository as public. If the intended policy remains private during development, change repository visibility in GitHub settings. Do not place production signing keys, malware samples, commercial security feeds, private recovery material, provider secrets or other sensitive production material in this repository regardless of visibility.

## P0 — Aura Sec branch divergence
Before PR #59 was retargeted, the branch was 99 commits ahead and 344 commits behind the integration branch. It now correctly targets `development/full-site-build`, but must remain draft until Chat 4 rebases or cleanly ports the work and reruns the complete Command Center CI + Security Gates.

## P1 — Creative editor PR #90
At the first control-room snapshot after opening, PR #90 was 3 commits ahead and 5 behind integration. Security Gates passed while main CI was still running. Rebase/refresh before merge if integration continues moving.

## P1 — Owner finance PR #83
At this refresh, the finance branch is 11 commits ahead and 67 behind integration. Rebase before merge. No direct merge while stale.

## P1 — Branding PR #70
PR #70 is 6 commits ahead and 120 behind integration. It overlaps already-merged visual-shell work but still contains potentially useful corrections: canonical master-artwork routing, legacy logo interception and Aura pre-inference brand migration. Do not merge the stale PR directly; clean-port only the missing verified fixes onto a current branch, then close #70 as superseded.

## P2 — Old control/deployment/social PRs
PRs #30, #36 and #23 are materially stale. Review whether their useful changes are already present or clean-port the exact remaining deltas. Do not merge them directly solely because they are open.

# Production infrastructure / release engineering

| Gate | Status | Required outcome |
|---|---|---|
| Durable production database/storage | PARTIAL | Tested migrations, backups, retention and restore procedures |
| GPU inference capacity | EXTERNAL | Capacity for music/video/image/voice generation with queues and cost controls |
| Job queues/retries/idempotency | PARTIAL | No duplicate paid work; recoverable long-running jobs |
| Observability | PARTIAL | Metrics, logs, traces, provider health, queue health, cost alerts |
| Backup/restore | INTEGRATED / PARTIAL | Successful restore drills with documented RTO/RPO |
| Load testing | RELEASE GATE | Auth, project, upload, generation queue and export workloads |
| Cross-tenant security testing | RELEASE GATE | No user/Creator/Agent/Owner data leakage |
| Penetration testing | RELEASE GATE | Web application + Aura agent boundaries + Aura Sec control plane |
| Mobile/responsive QA | RELEASE GATE | Major Android/iOS/desktop browser matrix |
| Accessibility QA | RELEASE GATE | Keyboard, screen reader, contrast, semantics, reduced motion |
| Provider credentials/app reviews | EXTERNAL | Stripe/PayPal/social/AI/commerce providers configured only through secret stores |
| Production mail | EXTERNAL | Verified delivery, bounce handling and rate controls |
| Staging environment | RELEASE GATE | Production-like staging with no real-user impact |
| Final production deployment | BLOCKED | Only after PR #25 release definition is satisfied |

# Definition of done

The complete site is not considered finished until all of the following are true:

1. Every required capability in the authoritative Drive specification library is either implemented or explicitly deferred to a named post-launch roadmap.
2. All four chat branches are integrated cleanly with no stale direct-to-main PR path.
3. PR #25 has full green Command Center CI and Security Gates on its final head.
4. Authentication, privacy, billing, Creator/Agent/Owner role isolation and Aura action authorization pass end-to-end tests.
5. Creative exports are real production media; symbolic/demo outputs can never be presented as final masters.
6. Production model/provider credentials and infrastructure are configured outside source control.
7. Full 3D Aura has a production-approved model, voice integration and device-performance validation.
8. Aura Sec has signed native releases and independent security validation before any claim of endpoint protection.
9. Backup and disaster-recovery restore drills pass.
10. Load/performance, mobile, accessibility and penetration testing pass.
11. Monitoring, alerts, incident response, support and rollback procedures are operational.
12. Repository/deployment visibility and branch protections match release policy.
13. Only then may PR #25 leave draft state and proceed toward `main`.

# Current control-room verdict

**Implementation:** ~80%  
**Production readiness:** ~70%  
**Integrated CI/security:** generally healthy, but the current integration head and active PRs must complete their current runs before promotion.  
**Release state:** **NOT READY FOR MAIN**. Remaining work is concentrated in deep editor parity, full 3D Aura production assets/voice, native Aura Sec engineering, provider infrastructure, branch reconciliation and final release validation.
