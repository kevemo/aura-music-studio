# Authoritative self-host production deployment

The Elevate Souls Productions Content Creation Command Center, powered by Aura AI, has one production architecture: the repository's Docker Compose stack behind Caddy on ESP-controlled/self-hosted infrastructure.

A serverless deployment is not a supported staging or production target. The retained `vercel.json` contains only `git.deploymentEnabled=false` so a legacy external Git integration cannot automatically deploy repository pushes. It contains no build, function, install or runtime configuration.

## Production contract

Production deployment requires all of the following before `deploy/selfhost/run-production.sh` will report READY:

- an exact approved Git commit and clean tracked working tree;
- a schema-2 release manifest created by `deploy/selfhost/build-release.sh`;
- the Command Center image and ACE-Step, Caddy and SearXNG runtime images pinned by immutable SHA-256 digest;
- current HIGH/CRITICAL vulnerability scans for those exact images;
- verified Command Center Cosign signature and approved Aura inference manifest;
- a real external production environment file kept outside the repository;
- `AURA_DEPLOYMENT_ENV=production` and `LSS_DEPLOYMENT_MODE=selfhost`;
- one real HTTPS origin supplied as both `LSS_PUBLIC_BASE_URL` and `LSS_PUBLIC_SITE_ADDRESS`;
- real production secrets/provider credentials where the enabled feature requires them;
- separate explicit GPU assignments for ACE-Step and Aura inference;
- verified Aura model-file integrity;
- the mandatory Caddy `public` profile;
- all required production containers running;
- ACE-Step and private Aura inference health checks passing;
- the application seeing the approved Aura model through its private network;
- the public HTTPS `/health/ready` endpoint succeeding through Caddy.

The repository deliberately does not contain production domains, private keys, provider secrets, release digests or an already-approved release manifest. Those are deployment evidence, not source code. Missing real values fail closed instead of being replaced with demo data.

## Release sequence

1. Candidate code passes the repository CI, security gates and integrated self-host smoke.
2. Operators choose and record immutable approved runtime-image digests.
3. `deploy/selfhost/build-release.sh` builds, scans and signs the Command Center image, scans the pinned runtime images, and writes a schema-2 release manifest with `approved=false`.
4. Release approval changes only the verified real manifest used by the deployment process; the repository template remains non-runnable.
5. An approved Aura inference manifest is supplied out of repository storage.
6. The production environment is supplied from the deployment secret store.
7. `deploy/selfhost/run-production.sh` verifies all release evidence, starts the complete production stack with Caddy public ingress, and validates the public readiness URL.

## Staging

Staging uses the same self-host architecture with isolated staging storage, credentials, provider sandbox accounts and its own HTTPS origin. It must not share production member data, production OAuth credentials, production payment credentials, or production release approval evidence.
