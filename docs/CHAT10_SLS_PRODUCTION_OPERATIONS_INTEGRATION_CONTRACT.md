# Chat 10 SLS & Production Operations Integration Contract

Status: Chat 10 integration contract. This document defines production-hardening boundaries and evidence requirements. It is **not** a production-release approval. Chat 11 remains the repository-wide release gate.

## 1. Public naming and compatibility boundary

The public/formal security product name is **Elevate Souls Productions Secure Lattice System (SLS)**.

Legacy internal identifiers such as `aura_sec`, `aura_sec_*`, database table names, compatibility route `/aura-sec`, entitlement key `aura_sec`, environment keys and package/module names may remain where changing them would break persisted data, APIs or parallel workstreams. They must not be presented as the public product name.

Command Center membership and SLS native/device licensing are separate commercial authorities. Unlimited Pro membership may grant Aura OS where the plan contract permits it, but it does not create an SLS entitlement. A commercial SLS entitlement also does not create device trust, a verified heartbeat, attestation, protection state, signing authority or privileged command authority.

## 2. Authoritative implementation surfaces

### Production/readiness and evidence

- `aura_music_studio/production_readiness.py`
- `aura_music_studio/operational_evidence.py`
- `scripts/run_restore_drill.py`
- `.github/workflows/ci.yml`
- `.github/workflows/security-gates.yml`

### SLS compatibility/native boundary

- `aura_music_studio/native_products.py`
- `aura_music_studio/native_access.py`
- `aura_music_studio/aura_sec_portal.py`
- `aura_music_studio/aura_sec_health.py`
- `aura_music_studio/aura_sec_heartbeat_gateway.py`
- `aura_music_studio/aura_sec_device_attestation.py`
- `aura_music_studio/aura_sec_enrollment.py`
- `aura_music_studio/aura_sec_native_bridge.py`
- `aura_music_studio/aura_sec_native_execution_guard.py`
- `aura_music_studio/aura_sec_command_signing.py`
- `aura_music_studio/aura_sec_command_delivery.py`
- `aura_music_studio/aura_sec_command_sequence.py`
- `aura_music_studio/aura_sec_release_trust.py`
- `aura_music_studio/aura_sec_recovery_vault.py`
- `aura_music_studio/aura_sec_strong_reauth.py`

### Request/application security

- `aura_music_studio/security.py`
- `aura_music_studio/auth_security.py`
- `aura_music_studio/csrf_tokens.py`
- `aura_music_studio/upload_security.py`
- `aura_music_studio/web_access.py`
- `aura_music_studio/aura_sandbox.py`
- `aura_music_studio/protected_data_authority.py`

### Queue/background work

- `aura_music_studio/jobs.py`
- API/domain job adapters owned by their feature workstreams

### Game Forge production boundary

Chat 8 owns Game Forge product behavior. Chat 10 owns only production/security hardening of its execution/export boundary.

- `aura_music_studio/game_forge_runtime.py`
- `aura_music_studio/game_forge_export.py`
- `aura_music_studio/game_forge_export_readiness.py`
- `aura_music_studio/game_forge_export_portal.py`
- `aura_music_studio/game_forge_godot_export.py`
- `aura_music_studio/game_forge_godot_export_api.py`
- `aura_music_studio/game_forge_package_integrity.py`
- `aura_music_studio/game_forge_assets.py`
- `aura_music_studio/tenant_storage.py`

## 3. Environment/configuration contract

Environment variables below are configuration **names only**. Secret values must never be committed to this document, source control, logs, screenshots or release reports.

### Deployment/runtime

- `AURA_DEPLOYMENT_ENV`
- `LSS_PUBLIC_BASE_URL`
- `AURA_ALLOWED_ORIGINS`
- `LSS_DB_PATH`
- `AURA_PROJECTS_ROOT`
- `LSS_BACKUP_DIR`
- `LSS_RESTORE_EVIDENCE_PATH`
- `LSS_RESTORE_EVIDENCE_MAX_AGE_HOURS`
- `AURA_MONITORING_ENABLED`
- `AURA_MONITORING_TOKEN`

### Request/security controls

- `LSS_CSRF_HMAC_KEY`
- `LSS_AUTH_RATE_LIMIT`
- `LSS_AUTH_RATE_WINDOW_SECONDS`
- `LSS_WEBHOOK_MAX_BYTES`
- `AURA_WEB_ENABLED`
- `AURA_WEB_ALLOW_HTTP`
- `AURA_WEB_TIMEOUT`
- `AURA_WEB_MAX_BYTES`
- `AURA_WEB_ALLOWED_DOMAINS`
- `AURA_WEB_BLOCKED_DOMAINS`
- `AURA_SEARXNG_URL`
- `AURA_SANDBOX_URL`
- `AURA_SANDBOX_TOKEN`
- `AURA_SANDBOX_MAX_RESPONSE_BYTES`

### Queue/export/resource controls

