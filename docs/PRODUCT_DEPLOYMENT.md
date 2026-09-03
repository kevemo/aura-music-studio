# Elevate Souls Productions Content Creation Command Center — Product Deployment

**Powered by Aura AI**  
**Elevate Your Soul Through Purposeful Media**

This document describes the deployment architecture for the **Elevate Souls Productions Content Creation Command Center**. The Python package name `aura_music_studio`, the `LSS_*` environment-variable namespace, database/cookie identifiers, and older repository terms are retained only where compatibility requires them. They are not the public product brand.

The primary architecture supports **ESP-controlled self-hosting** while also retaining a web/serverless deployment path where appropriate. Infrastructure configuration is not, by itself, proof of production readiness: launch requires real provider, security, data, capacity, monitoring, backup/restore and rollback evidence.

See [`SELF_HOSTING.md`](SELF_HOSTING.md) for the self-hosted public-address/network guide.

## What the Command Center stack contains

The integrated application and supporting private services include foundations for:

- public ESP-branded landing, pricing and discovery pages;
- installable PWA metadata and tightly scoped public service-worker behaviour;
- sign-up, sign-in and account/session security;
- membership approval and subscription lifecycle;
- Free / Member / Unlimited Pro entitlement enforcement;
- server-authoritative cross-studio usage/admission controls;
- Cosmic Creation Coin catalogue, wallet and payment fulfilment controls;
- private per-member projects and assets;
- Music / Professional DAW workflows;
- Voice House consent-bound workflows;
- Image & Poster Studio workflows;
- AI Video / Professional Video Editor workflows;
- Game Forge creation/playtest foundations;
- Social Media Centre planning, publishing-adapter and analytics foundations;
- Aura LIVE Overlay Studio, Auto Cue and Guardian foundations;
- private ESP Creator and Agent Hubs;
- Mary & Kev Owner Command Center administration;
- first-party marketplace, entitlement and settlement foundations;
- background production workers and provider/model adapters;
- Aura speech, reasoning, bounded tools and controlled web/research gateways;
- privacy, consent, safeguarding, IP/provenance and audit controls;
- owner-controlled backup/migration foundations;
- production liveness, readiness and authenticated metrics surfaces.

A module, route, UI label or adapter must not be represented as production-complete unless its required integration and external evidence gates have passed.

## Authoritative membership model

The public membership tiers are defined server-side in `aura_music_studio/plans.py`. The compatibility IDs remain `free`, `base` and `pro`, while customer-facing names are Free, Member and Unlimited Pro.

### Free — £0

Core Command Center exploration and limited eligible creative access. Current plan-level capabilities include Aura producer/songwriting assistance, starter creative tools, limited image/poster creation and public-safe Game Forge playtesting.

### Member — £4.99/month

Increased creative access across enabled Music, Video and Game workflows. The commercial target is up to **5 eligible creations/edits per day** across the defined cross-studio operation set, with eligible additional use handled through Cosmic Creation Coins where configured. The authoritative cross-studio allowance is enforced by the usage/admission layer rather than inferred from the plan object alone.

### Unlimited Pro — £9.99/month

Highest normal enabled creative access, **including the AuraSec entitlement**, and effectively unlimited ordinary use subject to fair-use, infrastructure, provider-capacity, rate-control, anti-abuse, safety, rights and legal safeguards. Eligible publishing remains subject to marketplace, entitlement, rights and accounting gates.

AuraSec can also be distributed and sold separately under its own approved commercial catalogue. This deployment document deliberately does not invent a standalone AuraSec price.

Safety, privacy, consent and transparency protections are never paywalled.

## Cosmic Creation Coins

Authoritative public Coin packs are:

- **1,000 Cosmic Creation Coins — £5**
- **2,500 Cosmic Creation Coins — £10**
- **6,000 Cosmic Creation Coins — £20**

