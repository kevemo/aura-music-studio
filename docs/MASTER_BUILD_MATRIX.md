# Pulsar-Frequency House — Master Build & Integration Control Matrix

**Product:** Pulsar-Frequency House  
**Powered by:** Elevate Souls Productions & Aura AI Systems  
**Tagline:** For Professional Creation Beyond The Cosmos  
**Control branch:** `integration/control-room`  
**Primary integration branch:** `development/full-site-build`  
**Primary integration PR:** #25 — WIP: Full Pulsar-Frequency House integration build

This document is the release-control source of truth for the multi-chat build. It tracks what is integrated, what exists only on side branches, what is partial, and what must be completed before the full application can merge to `main` or be treated as production-ready.

## Status legend

- **INTEGRATED** — present on `development/full-site-build` and included in the integrated application path.
- **SIDE BRANCH** — implemented or advanced on another branch but not safely integrated yet.
- **PARTIAL** — meaningful foundation exists, but the production workflow is incomplete.
- **PLANNED** — required by the approved architecture/build map but not yet evidenced as a complete implementation.
- **BLOCKED / RELEASE GATE** — must be resolved before release.

## Current integration baseline

At the time this matrix was created:

- `development/full-site-build` head: `c2f728023dd8398535629fc0ff86f89096555a7b`
- PR #25 is **open, draft and mergeable**.
- PR #25 contains 73 commits, 68 changed files, 10,483 additions and 83 deletions relative to its current `main` base.
- GitHub Actions run `33028189303` / CI run number 852 completed successfully for the current integration head.
- `feature/aura-platform-core` is currently identical to `development/full-site-build`.
- `esp-command-center` is dangerously stale: 2 commits ahead but 501 commits behind the integration branch. Do **not** merge it directly.
- `feature/aura-game-forge-foundation` is 24 commits ahead and 8 behind the integration branch. It requires rebase/integration review.
- `feature/credit-wallet-foundation` is 4 commits ahead and 73 behind the integration branch. PR #28 currently targets `main`; its CI passed, but it should not bypass the full-site integration gate.

## Branch-control rules

1. `main` remains the release branch. Do not use it for concurrent development.
2. `development/full-site-build` is the long-lived integration target until the complete product is ready.
3. Each development chat works only on a dedicated feature branch created from the latest integration head.
4. A branch that is materially behind the integration branch must be rebased or its useful commits selectively ported before review.
5. No feature branch merges directly to `main` while PR #25 is the active full-site integration programme.
6. Shared files such as `app.py`, subscription/entitlement logic, account/role models, storage, project models and CI configuration require integration review because they are high-conflict surfaces.
7. Every merged subsystem must preserve server-side ESP role separation; UI hiding alone is never sufficient.
8. Every creative subsystem should reuse shared account, project, media, versioning, billing, audit and Aura context services instead of creating isolated silos.
9. CI, security, integration and deployment-readiness gates must all pass before PR #25 can leave draft state.

## Immediate branch hazards

### 1. Repository visibility — BLOCKED / RELEASE GATE
GitHub currently reports `kevemo/aura-music-studio` as **public**. Development policy is that the unreleased build should remain private. Repository visibility must be corrected in GitHub settings before proprietary build work continues to be exposed.

### 2. Vercel branch-name mismatch — RELEASE RISK
`vercel.json` disables deployments for:

- `development/full-site-build`
- `feature/aura-platform-core`
- `feature/esp-command-centers`
- `feature/creative-studios-game-forge`
- `feature/hourly-auto-build`

However, the repository currently contains `esp-command-center` and `feature/aura-game-forge-foundation`, not the two expected feature branch names above. Development branches must either be renamed/recreated from the integration head or explicitly added to the deployment-disable map on their own branch state.

### 3. PR #28 target mismatch — RELEASE RISK
The credit-wallet PR targets `main` even though the active full-site programme integrates through PR #25. Review the wallet changes against `development/full-site-build` and then integrate them through the full-site branch rather than bypassing the release-control path.

---

# Product build matrix

## A. Brand, site shell and navigation

