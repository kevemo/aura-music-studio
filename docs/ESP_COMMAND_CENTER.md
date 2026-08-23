# ESP Command Center

The ESP Command Center is the private Creator Network layer inside **Elevate Souls Productions Presents: The Live Sound Studio**. It deliberately separates public music-studio customers from ESP Creator Network training, operations and leadership material.

## Access model

| Account type | Music studio | ESP Creator area | ESP Agent area | Owner admin |
|---|---|---:|---:|---:|
| Regular Free | Free studio features | No | No | No |
| Regular Base | Base studio features | No | No | No |
| Regular Pro | Full Pro studio | No | No | No |
| ESP Creator | Base included while ESP-active; may upgrade to Pro | Yes | No | No |
| ESP Agent | Base included while ESP-active; may upgrade to Pro | Shared creator resources + Agent area | Yes | No |
| ESP Creator + Agent | Base included while ESP-active; may upgrade to Pro | Yes | Yes | No |
| ESP Owner | Owner-comped Pro | Yes | Yes | Yes |

ESP approval never exposes internal areas to an ordinary Live Sound Studio customer. Server-side role checks are performed before a resource is opened.

## Approval flow

1. Member creates/signs into a Live Sound Studio account.
2. Member opens `/command-center` and requests **Creator**, **Agent**, or **Creator + Agent** access.
3. The request records TikTok handle, region and optional context.
4. The configured ESP approval inbox receives a single-use review URL.
5. Kev or Mary can approve, reject or assign a different ESP role.
6. Approval changes the ESP membership to active.
7. If the member is not already Pro, their studio entitlement becomes Base with `billing_status=esp_comped`.
8. The applicant receives a decision email.
9. Owners may later change Creator/Agent/Both roles or revoke ESP access.

## Subscription behavior

- ESP membership includes the normal Base entitlement without a Base subscription payment.
- Pro remains the paid full-feature upgrade for ESP members.
- If an ESP member's paid Pro period expires, they fall back to included Base instead of being locked out.
- If ESP membership is revoked while the account is using the comped Base entitlement, the account falls back to Free.
- Owner creation access uses `owner_comped` Pro and is independent of public paid subscriptions.

## Creator resources

The current resource catalogue routes approved members to the supplied Drive systems:

- Creator Companion System
- Creator Incentives & Recognition
- Battle Creator Programme
- Battle Operations & Collaboration
- TikTok Effect House Academy

These areas cover creator academy material, LIVE structure and growth, incentives, battle systems, niche/campaign resources, Effect House and supporting training.

## Agent resources

Agent/Both members additionally receive:

- ESP Agent Apprentice Programme
- ESP Agent & Creator Operations Master
- Governance, Accountability & Discord Operations
- ESP Expansion & Competitive Blueprint

These areas cover recruitment, KPI/diamond systems, video strategy, CapCut, business building, onboarding, governance, culture protection, accountability, staff duties, collaboration, scaling and competitive expansion.

## Owner dashboard

`/owner/esp` uses the existing protected owner session and adds:

- pending ESP access count
- active ESP members
- Creator-only / Agent-only / Both counts
- total studio users
- ESP-comped Base count
- active Pro count
- 30-day training/resource and studio usage
- verified subscription revenue history
- member role switching
- ESP membership removal
- link to the private owner Creation Centre

The existing `/owner/dashboard` remains responsible for normal Live Sound Studio membership/payment administration.

## Private owner Creation Centre

`/owner/esp/creation-centre` is owner-session only. Entering the owner studio creates/uses an internal owner identity with Pro entitlement and a normal tenant-isolated studio session. It does **not** store an email-account password in the repository.

## Data model

The Command Center extends the existing SQLite store with:

- `esp_memberships` — current ESP state and assigned role
- `esp_access_requests` — approval history and single-use request tokens
- `esp_resource_events` — resource/usage audit events
- `esp_training_progress` — per-member progress by resource

Existing `users`, `sessions`, `usage_events`, `subscription_state`, `subscription_payments` and studio project isolation continue to be used.

## Security requirements

Use deployment secrets/environment variables:

- `LSS_ADMIN_KEY` — long random owner-admin secret
- `LSS_OWNER_EMAIL` — internal owner studio email/identity
- `LSS_MEMBERSHIP_APPROVAL_EMAIL` — inbox that receives approval requests
- `LSS_SMTP_USERNAME` / `LSS_SMTP_PASSWORD` — SMTP credential; for Gmail prefer an app-specific password/OAuth mechanism

Never commit a Gmail/Google account password, SMTP credential, owner admin key, PayPal credential or model API secret.

## Product direction

The Command Center is intentionally designed as a creator-network operating system rather than a static document menu. The next product layers can use the same role, event and audit model for creator CRM, mentor assignments, KPI dashboards, campaigns, applications, battle scheduling, task boards, certifications/exams, notifications, internal messaging, compliance cases, referrals, incentives, payments and predictive performance analytics.