Coin price, quantity, debit, fulfilment, refund and reversal values remain server-authoritative. Browser-supplied commercial values must never be trusted as payment evidence.

## Payment architecture

The current codebase contains hardened Stripe subscription/Coin paths, verified marketplace fee/settlement/refund evidence foundations, and a server-authoritative hosted marketplace checkout release slice undergoing integration verification. Compatibility payment surfaces may remain where earlier architecture still requires them.

Payment rules are fail-closed:

- opening or returning from a checkout URL is never proof of payment;
- browser values cannot grant a subscription, Coins, marketplace entitlement or ESP organisational role;
- Stripe webhook evidence is cryptographically verified before covered mutations;
- membership and marketplace commercial facts remain server-authoritative;
- marketplace checkout must bind to an immutable local order and authenticated buyer;
- marketplace settlement/refund mutation requires verified provider evidence rather than redirect state;
- payment-provider configuration and live end-to-end evidence are launch gates.

Any legacy/manual payment bridge that remains enabled must preserve the same rule: an owner/provider verification step, not the browser redirect, is the source of truth.

## Marketplace economics

For eligible creator marketplace publications, the intended revenue allocation is:

- **50% Creator**
- **50% Elevate Souls Productions**

Eligible ESP-owned catalogue content explicitly created/published by Mary or Kev under Owner/Admin identity allocates **100% of eligible ESP-owned creation revenue to the ESP Admin Revenue Pool**.

Production marketplace readiness additionally requires verified purchase/entitlement flow, provider fees/net evidence, creator liabilities, ESP allocation, refunds, chargebacks, pending/cleared balances, reporting, rights/safety gates and unpublish/revocation behaviour.

## Public and private URLs

Representative public/indexable surfaces include:

- `/`
- `/pricing`
- `/signup`
- `/signin`
- public discovery/studio landing routes
- `/robots.txt`
- `/sitemap.xml`
- `/manifest.webmanifest`

Representative authenticated or role-gated surfaces include:

- `/dashboard`
- creative project/studio routes
- ESP Creator/Agent routes
- Owner Command Center routes
- private project/assets APIs
- commercial/account/payment administration

Private/member/owner routes must remain excluded from public search indexing and public service-worker caching unless an explicit reviewed exception exists.

## Membership lifecycle

The precise state machine is server-authoritative. At a high level:

1. A user creates or requests the appropriate account/membership state.
2. ESP approval rules are applied where required.
3. Free access may activate without paid-provider evidence where allowed by policy.
4. A paid tier requires the correct approved plan plus verified payment evidence.
5. Subscription/payment evidence binds to the authenticated local user.
6. Renewal, cancellation, expiry, refund or dispute handling updates access through verified provider/owner evidence rather than browser state.
7. ESP organisational roles remain separate from commercial subscription entitlements.

## Self-host-first network architecture

Default private stack:

```text
browser on ESP host
        │
        ▼
127.0.0.1:8000
        │
        ▼
ESP Command Center
 ├─ accounts / membership / commercial ledgers
 ├─ private projects and assets
 ├─ Aura production workers
 ├─ local/open model workers
 └─ private research/search services where enabled
```

Optional public self-host profile:

```text
Internet
   │
   ▼
public hostname / approved public endpoint
   │
   ▼
Caddy or approved TLS/reverse-proxy boundary
   │
   ▼
private Command Center service
```

The FastAPI service should remain behind the approved public TLS/reverse-proxy boundary in self-hosted production.

## Public addressing and HTTPS

Aura Public Address Manager supports self-hosting diagnostics and configured address-management modes. A provider account/record still has to exist when an external DDNS provider is used; provider credentials remain private deployment secrets.

For a hostname that resolves to the ESP server, configure the production base URL and secure cookies according to the validated deployment profile. Browser-trusted HTTPS, DNS readiness and inbound routing must be verified in the real production environment before launch.

