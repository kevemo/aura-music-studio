# Pulsar-Frequency House — Vercel deployment

The only canonical Vercel project for this product is `pulsar-frequency-house`.

Vercel must serve the real FastAPI application through `vercel_bootstrap:app`. The static file under `preview/` is a safe working-model artifact only and must not be used as the production root.

## Expected live routes

- `/` — public/member portal
- `/dashboard` — member dashboard
- `/studio` and the production/music routes mounted by `app.py`
- `/image-studio` / `/video-studio` routes mounted by `media_studios.py`
- `/aura-intelligence` and Aura realtime/workspace routes
- `/command-center` — private ESP gateway
- `/command-center/social` — private ESP Social Management
- `/health` — runtime health report

The exact route set is owned by `app.py`; do not duplicate it in a separate static frontend.

## Vercel storage model

`vercel_bootstrap.py` redirects test/runtime writes to `/tmp` only when `VERCEL` is present. This is deliberately ephemeral and suitable for interactive testing, not durable member data. Production persistence should use the project's approved durable database/object-storage architecture.

Provider credentials, OAuth secrets, admin secrets and raw social access tokens must be configured only through deployment secrets and must never be committed.

## Canonical Git deployment trigger

The canonical `pulsar-frequency-house` Vercel project is expected to deploy automatically from the GitHub `main` branch. This marker exists only to force a fresh Git-backed deployment after the FastAPI Vercel runtime correction, so the production domain replaces the older manual/static `vercel deploy` build with the real application runtime.
