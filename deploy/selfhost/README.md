# ESP Self-Hosted Production Control Plane

**Elevate Souls Productions Content Creation Command Center — Powered by Aura AI**

This directory is the authoritative production control plane for ESP-owned infrastructure. The supported staging/production runtime is self-hosted Linux using Docker/Compose + Caddy for Phase 1, with the separately gated Kubernetes architecture for future horizontal scale. There is no Vercel application runtime or serverless release path; the root `vercel.json` is only a disable switch that prevents any legacy external Git integration from automatically deploying repository pushes.

For the operator-facing authoritative release contract, also read `docs/SELF_HOST_PRODUCTION.md`.

## Phase-1 release topology

The tested single-host production composition uses:

- `docker-compose.yml` — Command Center, workers, private SearXNG, backup/address services and profiles;
- `docker-compose.gpu.yml` — private ACE-Step renderer topology;
- `deploy/production/docker-compose.production.yml` — fail-closed production hardening;
- `deploy/selfhost/compose.release.yml` — immutable release images for Command Center, ACE-Step, Caddy and SearXNG;
- `deploy/selfhost/compose.aura-inference.yml` — isolated private Aura inference;
- `deploy/Caddyfile` — mandatory public HTTPS ingress;
- `deploy/selfhost/build-release.sh` — exact-source image build, SBOM/provenance, vulnerability scanning and Cosign signing;
- `deploy/selfhost/run-production.sh` — release-evidence verification, topology launch and public readiness verification.

Phase 1 intentionally keeps the stateful Command Center writer topology on one host because the current durable database is SQLite. Do not place `live_sound_studio.sqlite3` on a network filesystem and run multiple writers. Horizontal application scaling remains gated by the PostgreSQL/object-storage migration documented in `control-plane.json`.

## Release evidence

Source control deliberately contains no production domain, private signing key, provider secret, approved release digest, approved model manifest or fabricated operational evidence. Production remains fail-closed until the operator supplies real values and evidence outside the repository.

A release requires:

1. exact candidate Git SHA and clean tracked checkout;
2. exact-head Command Center CI, Security Gates and Self-Host Smoke success;
3. schema-2 release manifest produced by `build-release.sh`;
4. Command Center and ACE-Step images built from exact source identities with SBOM/provenance, HIGH/CRITICAL vulnerability gate and Cosign signatures;
5. Caddy and SearXNG pinned to reviewed immutable digests and vulnerability-scanned;
6. approved private Aura-inference manifest with signed immutable vLLM image and verified model-file digest;
7. external production env/secret store containing every required real value;
8. distinct ACE-Step and Aura GPU assignments on the single-host topology;
9. real HTTPS origin configured identically in `LSS_PUBLIC_BASE_URL` and `LSS_PUBLIC_SITE_ADDRESS`;
10. successful private renderer/inference checks and public `/health/ready` through Caddy.

## Build the release artifacts

Authenticate Docker to the approved private registry outside this script, then run from the exact candidate checkout:

```bash
ESP_REGISTRY_IMAGE=registry.example/elevate-souls/command-center \
ESP_ACESTEP_REGISTRY_IMAGE=registry.example/elevate-souls/ace-step \
ESP_CADDY_IMAGE=caddy@sha256:<reviewed-digest> \
ESP_SEARXNG_IMAGE=searxng/searxng@sha256:<reviewed-digest> \
COSIGN_SIGNING_KEY=/secure/cosign.key \
COSIGN_VERIFY_KEY=/secure/cosign.pub \
ESP_RELEASE_MANIFEST_OUT=/secure/release.json \
deploy/selfhost/build-release.sh
```

`build-release.sh` uses the reviewed ACE-Step upstream commit recorded by `ESP_ACESTEP_UPSTREAM_COMMIT` (or the repository-approved default), builds both ESP-controlled images, pushes immutable digests, scans all production runtime images, signs/verifies Command Center and ACE-Step, and writes a non-approved schema-2 release manifest. The manifest stays `approved=false` until the real release review is complete.

Prepare the private Aura model/inference evidence with `prepare-aura-inference.sh`, `aura_model_integrity.py` and the inference-manifest contract in this directory. Model artifacts are not downloaded implicitly during a production launch.

## Launch the exact release

Copy `deploy/production/production.env.example` to an external secret location and fill it with real values. Then:

```bash
chmod +x deploy/selfhost/run-production.sh
deploy/selfhost/run-production.sh \
  --env /secure/production.env \
  --release /secure/release.json \
  --inference /secure/aura-inference.json
```

The launcher validates release/manifests, exact Git identity, clean tree, signatures, current vulnerability state, model integrity, production settings, GPU separation and Compose configuration. It always activates Caddy's `public` profile. It activates `social-publishing` only when `AURA_SOCIAL_PUBLISH_WORKER_ENABLED=true`, and then treats that worker as a required running production service. It refuses `--build`; every production runtime must come from the reviewed immutable release composition.

## Phase-2 scale target

Kubernetes assets in this directory are a foundation, not evidence that multi-replica production is already approved. Phase 2 remains gated on PostgreSQL migration/parity/rollback, object-storage authorization, durable distributed queue semantics, replica/chaos testing, PITR/restore evidence, signed-image admission, tested network policy, and production-representative provider E2E validation.

The platform can scale by adding compute, GPU, storage and network capacity after those gates are satisfied; it is never described as literally unlimited.