Do not treat a configuration file saying HTTPS is enabled as TLS evidence. Production evidence should include the live hostname/certificate and the actual application response through the intended ingress path.

## Secure initialization and secrets

Self-host initialization remains available through the repository setup/CLI paths. Deployment secrets must never be committed or supplied through unsafe shell-history patterns when a private environment/secret store is available.

At minimum, production secret handling must cover:

- owner/provenance secrets;
- session/security keys;
- payment-provider keys/webhook secrets;
- SMTP/email credentials;
- external provider/API credentials;
- DDNS credentials where used;
- model/provider credentials where used;
- production database/storage credentials where applicable.

## Email delivery

The Command Center supports SMTP-backed delivery paths. If development fallback/outbox behaviour is used, it must be reported as non-delivery and must not be presented as production email success.

Production launch requires verified delivery, sender configuration and failure/escalation behaviour for account, billing and operationally critical messages.

## Backups and recovery

The backup architecture can create portable archives containing application data and private project material while excluding deployment secrets. Integrity checks and restore controls are part of the recovery design.

Production readiness requires more than the existence of backup code. ESP must verify:

- scheduled/operational backup creation;
- integrity of the resulting backup;
- access controls around backup data;
- a successful restore drill into an isolated target;
- documented recovery/rollback ownership and procedure.

## Real-audio and AI compute architecture

Customer/account web services are separated from heavy generation/rendering where possible. The production stack can route to self-hosted/open or approved external generation/rendering providers depending on the capability.

MIDI, MusicXML, notation and other symbolic representations are control layers only. They must not silently substitute for a required real/neural waveform Final Master.

GPU/AI compute is a physical/provider resource. A self-host-first architecture does not make generation unlimited or computationally free; throughput remains bounded by available hardware, model/provider limits and operational safeguards.

## Runtime health and readiness

The production application exposes:

- `/health/live` for process liveness;
- `/health/ready` for fail-closed deployment readiness;
- authenticated `/internal/metrics` for operational metrics when monitoring is configured.

Readiness evaluates configuration categories such as payments, required provider credentials, GPU/renderer requirements, monitoring, backups, security/HTTPS, storage and deployment environment. A green source-code test does not replace checking these gates in the real production environment.

## Vercel / web deployment path

The repository retains a Vercel-compatible FastAPI bootstrap (`vercel_bootstrap:app`) for the web application path. Serverless/web deployment does not replace heavy-worker/GPU infrastructure where those workloads require separate compute.

A repository Vercel configuration or successful Vercel build alone is not full product production evidence. The authorised Vercel account must independently confirm that the intended project/deployment exists, and the release candidate still needs the complete provider/data/security/operations gates described below.

## Production release gates

`development/full-site-build` is the active integration branch. `main` is the production release target. Integration must not be promoted to `main` merely because feature code exists or an isolated feature PR passes.

Production promotion requires, at minimum:

- no unresolved P0 blockers;
- acceptable disposition of P1 risks;
- complete regression/security checks on the exact release candidate;
- verified public domain/TLS and ingress;
- verified production database and object/project storage strategy;
- verified payment-provider configuration and end-to-end payment evidence;
- verified email delivery;
- verified required external provider/model capabilities and permissions;
- secrets-management evidence;
- monitoring, logs and alerting;
- backup **and restore** evidence;
- deployment rollback procedure and proof;
- realistic capacity/load/failure testing for release-critical paths;
- incident/support ownership and escalation procedures;
- current public legal/privacy/commercial configuration and documentation;
- release/integration branch-protection or equivalent repository-governance enforcement.

## Truth rule

The release status must distinguish between:

- **implemented and verified**;
- **implemented pending verification**;
- **integrated pending production evidence**;
- **external dependency**;
- **gap confirmed**;
- **planned**.

Code existence, UI presence, infrastructure-as-code, a provider adapter, or a successful browser redirect is not enough to claim a capability is production complete.