| Capability | Status | Current evidence / next gate |
|---|---|---|
| Pulsar-Frequency House branding | INTEGRATED | Production entrypoint and benchmark docs use the approved brand and endorsement. |
| Unified single-site architecture | INTEGRATED | Public Creative Studio + ESP Creator + ESP Agent + Owner areas are defined as one application/account system. |
| Member dashboard / main site shell | INTEGRATED | `member_dashboard`, `creative_portal`, `media_studios` and shared app routing are mounted. |
| Responsive/mobile final QA | PARTIAL | Must be checked end-to-end on staging across all major workspaces. |
| Accessibility final QA | PARTIAL | Requires keyboard, focus, contrast, semantics and reduced-motion release sweep. |

## B. Accounts, authentication, permissions and owner identity

| Capability | Status | Current evidence / next gate |
|---|---|---|
| Shared account system | INTEGRATED | Existing account/access-control services are part of the application. |
| ESP membership separate from paid creative plan | INTEGRATED | Build map and installed access separation explicitly enforce separate permission dimensions. |
| Creator / Agent / Creator+Agent role model | INTEGRATED | Role-aware hubs and Creator↔Agent view switching foundations exist. |
| Mary/Kev owner context | INTEGRATED | Owner auth, owner identity middleware, owner access, control centre, directory and intelligence routes are mounted. |
| Server-side private-route enforcement | INTEGRATED / CONTINUOUS GATE | Architecture requires API-level gates. Every new ESP route must keep tests proving this boundary. |
| Owner audit attribution | PARTIAL | Existing owner identity/audit foundations exist; final production review is still required. |

## C. Commercial plans, entitlements and wallet

| Capability | Status | Current evidence / next gate |
|---|---|---|
| Free / Basic £4.99 / Pro £9.99 packaging baseline | INTEGRATED | Current competitive benchmark defines the authoritative baseline. |
| Image/poster daily quotas | INTEGRATED | Commercial-entitlement routes are intentionally mounted before Creative handlers. |
| Media download entitlements | INTEGRATED | Current integration PR includes server-enforced download rules. |
| Credit wallet ledger | SIDE BRANCH | PR #28; CI passed. Must be integrated through the full-site branch. |
| Payment processor / real top-ups | PLANNED | PR #28 explicitly does not implement payment processing yet. |
| Automatic feature-credit spending | PLANNED | Ledger exists on side branch; metering/spend policy remains to be layered on. |
| Subscription lifecycle production wiring | PARTIAL | Requires final payment provider, webhook/idempotency, cancellation/refund and reconciliation paths. |

## D. Aura — cross-media operating intelligence

| Capability | Status | Current evidence / next gate |
|---|---|---|
| Persistent Aura chat/workpage | INTEGRATED | Aura chat, streaming, reasoning, profiles, attachments, artifacts and runtime context are mounted. |
| Aura project knowledge/context | INTEGRATED | Project knowledge and project bridge are installed/mounted. |
| Aura DAW tools | INTEGRATED | DAW tool extension is installed in the production entrypoint. |
| Aura Game Forge tools | INTEGRATED / SIDE BRANCH ADVANCEMENT | Foundation is integrated; additional Game Forge work exists on the side branch. |
| Aura workflow engine | INTEGRATED | Installed after tool classes so verified step results can flow through workflows. |
| Text + voice interaction | INTEGRATED / PARTIAL | Voice conversation path exists; final provider credentials and full production duplex behaviour still require deployment configuration. |
| Multilingual / translation workflow | PARTIAL | Foundation exists in the broader Aura work; production STT/TTS/provider configuration remains a release dependency. |
| 3D Aura runtime | PARTIAL | Runtime/avatar infrastructure exists; final likeness-grade `aura.glb` and DCC/GPU pipeline completion remain outstanding. |
| Aura-guided exact non-destructive edits across all media | PARTIAL | Project bridge/versioning exists; complete cross-media command coverage still needs consolidation and UX QA. |

## E. Shared Creative Project Workspace