- `LSS_JOB_MAX_PAYLOAD_BYTES`
- `GAME_FORGE_EXPORT_MAX_MEDIA_BYTES`
- `GAME_FORGE_EXPORT_MAX_ASSETS`
- `CREATIVE_RENDER_FREE_DAILY_UNITS`
- `CREATIVE_RENDER_FREE_BURST_JOBS`
- `CREATIVE_RENDER_FREE_MAX_REQUEST_UNITS`
- `CREATIVE_RENDER_BASE_DAILY_UNITS`
- `CREATIVE_RENDER_BASE_BURST_JOBS`
- `CREATIVE_RENDER_BASE_MAX_REQUEST_UNITS`
- `CREATIVE_RENDER_PRO_DAILY_UNITS`
- `CREATIVE_RENDER_PRO_BURST_JOBS`
- `CREATIVE_RENDER_PRO_MAX_REQUEST_UNITS`

### Payment/provider evidence

Provider credentials and webhook secrets remain server-only. Readiness consumes names defined in `production_readiness.py`, including Stripe and PayPal credential/price/webhook identifiers. A configured credential is not proof that the provider is reachable or correctly delivering signed production events.

## 4. Readiness state contract

Three states are deliberately separate:

1. `configuration_ready`: static required configuration is structurally acceptable.
2. `serving_ready`: bounded runtime dependency probes required to serve traffic have passed.
3. `production_ready`: production environment, configuration, runtime dependencies and required operational evidence all pass.

A configuration-only CI job must never set `production_ready=true`.

Endpoints:

- `GET /health/live` — process liveness only; no expensive dependency checks.
- `GET /health/ready` — bounded serving-readiness probes; returns non-2xx when serving requirements fail.
- `GET /internal/metrics` — monitoring-token protected operational metrics.

Key metrics include:

- `aura_configuration_ready`
- `aura_serving_ready`
- `aura_restore_evidence_verified`
- `aura_production_ready`

No endpoint may claim external provider verification unless a real provider/network verification occurred.

## 5. Backup and restore evidence

CI command:

```bash
python scripts/run_restore_drill.py --environment ci --output /tmp/chat10-restore-evidence.json
```

This verifies the restore mechanism with isolated synthetic data. Synthetic evidence must retain `production_backup_used=false` and must not be described as a production-backup restore.

A production release candidate additionally requires a controlled restore drill from an actual production backup/snapshot into an isolated recovery environment. The restored data must pass archive/hash checks, SQLite integrity validation, application-level data verification and recorded duration/evidence freshness rules.

Never restore a production backup over live production as a drill.

## 6. Queue/DLQ contract

The shared job queue uses stable optional idempotency keys, bounded serialized payload admission and a dead-letter state for exhausted stale work.

Operational rules:

- Repeated submission with the same `(user_id, job_type, idempotency_key)` must return the authoritative existing job rather than duplicate work.
- Stale leases may be requeued below the retry ceiling.
- Exhausted stale work goes to dead letter.
- Dead-letter replay requires an explicit idempotency verification decision.
- Dead-letter resolution requires an operator reason.
- A generic retry policy must never blindly replay financially or externally side-effecting jobs whose feature owner has not defined idempotent retry semantics.

Distributed queue/backpressure behavior remains a release-evidence requirement where production uses multiple application instances.

## 7. SLS device state contract

Commercial entitlement, native-device policy and protection state are separate.

A device must not be represented as `protected` merely because any of the following are true:

- the member is enrolled;
- a commercial entitlement exists;
- a device record exists;
- a device limit is configured;
- a command was proposed/approved;
- an attestation was requested but not verified.

Protection requires the authoritative native security pipeline to establish the evidence required by the current SLS implementation, including fresh authenticated telemetry/heartbeat and verified protection evidence. Stale, offline, unmanaged, unknown and unverified conditions remain explicitly distinguishable and fail closed.

Signing/attestation status stays unverified unless cryptographic verification actually succeeded. Production HSM/KMS use, platform signing, notarisation and native attestation are evidence gates, not configuration labels.

## 8. SLS privileged command contract

Browser/UI commercial access never grants native command authority.

Privileged native commands require the existing SLS controls applicable to the action, including:

- enrolled device identity;
- strong re-authentication where required;
- typed/allowlisted action parameters;
- bounded approval lifetime;
- server-side command signing;
- command-bound attestation where applicable;
- execute-once/idempotency admission;
- command sequence/anti-rollback checks;
- auditable request/approval/execution/verification state.

The application host must not execute arbitrary member-supplied native shell commands.

## 9. Aura sandbox contract

Aura code execution is available only through the separately configured sandbox transport. The FastAPI host must not execute member/LLM code.

Sandbox requests are bounded and request:

- no network;
- ephemeral filesystem;
- explicit execution timeout;
- bounded code/input and response/output;
- no redirects;
- explicit current-turn/member/project authorization.

Diagnostics must not expose the sandbox bearer token.

## 10. Game Forge execution/export contract

Creator/LLM text is data, not application-host executable code.

Aura playtest runtime uses a restrictive CSP and does not add external network access. Game assets remain tenant-scoped and verified before export.

Aura Web export:

- may be `package_ready=true` when current build, rights, assets and package-integrity checks pass;
- must keep `production_release_ready=false` until independently trusted publisher signing evidence is verified;
- uses aggregate media and asset-count admission before archive construction;
- constructs the archive on disk and streams verified media rather than buffering the full project media set in memory;
- re-verifies package integrity before download;
- does not include server secrets, sessions or creator-private host paths.

Godot source adapter:

- remains a developer/source preview;
- uses a fixed reviewed GDScript template with creator/game text stored as JSON data;
- does not claim Aura runtime parity;
- applies aggregate export admission on the public route;
- remains non-production until a pinned Godot 4 headless validation gate and production release signing evidence are independently verified.

## 11. SSRF/outbound network contract

The Aura Web Gateway must block private/local/reserved address targets, validate redirects, bound response size and restrict schemes/domains according to deployment policy.

Current unresolved release gate: DNS validation and the actual connected peer are not yet cryptographically/transport-bound in a proxy-aware manner. Production DNS-rebinding/TOCTOU protection must be proven without breaking an explicitly approved egress proxy architecture. This is a blocker, not a claimed control.

The configured private SearXNG endpoint is a narrowly scoped internal-service exception and must not become a general private-network fetch bypass.

## 12. Browser request and CSRF contract

Cookie-authenticated unsafe methods are protected by the outer cross-site guard plus session-bound CSRF tokens for designated destructive account actions.

In production:

- state-changing Origin/Referer values are compared only with explicitly configured trusted origins;
- incoming Host is not reflected into the trusted origin list;
- cookie-authenticated writes lacking browser origin/fetch evidence fail closed;
- bearer-authenticated non-browser clients do not depend on ambient-cookie CSRF controls.

The application adds CSP, frame denial, MIME sniffing protection, referrer policy, permissions policy, cache controls and HSTS when served as HTTPS.

Provider webhook POSTs are additionally admitted through a bounded raw-body gate before route code buffers the signed payload. Production operators should keep `LSS_WEBHOOK_MAX_BYTES` at a provider-compatible value no larger than the application edge ceiling and validate real provider delivery in staging.

## 13. Rate limiting and abuse contract

The current authentication sliding-window limiter is process-local memory. It is suitable only as an application-instance guard and must not be described as distributed production rate limiting.

Multi-instance release requires a shared authoritative limiter or equivalent edge/gateway enforcement with evidence covering concurrency, expiry, failure behavior and bypass resistance.

## 14. Upload contract

Upload paths must use tenant-scoped storage, safe filenames, bounded streaming writes and type/rights validation appropriate to the feature domain. Untrusted filenames must never select arbitrary host paths. File-size admission must occur while streaming rather than after loading an unbounded request into memory.

## 15. Security/release evidence contract

Expected CI/security evidence includes:

- source-completeness audit;
- Python compilation;
- full automated test suite;
- isolated restore drill artifact;
- Compose/config validation;
- Caddy validation where applicable;
- committed-secret scan;
- dependency vulnerability audit;
- static application security scan;
- CycloneDX SBOM artifact;
- focused SLS signing/trust/native-execution tests.

A workflow definition committed to the candidate branch is not itself evidence that the workflow ran. Release reports must record the actual run/check result or state the evidence as missing.

## 16. Required external/production evidence

Chat 10 cannot self-generate or fake the following evidence:

- successful real production-backup restore drill;
- production provider/network verification;
- independently trusted native/release signing configuration and key-custody evidence;
- required platform signing/notarisation evidence;
- independent penetration test;
- distributed rate-limit/load evidence;
- production monitoring/alert delivery evidence;
- production rollback exercise evidence.

Missing evidence keeps production readiness false.

## 17. CI/CD and merge boundary

Chat 10 changes are proposed through a dedicated branch/PR into `development/full-site-build`. Chat 10 must not merge itself to production or declare repository-wide release approval.

Actions/checks must be read from GitHub as evidence. If automation-authored events do not trigger checks, that condition is recorded as missing CI evidence and requires an independently triggered run before release.

Branch/ruleset protection is a repository release-control requirement and remains distinct from application code.

## 18. Operational ownership/escalation

- Coin/Gift ledger, financial reversals and economy correctness: escalate to Chat 5/domain owner.
- Shared Sky transport/destination infrastructure: Chat 2/domain owner.
- Shared Sky professional studio: Chat 3/domain owner.
- Live viewer/community realtime: Chat 4/domain owner.
- Battles/multi-host: Chat 6/domain owner.
- Creative studio live integration: Chat 7/domain owner.
- Game Forge product/gameplay behavior: Chat 8/domain owner.
- Creator/Agent/Admin/Owner/social/support workflows: Chat 9/domain owner.
- Repository-wide merge/release decision: Chat 11.

Chat 10 may harden shared production boundaries but must not replace the authoritative business-domain implementation.

## 19. Release evidence handoff

Chat 11 should consume:

- this integration contract;
- PR/check results and artifacts;
- restore evidence;
- SLS trust/signing evidence;
- unresolved blocker register;
- incident/rollback runbooks;
- security scan results/SBOM;
- performance/load/fault evidence;
- browser/accessibility evidence;
- production provider/monitoring evidence.

No percentage, badge or UI label overrides missing evidence.
