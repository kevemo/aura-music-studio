# Aura Avatar Client Health Evidence

This runtime turns part of Aura's remaining 3D device-validation work into measurable evidence without confusing telemetry with release approval.

## What it measures

For a signed-in Aura workspace session the browser records only the data needed to assess renderer health:

- coarse client class: `mobile`, `tablet`, `desktop` or `unknown`;
- WebGL and WebGL2 availability;
- whether the 3D renderer was expected/attempted;
- whether the renderer and Aura GLB loaded;
- whether the loaded renderer exposes the layered performance API;
- Aura model load latency;
- a short `requestAnimationFrame` cadence sample;
- whether the cadence sample was taken while the page was hidden/backgrounded;
- one bounded renderer error code rather than raw exception text.

The browser reports one short sample after the Aura page loads. It may also submit on page exit using a small same-origin `fetch(..., keepalive: true)` request.

## Privacy boundary

The health evidence deliberately does **not** collect or persist:

- IP address;
- user-agent string;
- browser or device fingerprint;
- viewport dimensions;
- CPU thread count;
- device memory;
- device pixel ratio;
- geolocation;
- hostname;
- cookies;
- raw renderer exception/error text;
- model or machine identifiers.

The coarse client class is computed in the browser from the mobile hint and viewport breakpoint but only the resulting class is transmitted.

Reports are scoped to the signed-in member and stored in a small rolling SQLite window. The default retention is the latest 24 samples per member and is bounded to a maximum of 100 by the store implementation.

## Health states

Each report is classified into one operational state:

- `webgl_unavailable`
- `renderer_error`
- `renderer_load_incomplete`
- `model_load_incomplete`
- `sample_backgrounded`
- `frame_cadence_degraded`
- `model_load_slow`
- `healthy_3d_session`
- `runtime_capable_no_3d_attempt`

Current conservative health thresholds are:

- frame cadence is degraded below 24 FPS when at least 20 frames were sampled;
- model load is considered slow above 15 seconds.

These are operational diagnostics, not promises that 24 FPS or a 15-second load is an acceptable final production target. Final acceptance should use the intended mobile/desktop quality tiers and deployment hardware matrix.

## Routes

Authenticated JSON routes:

- `POST /aura-intelligence/api/avatar/client-health` — record one bounded sample.
- `GET /aura-intelligence/api/avatar/client-health` — return the member's rolling evidence summary.

Browser script:

- `GET /aura-intelligence/avatar-client-health.js`

The canonical Aura avatar middleware injects the base avatar runtime first and the health script second.

## Readiness authority

Client health evidence has **no release authority**.

The API explicitly returns:

- `readiness_authority: false`
- `production_3d_ready_can_be_promoted_by_client_health: false`
- `operator_validation_still_required: true`

A healthy browser session therefore cannot flip `production_3d_ready`, bypass the GLB performance-rig contract, replace legal/asset clearance, or replace real operator validation.

The evidence is intended to make final device/browser validation easier to prove once the final likeness-grade Aura GLB is installed.