| Capability | Status | Current evidence / next gate |
|---|---|---|
| Creative projects | INTEGRATED | `creative_project_api` is mounted. |
| Creative workspace | INTEGRATED | `creative_workspace` is mounted. |
| Creative Library | INTEGRATED | Shared library route exists and is part of PR #25. |
| Version history / non-destructive variants | INTEGRATED / PARTIAL | Version-autopromotion and project services exist; full cross-media history UX needs consolidation. |
| Persistent Pulsar Player | INTEGRATED | Player middleware/router is integrated. |
| Cross-media project timeline | PARTIAL | Current foundations connect projects/media/context, but a first-class unified timeline for song → image → video → social variants should be completed. |
| Precise version mixing (e.g. vocals from V4 + chorus from V7) | PARTIAL | Version primitives exist; complete user-facing semantic merge workflow should be treated as a major differentiation milestone. |
| Reference uploads attached to project context | INTEGRATED / PARTIAL | Aura attachment/project systems exist; all generators must consistently consume the shared reference model. |

## F. Music generation and transformation

| Capability | Status | Current evidence / next gate |
|---|---|---|
| Prompt/lyrics-to-song foundation | INTEGRATED | ACE-Step / music generation foundations exist in the codebase. |
| Full-song generation adapters | INTEGRATED / PARTIAL | ACE-Step/YuE/self-host architecture exists; real production renderer capacity and credentials remain infrastructure gates. |
| Song DNA | INTEGRATED | Song DNA API, portal and execution routes are mounted. |
| Section-level regeneration | INTEGRATED | `song_section_regeneration` is included in the full-site integration changes. |
| Lyrics alignment | INTEGRATED | API and portal routes are mounted. |
| Performance / take workflow | INTEGRATED | Performance input, takes, recording and revision routes are mounted. |
| Stem-aware workflow | PARTIAL | Production/DAW foundation exists; release QA must verify end-to-end generated stems, edits and exports. |
| Commercial final-master safety | INTEGRATED / CONTINUOUS GATE | Existing architecture prevents symbolic guides from being treated as real final audio; preserve this invariant. |

## G. DAW, recording, editing, mixing and mastering

| Capability | Status | Current evidence / next gate |
|---|---|---|
| Browser DAW foundation | INTEGRATED | DAW API + portal are mounted. |
| Direct recording | INTEGRATED | Recording API/UI routes are mounted. |
| Routing | INTEGRATED | Routing API/UI routes are mounted. |
| Mixer | INTEGRATED | Mixer UI is mounted. |
| MIDI/control layer | INTEGRATED | `daw_midi` is part of the current integration PR. |
| Visual timeline editing | INTEGRATED / PARTIAL | Significant DAW work exists; final professional UX and performance testing remain. |
| Stem separation | PARTIAL | Must be verified against actual renderer/service configuration. |
| Mastering | PARTIAL | Production foundations exist; final real-audio processing pipeline requires end-to-end QA. |
| Automation/advanced editing depth | PARTIAL | Benchmark target is Ableton/Logic-class workflow where practical; remaining professional depth should be tracked feature-by-feature. |

## H. Voice House / vocal tools

| Capability | Status | Current evidence / next gate |
|---|---|---|
| Voice House APIs and portal | INTEGRATED | Voice House API, assets API and portal are mounted. |
| Vocal workflow | INTEGRATED | Vocal API is mounted. |
| Consent-bound voice cloning/conversion | PARTIAL / RELEASE GATE | Consent and ownership controls must remain authoritative; production model/provider integration requires final validation. |
| Singing-voice transformation | PARTIAL | Foundation exists; real production renderer and abuse-control QA remain. |

## I. Image, poster and artwork studio

| Capability | Status | Current evidence / next gate |
|---|---|---|
| Image/poster generation | INTEGRATED | Current full-site PR includes generation entitlements and media storage/preview paths. |
| Save/download outputs | INTEGRATED | Commercial entitlement baseline includes save/download support. |
| Daily plan quotas | INTEGRATED | Free/Basic/Pro image/poster quota enforcement is server-side. |
| Reference-aware generation | PARTIAL | Shared attachments/project context exist; generator-level consistency needs release QA. |
| Object/background replacement | PARTIAL | Conversational image-editing work exists as a separate branch and requires integration review. |
| In/out-painting | PARTIAL | Must be verified and consolidated into the primary Creative Project workflow. |
| Upscaling | PARTIAL | Must be verified against actual production adapter. |
| Brand kits/templates/text-layout tools | PARTIAL / PLANNED | Benchmark target is defined; complete professional template/editor experience is not yet release-certified. |

## J. Video generation and professional editing

