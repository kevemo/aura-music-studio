# Elevate Souls Productions Content Creation Command Center — Working Test Model

The working test model now follows the same architecture as production: **self-hosted Docker Compose behind Caddy**. There is no supported serverless/Vercel preview target.

Use a separate self-host staging environment with its own domain, database, project storage, credentials and provider sandbox accounts. Do not point staging at production member data or live provider credentials.

## Release path

1. Run local unit/integration tests.
2. Push the candidate branch and require the GitHub CI, security and self-host smoke gates to pass.
3. Deploy the approved candidate to the isolated self-host staging stack.
4. Verify the unified route surface, authentication/role boundaries, renderers, workers, backups and public HTTPS `/health/ready` endpoint.
5. Build the signed production release manifest with `deploy/selfhost/build-release.sh`.
6. Approve that exact immutable manifest through the release process.
7. Deploy with `deploy/selfhost/run-production.sh` using out-of-repository production environment and inference manifests.

The production runner refuses mutable runtime images, missing real HTTPS origins, missing credentials, overlapping GPU assignments, failed vulnerability/signature gates, an unapproved release manifest, or a non-self-host deployment mode.

## Views to test

1. **Regular Member** — creative tools only: Music Studio, Video Studio, Image Designer and Aura Intelligence. ESP social/agency tools are absent.
2. **ESP Creator** — normal creative tools plus niche-specific training, ESP-only Social Manager and LIVE/Video Progress.
3. **ESP Agent** — ESP Creator functionality plus agent-only Creator Roster oversight.
4. **Mary Admin** — owner dashboard, user access controls, ESP approvals, creator progress and audit using Mary's visual/Aura context.
5. **Kev Admin** — same protected owner capabilities using Kev's visual/Aura context.

## Key interaction checks

- Request ESP verification from Regular Member view; private ESP access must remain locked until authorised.
- Switch to ESP Creator and select different niches; training context must remain distinct from regular creative tools.
- Open Social Manager and verify content remains scoped to the ESP-only workspace.
- Switch to ESP Agent and confirm Creator Roster appears only there.
- Switch to Mary/Kev Admin and verify creative subscription state remains independent of ESP role.
- Verify owner approval/decline flows and the owner audit trail.
- Verify project continuity across Music, Image, Video and Game Forge workspaces.
- Verify Game Forge private/public playtest navigation stays bound to the correct Creative project.
- Verify `/health/live` and `/health/ready` through the real staging HTTPS gateway.

## Production integrations

External services are considered connected only when their real credentials and end-to-end checks pass. This includes GPU model services, OAuth publishing/analytics/inbox providers, payment webhooks, SMTP, public DNS/TLS and any other third-party service. The repository contains no fake production credentials or substitute production domains.
