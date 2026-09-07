# Production Readiness — Elevate Souls Productions Content Creation Command Center

Powered by Aura AI.

This document is the deployment contract for the Creative Studios / Game Forge production lane. It does not claim that a deployment is live merely because the repository contains production code. `/health/ready` is the configuration gate; provider reachability, payment-provider operation, GPU capacity and disaster-recovery drills remain deployment responsibilities.

## Non-negotiable environment separation

Production and staging must run from **separate deployment directories/checkouts**. The base Compose file intentionally loads a root `.env` into services, so do not reuse one working directory and swap only the Compose CLI interpolation file.

For staging, copy `deploy/staging/staging.env.example` to `.env` inside the staging deployment directory and fill it only with sandbox/test credentials. For production, copy `deploy/production/production.env.example` to `.env` inside the production deployment directory and fill it only from the production secret store. Never commit either completed file.

The overlays use distinct Compose project names:

- staging: `aura-command-center-staging`
- production: `aura-command-center-production`

That isolates Compose-created networks and named volumes even when the two deployments run on the same Docker host. Separate hosts are preferable for higher-assurance production.

## Payments

The application currently supports PayPal invoice evidence. Browser redirects and manual invoice links are never payment proof.

Production readiness requires:

- `LSS_PAYMENT_PROVIDER=paypal`
- `LSS_PAYMENT_MODE=verified_paypal_invoice` or `verified_paypal_webhook`
- `LSS_PAYPAL_ENVIRONMENT=live`
- non-placeholder `LSS_PAYPAL_CLIENT_ID`, `LSS_PAYPAL_CLIENT_SECRET`, and `LSS_PAYPAL_WEBHOOK_ID`

Staging is hard-wired to `LSS_PAYPAL_ENVIRONMENT=sandbox` by the staging overlay and verified modes also require sandbox verification credentials. A staging configuration attempting to use live PayPal fails readiness.

A verified webhook is stored as evidence; paid membership activation still uses the existing exact plan/currency/amount/payer validation and explicit administrative activation path. This work does **not** claim automatic recurring subscription activation.

## Provider credentials

Real provider keys never belong in Git, screenshots, logs or readiness responses. Configure them in the environment-specific deployment secret store.

`AURA_PRODUCTION_REQUIRED_PROVIDER_SECRETS` is a comma-separated list of uppercase environment-variable names required by that production deployment, for example:

`ELEVENLABS_API_KEY,MUREKA_API_KEY`

Readiness reports only missing **names**, never values. If Google OAuth is partially configured, readiness fails instead of silently presenting it as connected.

## GPU infrastructure

Production uses the base Compose file + `docker-compose.gpu.yml` + the production overlay. ACE-Step stays on the private Compose network; no public GPU inference port is required.

Production configuration requires:

- `AURA_GPU_REQUIRED=true`
- `AURA_REQUIRE_LIVE_RENDERER=true`
- a private `AURA_ACESTEP_API_URL`
- a non-placeholder `ACESTEP_API_KEY`
- an operator-selected minimum VRAM value (`AURA_GPU_MIN_VRAM_GB`, currently 12 GB in the deployment example)

The production overlay fails Compose interpolation if `ACESTEP_API_KEY` is absent. The ACE-Step service retains its health check, persisted model/cache volumes and pinned upstream source revision from the GPU topology.

Staging can run CPU/API-only using the base + staging overlay. To exercise the real GPU path in staging, also include `docker-compose.gpu.yml` and provide staging-only ACE-Step credentials.

## Liveness, readiness and monitoring

- `/health/live` answers only whether the FastAPI process is alive.
- `/health/ready` is a non-network configuration/safety gate and returns HTTP 503 while required production/staging categories are unsafe.
- `/internal/metrics` is excluded from OpenAPI and requires `X-Aura-Monitoring-Token`.

Monitoring behavior is fail-closed:

- monitoring disabled or token unconfigured: HTTP 503
- missing/wrong token: HTTP 403
- correct token: HTTP 200 Prometheus text