| Capability | Status | Current evidence / next gate |
|---|---|---|
| Text/image-to-video foundations | INTEGRATED / PARTIAL | Video-generation adapters and Music Video Studio work exist in the codebase. |
| Music Video Director | INTEGRATED / PARTIAL | Existing work connects Aura/music/video direction; final editor UX remains. |
| Lyric video / visualizer rendering | INTEGRATED | Existing local FFmpeg/librosa pipeline is part of the broader video work. |
| External neural video adapters | PARTIAL | Wan/LTX/other adapter architecture exists; production compute/provider configuration remains. |
| Scene/shot timeline | PARTIAL | Benchmark target defined; final coherent editor must be release-tested. |
| Captions / beat / lyric sync | PARTIAL | Foundations exist; final UX and export verification required. |
| Masks/cutouts/keyframes/transitions/effects/colour | PARTIAL | Professional editor completeness remains one of the largest Creative Studio gaps. |
| Reusable characters/assets | PARTIAL | Shared asset/project model exists; persistent character continuity needs end-to-end validation. |
| Social-format variants | PARTIAL | Social system exists; one-command video adaptation/publishing workflow should be completed. |

## K. Game Forge

| Capability | Status | Current evidence / next gate |
|---|---|---|
| Game Forge API/portal foundation | INTEGRATED | Game Forge API and portal are mounted in the full-site branch. |
| Game models/store/runtime | INTEGRATED / SIDE BRANCH ADVANCEMENT | Foundation modules exist; side branch has substantial additional commits. |
| Game world model/API | INTEGRATED / SIDE BRANCH ADVANCEMENT | World and world API are part of current integrated changes and side-branch development. |
| Integrity scanning/publish gates | INTEGRATED / PARTIAL | Integrity modules/routes exist; production export/publish validation needs final QA. |
| Native 3D renderer | INTEGRATED / SIDE BRANCH ADVANCEMENT | Native renderer architecture exists; performance and final tooling need integration review. |
| Aura game-building tools | INTEGRATED | Aura tool installation is active in the entrypoint. |
| Asset pipeline | SIDE BRANCH / PARTIAL | Dedicated Game Forge asset-pipeline work exists and must be consolidated. |
| Full browser game editor UX | PARTIAL | Engine foundations are ahead of final end-user editor polish. |

## L. ESP Creator Hub

| Capability | Status | Current evidence / next gate |
|---|---|---|
| Private Creator area | INTEGRATED | Member Hub/role model defines and enforces Creator access. |
| Niche selector / niche-aware experience | INTEGRATED / PARTIAL | ESP niche portal and persisted niche foundations exist; final per-niche dashboard/training coverage needs content QA. |
| My Plan / progress | INTEGRATED | Creator-plan overlay and progress portal are mounted. |
| Academy / learning library | INTEGRATED / PARTIAL | Learning-library module exists; all approved Drive content still requires coverage verification. |
| Incentives/rewards engine | INTEGRATED / PARTIAL | Incentives module exists; all current ESP programmes need rule-by-rule verification. |
| Creator social management | INTEGRATED / PARTIAL | ESP Social systems are integrated; provider support varies by official API capability. |
| Collaborations/events | INTEGRATED / PARTIAL | Collaboration module exists; final booking/operations UX needs verification. |
| Brand opportunities | INTEGRATED / PARTIAL | Brand opportunity/commercial modules exist; creator-facing marketplace workflow needs final integration QA. |
| Shop Creator system | PLANNED / PARTIAL | Included in approved build map; no release-certified end-to-end Shop module yet. |
| Wellbeing/safety resources | PLANNED / PARTIAL | Approved build map includes these resources with non-clinical boundaries; final native workflow remains. |

## M. ESP Agent Hub

| Capability | Status | Current evidence / next gate |
|---|---|---|
| Private Agent area | INTEGRATED | Role architecture includes Agent and Creator+Agent contexts. |
| Creator↔Agent view switch | INTEGRATED / PARTIAL | Foundation exists while preserving server role gates; end-to-end UX needs final test. |
| Assigned creator roster | INTEGRATED / PARTIAL | Agent roster overlay exists. |
| Creator Discovery CRM | INTEGRATED | Current integration PR includes discovery/validation/outreach/follow-up CRM. |
| Network-affiliation block / compliance controls | INTEGRATED / CONTINUOUS GATE | Build map defines provenance, dedupe, affiliation, age/eligibility and do-not-contact boundaries. |
| Recruitment funnel / metrics | PARTIAL | Discovery CRM exists; complete funnel and owner/agent dashboards require verification. |
| Agent Academy | PARTIAL | Source/build map exists; old `esp-command-center` branch contains stale implementation work that must be ported safely rather than merged. |
| Creator Health Queue | PLANNED / PARTIAL | Required by approved build map; verify complete native implementation. |
| Technical/compliance escalation lanes | PLANNED / PARTIAL | Required by approved build map; not release-certified yet. |

