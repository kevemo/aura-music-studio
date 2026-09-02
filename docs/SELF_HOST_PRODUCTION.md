# Authoritative self-host production deployment

The Elevate Souls Productions Content Creation Command Center, powered by Aura AI, has one supported staging/production architecture: the repository's Docker Compose stack behind Caddy on ESP-controlled/self-hosted infrastructure.

A serverless deployment is not a supported staging or production target. The retained root `vercel.json` contains only `git.deploymentEnabled=false` so a legacy external Git integration cannot automatically deploy repository pushes. It contains no build, function, install or runtime configuration.

## Production contract

Production deployment requires all of the following before `deploy/selfhost/run-production.sh` will report READY:

- an exact approved Git commit and clean tracked working tree;
- a schema-2 release manifest created by `deploy/selfhost/build-release.sh`;
- the Command Center image pinned by immutable digest, built with SBOM/provenance, vulnerability-scanned and Cosign-signed;
- ACE-Step built by ESP from the exact reviewed upstream commit, pinned by immutable digest, built with SBOM/provenance, vulnerability-scanned and Cosign-signed;
- Caddy and SearXNG pinned by reviewed immutable SHA-256 digests and vulnerability-scanned;
- verified Command Center and ACE-Step signatures at deployment time;
- an approved Aura inference manifest with immutable signed vLLM image and verified model-file integrity;
- a real external production environment file kept outside the repository;
- `AURA_DEPLOYMENT_ENV=production` and `LSS_DEPLOYMENT_MODE=selfhost`;
- one real HTTPS origin supplied as both `LSS_PUBLIC_BASE_URL` and `LSS_PUBLIC_SITE_ADDRESS`;
- real production secrets/provider credentials where the enabled feature requires them;
- separate explicit GPU assignments for ACE-Step and Aura inference;
- the mandatory Caddy `public` profile;
- the Social publishing profile only when `AURA_SOCIAL_PUBLISH_WORKER_ENABLED=true`;
- all enabled required production containers running;
- ACE-Step and private Aura inference health checks passing;
- the application seeing the approved Aura model through its private network;
- the public HTTPS `/health/ready` endpoint succeeding through Caddy.

The repository deliberately does not contain production domains, private keys, provider secrets, release digests or an already-approved release manifest. Those are deployment evidence, not source code. Missing real values fail closed instead of being replaced with demo data.

## Release sequence

1. Candidate code passes exact-head Command Center CI, Security Gates and integrated Self-Host Smoke.
2. Operators select reviewed immutable Caddy and SearXNG digests and configure the private registry/signing identities outside source control.
3. `deploy/selfhost/build-release.sh` builds the Command Center from the exact candidate Git SHA and ACE-Step from the exact reviewed upstream SHA; both builds emit SBOM/provenance and are scanned, signed and verified. The script also scans the selected Caddy/SearXNG digests and writes a schema-2 release manifest with `approved=false`.
4. Release approval changes only the verified real manifest used by the deployment process; the repository template remains deliberately non-runnable.
5. An approved Aura inference manifest and verified model directory are supplied from protected out-of-repository storage.
6. The complete production environment is supplied from the deployment secret store using `deploy/production/production.env.example` as its key contract.
7. `deploy/selfhost/run-production.sh --env ... --release ... --inference ...` re-verifies all release evidence, starts the immutable production stack with Caddy public ingress, optionally starts Social publishing only when enabled, verifies required containers and private model/renderer paths, and validates the public readiness URL.

## Staging

Staging uses the same self-host architecture with isolated staging storage, credentials, provider sandbox accounts and its own HTTPS origin. It must not share production member data, production OAuth credentials, production payment credentials, production release approval evidence or production signing material.

## Scope of repository readiness

Repository CI can prove implementation, route composition, security regressions, source completeness, container topology and fail-closed deployment contracts. A real launch still requires external evidence from the actual ESP-controlled host: domain/DNS/TLS, production secrets, provider approvals, payment/email E2E, monitoring/alert delivery, backup-and-restore drill, rollback rehearsal and capacity/failure testing. Those values and outcomes must never be fabricated to make a release appear ready.
