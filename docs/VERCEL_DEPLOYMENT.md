# Elevate Souls Productions Command Center — Vercel deployment

The repository supports a Vercel-compatible FastAPI web deployment through `vercel_bootstrap:app`.

A repository configuration, bootstrap file or successful local/serverless build is **not** evidence that a production Vercel project, domain, environment or deployment currently exists. The connected deployment account must independently confirm the target project and live deployment before Vercel can be counted as production infrastructure.

The static file under `preview/` is a working-model artifact only and must not be used as the production application root.

## Expected application surface

The real route set is owned by `app.py`. Representative surfaces include:

- `/` — public/member portal;
- `/dashboard` — member dashboard;
- creative Music, Image, Video and Game routes mounted by the production application;
- Aura intelligence/realtime/workspace routes;
- `/command-center` and private ESP role-gated surfaces;
- `/health/live` — process liveness;
- `/health/ready` — fail-closed deployment readiness.

Authenticated `/internal/metrics` is intentionally excluded from public OpenAPI discovery and requires its monitoring credential when enabled.

Do not duplicate the application into a separate static frontend and do not treat a route existing in source code as proof that a live deployment serves it.

## Vercel storage model

`vercel_bootstrap.py` redirects default runtime writes to `/tmp/esp-command-center` only when the `VERCEL` environment variable is present. This storage is deliberately **ephemeral**. It is suitable for serverless execution/testing but is not durable member, commercial or project storage.

Any production Vercel architecture must therefore provide and verify approved durable persistence for data that cannot safely disappear between function instances, including the production database, project/media storage and any other stateful commercial or user data.

Explicit operator-supplied environment values take precedence over bootstrap defaults.

## Secrets and provider configuration

Provider credentials, payment/webhook secrets, OAuth credentials, owner/admin secrets, monitoring credentials and raw social/provider tokens must be supplied through approved deployment-secret controls. They must never be committed to the repository or embedded in browser-visible code.

Production readiness requires the actual environment to pass the Command Center readiness and security gates; merely setting similarly named variables in documentation is not evidence.

## Git deployment

The intended release source is the approved production release from `main` after the integration and production gates pass.

Do **not** assume that Git integration, project binding, automatic deployment or a production domain exists unless the connected Vercel account confirms it. If a Vercel project is provisioned later, record its verified project identity, production branch, domains, environment separation and rollback procedure as deployment evidence rather than hard-coding an obsolete project claim into this document.

## Production evidence required

Before a Vercel path may be reported as production-ready, verify at minimum:

- the intended Vercel project exists in the authorised account/team;
- the deployment is sourced from the approved release commit/branch;
- the production domain resolves to the intended deployment with valid HTTPS;
- required deployment secrets and provider configuration are present without exposing values;
- durable database/project storage is configured where required;
- `/health/live` succeeds and `/health/ready` reports ready on the real deployment;
- payment/email/provider paths have their separate end-to-end evidence;
- monitoring/logging and alerting are operational;
- a rollback path is documented and tested.

Until those checks exist, Vercel remains a **supported deployment path pending production evidence**, not a confirmed live production environment.