## N. ESP Social / Rella-class creator operating system

| Capability | Status | Current evidence / next gate |
|---|---|---|
| Secure social provider connections | INTEGRATED | Current integration PR includes OAuth/connections work. |
| Publishing queue | INTEGRATED / PARTIAL | Secure publishing queue exists; provider-by-provider production credentials/scopes remain. |
| Provider analytics | INTEGRATED | Private provider analytics UI and ingestion foundation exist. |
| TikTok `video.list` explicit consent | INTEGRATED | Separate consent upgrade exists. |
| TikTok public-video statistics sync | INTEGRATED | Current implementation ingests permitted public video statistics and does not claim unsupported LIVE/revenue data. |
| Content calendar | PARTIAL | Benchmark target requires full calendar/campaign planning UX; verify completeness. |
| Campaign board/tasks/notes/approvals | PARTIAL | Social/ESP foundations exist; complete Rella-class workflow still needs product-level consolidation. |
| Platform-specific variants | PARTIAL | Creative + social systems can support this direction; one-flow UX remains to be completed. |
| Social inbox | PLANNED / API-DEPENDENT | Only implement where official provider APIs permit it. |
| Aura social recommendations | INTEGRATED / PARTIAL | Social intelligence exists; final cross-platform recommendation UX needs validation. |

## O. Mary/Kev Owner Administration

| Capability | Status | Current evidence / next gate |
|---|---|---|
| Owner identity selection | INTEGRATED | Owner identity middleware and owner auth portal are mounted. |
| Owner control centre | INTEGRATED | Control centre route is mounted. |
| User directory/intelligence | INTEGRATED / PARTIAL | Directory/intelligence routes are mounted; final management UX needs release QA. |
| ESP permissions/assignments | INTEGRATED / PARTIAL | Owner-access and role foundations exist; complete operator workflow must be tested. |
| Owner focus/momentum | INTEGRATED / PARTIAL | `esp_owner_focus` is included in current integration changes. |
| Finance/subscription administration | PARTIAL | Entitlement foundation exists; payment processor/reconciliation remain incomplete. |
| System health / compute controls | INTEGRATED / PARTIAL | Owner compute/backup routes exist; production observability remains to be completed. |
| Mary/Kev personalised Aura/theme context | PARTIAL | Owner identity/context foundations exist; full user-specific presentation consistency needs final QA. |

## P. ESP Commercial, brand and growth systems

| Capability | Status | Current evidence / next gate |
|---|---|---|
| Brand opportunity model | INTEGRATED / PARTIAL | `esp_brand_opportunities` exists in current integration changes. |
| Collaboration workflows | INTEGRATED / PARTIAL | `esp_collaborations` exists. |
| Commercial growth tools | INTEGRATED / PARTIAL | `esp_commercial_growth` exists. |
| Brand lead CRM / proposals / deliverables / renewals | PARTIAL | Build map requires complete workflow; release-certify each stage. |
| Media-kit workflow | PLANNED / PARTIAL | Required by build map; verify/create native implementation. |
| Public opt-in creator directory/case studies | PLANNED | Must be consent-based and remains later build order. |

## Q. Infrastructure, security, CI and deployment