Metrics and readiness output never contain provider, owner, provenance or monitoring secret values, and metrics responses are `Cache-Control: no-store`.

The readiness categories are payments, provider credentials, GPU, monitoring, backups, security, storage and deployment identity.

## Backups and disaster recovery

Production readiness requires automatic backups, a 1–24 hour interval, at least seven retained backups and an `age` recipient. The private `age` identity stays offline/outside the application.

The existing backup format uses SQLite's backup API, SQLite integrity checks, SHA-256/size verification, archive traversal/symlink rejection and optional `age` encryption. Deployment secrets are not included.

Run the isolated restore drill against the deployment data paths:

```bash
python -m aura_music_studio.backup_drill
```

`AURA_BACKUP_DRILL_DIR` must be a disposable directory outside the live database/project roots. The drill creates a backup, verifies it, restores it to the isolated drill tree, runs SQLite integrity checking again and never replaces live source state.

A production operator should run and record restore drills on a schedule appropriate to the business recovery objectives. A backup that has never been restored is not treated as proven recoverable.

## Bounded load smoke testing

The built-in probe is deliberately small and guarded:

```bash
python -m aura_music_studio.load_probe http://127.0.0.1:8000/health/live --requests 100 --concurrency 10
```

Hard limits are 500 requests, 20 concurrent workers and 15 seconds per request. Remote targets are refused unless `--allow-remote` is explicitly supplied, and remote probes require HTTPS. Do not use this tool for denial-of-service testing or against infrastructure you do not own/operate.

Run serious capacity tests in staging with production-like data/GPU topology and an agreed traffic model. Validate queue latency and GPU saturation separately from the lightweight HTTP health smoke.

## Mobile and accessibility

Aura3D v4 now applies runtime accessibility hardening at its stable renderer boundary:

- mobile touch movement controls
- minimum 44 px interactive targets
- safe-area-aware mobile layout
- keyboard-focusable game canvas
- visible `:focus-visible` treatment
- ARIA live runtime/status messages
- labelled audio, cutscene and fallback surfaces
- `prefers-reduced-motion` handling

The existing Game Forge creation portal already has a mobile viewport and responsive grid breakpoints. Accessibility remains a release gate: test keyboard-only operation, screen readers, zoom/reflow and representative iOS/Android browsers in staging.

## Production Compose

From the dedicated production deployment directory, after filling its root `.env`:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.gpu.yml \
  -f deploy/production/docker-compose.production.yml \
  --profile public config -q
```

Then deploy using the same file set. The production overlay adds readiness-based service health, `no-new-privileges`, capability drops on application workers, graceful stop windows, log rotation and a required ACE-Step key.

## Staging Compose

From the dedicated staging deployment directory, after filling its root `.env` only with sandbox/test credentials:

```bash
docker compose \
  -f docker-compose.yml \
  -f deploy/staging/docker-compose.staging.yml \
  config -q
```

For staging GPU validation:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.gpu.yml \
  -f deploy/staging/docker-compose.staging.yml \
  config -q
```

Social publishing remains disabled in the staging overlay.

## Release gate

Before production traffic is enabled:

1. Full repository CI is green on the synchronized integration candidate.
2. Repository credential/secret scanning is green.
3. Production and staging Compose configurations validate.
4. `/health/ready` is 200 with the real environment (secret values remain hidden).
5. PayPal live webhook verification is tested with provider-controlled evidence; staging remains sandbox-only.
6. Required AI/provider credentials are installed through the deployment secret store.
7. GPU service health and a real generation smoke are green.
8. Monitoring authentication and alert collection are verified.
9. An encrypted backup has been created and an isolated restore drill has passed.
10. Bounded HTTP load smoke and staging capacity tests meet the agreed SLOs.
11. Mobile, keyboard, screen-reader and reduced-motion checks pass on representative devices/browsers.
12. Security review/penetration testing is complete for the release candidate.

Do not mark the site production-ready merely because source code for these controls exists. The final gate depends on real deployment credentials, real provider accounts, the target GPU hosts and successful staging/operations evidence.
