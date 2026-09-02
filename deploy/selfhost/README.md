# ESP Self-Hosted Production Control Plane

**Elevate Souls Productions Content Creation Command Center — Powered by Aura AI**

This directory makes ESP-owned infrastructure the authoritative and supported production runtime. The release path is self-hosted Linux infrastructure using Docker/Compose for the immediate production topology and the separately gated Kubernetes architecture for future scale. The repository no longer carries a Vercel runtime, bootstrap, deployment configuration or release path.

## Design principles

1. **Provider independent.** Application runtime is standard Linux containers. GPU nodes are standard NVIDIA/CUDA hosts. DNS/CDN, registries, storage, and compute can be replaced without redesigning the product.
2. **No artificial serverless runtime ceiling.** Long music/video/image jobs execute on private workers, not request-duration-limited serverless functions. Capacity grows by adding CPU/GPU/storage nodes. Physical resources and budget still impose real limits.
3. **Fail closed.** Production requires live renderer health, secure cookies, HTTPS, payment/provider secrets, monitoring, backups, and exact release identity.
4. **Immutable releases.** Production promotion uses exact Git SHAs plus immutable container digests. Mutable `latest` tags are forbidden by the release tooling.
5. **Private AI plane.** ACE-Step and Aura inference are private services. Only the Command Center/workers may call them.
6. **GitOps at cluster scale.** Argo CD is the target promotion controller for Kubernetes. CI proves a release; the cluster reconciles declared desired state.
7. **Signed supply chain.** Production application, renderer and inference images must be signed and independently verified before deployment.
8. **Observable by default.** OpenTelemetry + Prometheus metrics, Grafana dashboards, Loki logs and Tempo traces form the target ESP-owned telemetry plane.
9. **Backups are not complete until restored.** Database and asset backups require scheduled restore drills.
10. **Security segmentation.** Internal services remain private by default and public traffic terminates at the controlled ingress boundary.

## Current safe production path — Phase 1

The repository has a functional self-hosted Docker topology. The release path uses:

- `docker-compose.yml`
- `docker-compose.gpu.yml`
- `deploy/production/docker-compose.production.yml`
- `deploy/selfhost/compose.release.yml` for immutable Command Center and ACE-Step images
- `deploy/selfhost/compose.aura-inference.yml` for private Aura inference
- `deploy/selfhost/build-release.sh` to build, scan, sign and record immutable application/renderer images
- `deploy/selfhost/run-production.sh` as the fail-closed production deployment wrapper
- `deploy/Caddyfile` as the public HTTPS ingress

Phase 1 deliberately keeps the Command Center stateful application topology on one host because the current durable database is SQLite. This preserves the tested local-filesystem locking model while allowing dedicated GPU hardware on that host.

Do **not** put `live_sound_studio.sqlite3` on NFS/CephFS/another network filesystem and start multiple Command Center writers. SQLite network-filesystem locking may be unreliable. Horizontal application scaling remains blocked until the PostgreSQL migration gate is complete.

## Target maximum-scale path — Phase 2

The production cluster target is:

```text
Internet
  -> authoritative DNS / optional CDN-WAF
  -> redundant edge/load-balancer addresses
  -> Caddy or Kubernetes Gateway/Ingress
  -> Command Center web/API pool
  -> durable job/event plane
  -> Aura worker pool
  -> GPU render pools (music / image / video / voice)
  -> object storage

Control plane
  Kubernetes HA (3+ control-plane nodes)
  Cilium eBPF networking + default-deny policies
  MetalLB/BGP or equivalent bare-metal load balancing
  Argo CD GitOps
  cert-manager PKI/TLS automation
  Kyverno admission policy
  Cosign signed images/attestations
  Vault/OpenBao-class secret management

State plane
  CloudNativePG PostgreSQL HA + WAL/PITR
  Valkey HA for cache/queue workloads after application adapter integration
  Rook/Ceph for distributed block/object/file storage where operationally justified

AI plane
  NVIDIA GPU Operator
  dedicated GPU node pools by workload/model class
  MIG where hardware supports strong partitioning
  time-slicing only for workloads where shared-memory/fault-isolation trade-offs are acceptable
  queue-depth/latency-based scaling with KEDA after durable distributed queues are integrated

Observability
  OpenTelemetry Collector
  Prometheus
  Grafana
  Loki
  Tempo
  NVIDIA DCGM GPU telemetry
```

## Phase-2 gates

Phase 2 is **not** declared production-ready merely because Kubernetes manifests exist. These gates must pass:

- PostgreSQL adapter/migration covers every current SQLite-backed subsystem.
- Schema/data migration has rollback and parity tests.
- Sessions, background jobs, billing idempotency, ESP private roles and governance cases remain correct under concurrent replicas.
- Generated assets move from host paths to an object-storage abstraction with project/member authorization preserved.
- Renderer result retrieval does not depend on whichever GPU pod receives a later HTTP request.
- Queue state is durable across worker loss/restarts.
- Multi-replica chaos tests prove node/pod loss does not corrupt state.
- Database PITR restore and object-store restore drills are successful.
- Signed-image admission is fail-closed.
- Network policies are tested from both allowed and denied pods.
- Real payment, email, storage and AI-provider/network E2E tests pass in staging.

`control-plane.json` is the machine-readable source of truth for those gates.

## Immediate self-host deployment

1. Provision a Linux host with Docker Engine + Docker Compose v2, `git`, `curl`, Cosign, Trivy, a supported NVIDIA driver and NVIDIA Container Toolkit.
2. Copy `deploy/production/production.env.example` to an external secret location. Populate real operator/provider secrets there; never commit the completed file. Configure DNS so `LSS_PUBLIC_SITE_ADDRESS` resolves to the host and ports 80/443 reach Caddy.
3. Log the trusted Docker client into your private registry using its credential helper or external secret mechanism.
4. Build, scan and sign the exact Command Center and pinned ACE-Step release images:

```bash
ESP_REGISTRY_IMAGE=registry.example/elevate-souls/command-center \
ESP_ACESTEP_REGISTRY_IMAGE=registry.example/elevate-souls/ace-step \
COSIGN_SIGNING_KEY=/secure/path/cosign.key \
COSIGN_VERIFY_KEY=/secure/path/cosign.pub \
ESP_RELEASE_MANIFEST_OUT=/secure/path/release.json \
deploy/selfhost/build-release.sh
```

5. Prepare and verify the private Aura inference model and create its approved inference manifest using `deploy/selfhost/prepare-aura-inference.sh` and `deploy/selfhost/aura_model_integrity.py`. The production wrapper requires an immutable signed vLLM image and a verified model-manifest digest; it never downloads an unverified model at launch.
6. Review the generated release and inference manifests. They remain fail-closed until their explicit `approved` fields and required evidence accurately reflect the completed release review.
7. Launch the exact tested release:

```bash
chmod +x deploy/selfhost/run-production.sh
deploy/selfhost/run-production.sh \
  --env /secure/path/production.env \
  --release /secure/path/release.json \
  --inference /secure/path/aura-inference.json
```

The wrapper verifies the exact Git checkout, clean tracked tree, release manifest, image digests, Cosign signatures, current Trivy vulnerability state, Aura model integrity, required production settings, non-overlapping GPU assignments, Compose topology, GPU visibility, private ACE-Step health, private Aura inference health/model identity, and the public HTTPS `/health/ready` endpoint. It always activates the Caddy public profile; the Social publishing worker is activated only when `AURA_SOCIAL_PUBLISH_WORKER_ENABLED=true`. A failed readiness check leaves the release failed rather than reporting false success.

## Test-before-launch path

Before any production approval, the exact candidate must pass the repository workflows for:

- complete Command Center CI and production-source completeness audit;
- Security Gates;
- integrated Self-Host Smoke;
- Compose validation for base, public, social-publishing, local-AI/GPU and production overlays;
- fail-closed production-readiness contract;
- Caddy configuration validation;
- real-audio safety checks.

Those automated checks establish code/topology readiness. Real domain/TLS resolution, production secrets, external provider approvals, real payment/email delivery, backup-and-restore evidence, monitoring/alert delivery and operational rollback still require evidence from the actual self-host environment and must not be fabricated in source control.

## Kubernetes foundation bootstrap

`bootstrap-k8s.sh` intentionally refuses unpinned component versions. Copy `versions.env.example`, fill it only with versions reviewed for the target cluster, then run the bootstrap from a trusted administrative host.

The bootstrap installs platform operators; it does **not** automatically migrate the application to multi-replica Kubernetes. That remains gated by `control-plane.json`.

## Capacity model

There is no literal unlimited infrastructure. Instead, ESP removes arbitrary platform ceilings and scales these independent dimensions:

- API replicas
- Aura worker replicas
- music GPU nodes
- image GPU nodes
- video GPU nodes
- voice GPU nodes
- PostgreSQL read/HA capacity
- distributed storage capacity
- network capacity

This is the correct definition of a high-ceiling self-hosted platform: capacity is bounded by hardware and budget, not by a serverless provider's function duration or deployment quota.