| Capability | Status | Current evidence / next gate |
|---|---|---|
| GitHub CI | INTEGRATED | Current integration head passed Pulsar-Frequency House CI. |
| Tests for current integrated modules | INTEGRATED / CONTINUOUS GATE | PR #25 adds substantial test coverage; every new subsystem must extend it. |
| Self-host/compute-node architecture | INTEGRATED / PARTIAL | Compute-node and production/self-host foundations exist; real infrastructure must be configured. |
| Tenant-bound storage/media access | INTEGRATED / CONTINUOUS GATE | Tenant storage and media-preview paths exist; every new media type must preserve ownership isolation. |
| Secrets hygiene | RELEASE GATE | No production secret should be committed. Final secret scanning and environment review required. |
| Repository private during development | BLOCKED | Current GitHub repository visibility is public and must be corrected. |
| Integration branch Vercel deployment disabled | INTEGRATED | `development/full-site-build` is explicitly disabled in `vercel.json`. |
| All actual development branches deployment-disabled | PARTIAL / RELEASE RISK | Current branch names do not fully match the deployment-disable list. |
| Production Vercel release | NOT YET | Full integration intentionally remains off production until release gates pass. |
| Monitoring/error telemetry | PARTIAL / PLANNED | Required before production launch. |
| Backup/restore drill | PARTIAL | Backup foundations exist; restore must be tested, not merely configured. |

---

# Priority integration queue

## P0 — protect the build

1. Make the GitHub repository private again.
2. Stop new work from branching off stale histories.
3. Recreate/realign the ESP development branch from the latest integration head and selectively port useful `esp-command-center` work.
4. Rebase/review Game Forge side-branch work against the latest integration head.
5. Review PR #28 against the integration branch and integrate it through PR #25 rather than directly to `main`.
6. Correct Vercel deployment-disabled branch names everywhere development is actively occurring.
7. Run CI after every integration step.

## P1 — finish the shared platform layer

1. Complete the unified Creative Project Timeline and cross-media version semantics.
2. Finish payment processor + subscription lifecycle + wallet spending/reconciliation.
3. Finish production Aura realtime voice/STT/TTS/provider integration.
4. Complete final Aura 3D likeness asset and production packaging pipeline.
5. Complete shared media jobs/queue/status/cost controls across music, image, video and games.
6. Finish abuse controls, quotas, retention and provider-failure handling.

## P1 — finish Creative Studios

1. Professional image editor: replacement, in/out-painting, resizing, layouts/templates and brand kits.
2. Professional video editor: scene timeline, masks, cutouts, keyframes, transitions, captions, beat/lyric sync, colour/effects and social reframing.
3. DAW release polish: editing depth, automation, stems, mastering, export and performance.
4. Voice House production consent/audit flow and renderer readiness.
5. Consolidate Game Forge side-branch advances into the integration branch without regressing shared services.

## P1 — finish ESP systems

1. Safely port useful stale ESP Command Center work onto a fresh current branch.
2. Complete recruitment funnel + Creator Health + owner/agent performance dashboards.
3. Complete Academy coverage from approved ESP source material.
4. Complete Support Desk / specialist queues.
5. Complete Shop Creator/sample tracking workflows.
6. Complete creator safety/wellbeing/IP/evidence workflows.
7. Complete brand CRM/media-kit/deliverable/renewal flows.
8. Consolidate Rella-class social planning, calendar, approval and variant workflows.

## P2 — production release hardening

1. Full permission matrix penetration tests.
2. Cross-tenant media/project access tests.
3. Payment and credit reconciliation tests.
4. Provider outage/rate-limit/error simulation.
5. Mobile/responsive/accessibility/browser QA.
6. Performance/load testing for uploads, generation jobs, project timelines and Aura sessions.
7. Backup + restore drill.
8. Observability, error telemetry and operator runbooks.
9. Staging deployment with production-like configuration.
10. Only after all gates pass: mark PR #25 ready, final review, controlled merge to `main`, production deployment and post-release smoke tests.

---

# Definition of done for the full site

Pulsar-Frequency House is not considered complete merely because routes or UI screens exist. A feature is release-complete only when:

1. the user-facing workflow is usable end to end;
2. server-side permission and entitlement enforcement is present;
3. tenant/project ownership boundaries are enforced;
4. real provider/render state is reported truthfully;
5. failures are recoverable and do not corrupt project state;
6. non-destructive project/version behaviour is preserved;
7. Aura can access the feature through a safe, permission-aware tool path where appropriate;
8. automated tests cover the critical path and denial path;
9. mobile/accessibility behaviour is acceptable;
10. the feature passes the integration CI and staging smoke tests.

This matrix should be updated after every material integration so the three development chats and the control-room chat operate from the same factual build state.