# ESP Self-Hosted Production Control Plane

**Elevate Souls Productions Content Creation Command Center — Powered by Aura AI**

This directory makes ESP-owned infrastructure the authoritative production runtime. Vercel is optional preview tooling only and is not required for the application, Aura workers, GPU renderers, storage, database, or release process.

## Design principles

1. **Provider independent.** Application runtime is standard Linux containers. GPU nodes are standard NVIDIA/CUDA hosts. DNS/CDN, registries, storage, and compute can be replaced without redesigning the product.
2. **No artificial serverless runtime ceiling.** Long music/video/image jobs execute on private workers, not request-duration-limited serverless functions. Capacity grows by adding CPU/GPU/storage nodes. Physical resources and budget still impose real limits.
3. **Fail closed.** Production requires live renderer health, secure cookies, HTTPS, payment/provider secrets, monitoring, backups, and exact release identity.
4. **Immutable releases.** Production promotion should use an exact Git SHA plus immutable container digests. Do not deploy mutable `latest` tags.
5. **Private AI plane.** ACE-Step and future video/image/voice renderers are private services. Only the Command Center/workers may call them.
6. **GitOps at cluster scale.** Argo CD is the target promotion controller for Kubernetes. CI proves a release; the cluster reconciles declared desired state.
7. **Signed supply chain.** Production images should be signed with Cosign and admission-verified with Kyverno before Kubernetes promotion.
8. **Observable by default.** OpenTelemetry + Prometheus metrics, Grafana dashboards, Loki logs and Tempo traces form the target ESP-owned telemetry plane.
9. **Backups are not complete until restored.** Database PITR and object-store backups require scheduled restore drills.
10. **Security segmentation.** Cilium/Kubernetes network policies default-deny internal traffic and explicitly allow only required service flows.

## Current safe production path — Phase 1

The repository already has a functional self-hosted Docker topology. Use:

- `docker-compose.yml`
- `docker-compose.gpu.yml`
- `deploy/production/docker-compose.production.yml`
- `deploy/selfhost/compose.release.yml` for immutable release images
- `deploy/selfhost/run-production.sh` as the fail-closed deployment wrapper

Phase 1 deliberately keeps the Command Center stateful application topology on one host because the current durable database is SQLite. This preserves the tested local-filesystem locking model while allowing dedicated GPU hardware on that host.

Do **not** put `live_sound_studio.sqlite3` on NFS/CephFS/another network filesystem and start multiple Command Center writers. SQLite explicitly warns that network filesystem locking may be unreliable. Horizontal application scaling remains blocked until the PostgreSQL migration gate is complete.

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

1. Provision a Linux host with Docker Engine + Docker Compose v2 and a supported NVIDIA driver/container runtime.
2. Copy `deploy/production/production.env.example` to an external secret location; never commit the populated file.
3. Create a release manifest from `release-manifest.example.json`, replacing the Git SHA and image digest with the exact tested release.
4. Run:

```bash
chmod +x deploy/selfhost/run-production.sh
deploy/selfhost/run-production.sh \
  --env /secure/path/production.env \
  --release /secure/path/release.json
```

The wrapper validates release identity, required secrets, Compose topology, GPU visibility, the private ACE-Step health endpoint, and the public `/health/ready` endpoint. A failed readiness check leaves the release failed rather than reporting a false success.

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
