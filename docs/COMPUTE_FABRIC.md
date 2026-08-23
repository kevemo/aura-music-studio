# ESP Live Sound Studio — Aura Compute Fabric

**Elevate Souls Productions Presents: The Live Sound Studio**  
**Powered by Aura**

Aura Compute Fabric lets additional ESP-controlled computers contribute music-generation and audio-engineering compute without becoming web servers and without receiving direct access to the Studio membership database.

## Design goals

- ESP owns the coordinator and compute nodes.
- Worker nodes make outbound HTTPS connections to the Studio coordinator.
- Worker nodes require no public inbound ports, DDNS hostname or paid domain.
- Every node has its own revocable credential.
- The ESP owner creates short-lived, single-use enrollment tokens.
- Node secrets are stored as hashes on the coordinator.
- Nodes advertise capabilities and only lease compatible jobs.
- Member projects are transferred as per-job, checksummed bundles.
- Results are checksummed before the coordinator merges them.
- Remote nodes cannot replace project ownership manifests, asset indexes or rights ledgers.
- Long renders renew their leases so the same job is not accidentally run twice.

## Coordinator workflow

1. Sign in to `/owner`.
2. Open `/owner/compute-nodes`.
3. Create a one-time enrollment code.
4. The enrollment code expires automatically and is invalid after one successful exchange.
5. On the ESP compute machine run:

```bash
aura node-enroll --coordinator https://YOUR-STUDIO-HOST
```

The CLI securely prompts for the one-time enrollment token. It writes the long-lived node credential to `.env.node` and does not print the node secret back to the terminal.

6. Test the machine:

```bash
aura node-doctor
aura node-run-once --env .env.node
```

7. Run continuously:

```bash
aura node-worker --env .env.node
```

Or run the isolated GPU node stack:

```bash
docker compose -f docker-compose.node.yml up -d --build
```

## Node-only Docker stack

`docker-compose.node.yml` runs:

- `aura-compute-node` — the outbound worker agent;
- `ace-step` — the node-local ACE-Step 1.5 REST renderer;
- persistent model/checkpoint/Hugging Face caches;
- a private temporary work volume.

It does **not** run:

- the public website;
- membership accounts;
- the ESP owner dashboard;
- PayPal/payment administration;
- SMTP/email;
- the master Studio SQLite database;
- Caddy/DDNS/public-address services.

The ACE-Step API is not published to the LAN/Internet; it is exposed only inside the node's private Compose network.

## Capability routing

Current capability names include:

| Capability | Eligible jobs |
|---|---|
| `music_generation` | full-song production, Build Around Upload |
| `engineering` | split, master, Aura Tune, restoration, spatial renders |
| `stem_separation` | splitter only |
| `mastering` | mastering only |
| `autotune` | Aura Tune only |
| `restoration` | restoration only |
| `spatial_audio` | spatial processing only |

A node can advertise multiple capabilities. The coordinator atomically leases only a compatible queued job.

## Project transfer security

The coordinator creates a ZIP64 job bundle containing the tenant-scoped project files required for that job and a manifest with SHA-256 hashes.

The bundle excludes coordinator-only revision and rights ledgers and does not give the node the membership database.

The node rejects unsafe paths and verifies each file hash before rendering.

After rendering, the node returns a separate result bundle. The coordinator accepts only:

- generated `output/` files;
- generated `work/` files except revision history;
- `aura_session.json`;
- `aura_status.json`.

The node cannot replace source project manifests, `assets.json`, membership data or rights records through the result protocol.

Before a valid remote result is merged, the coordinator creates a normal Studio revision snapshot.

## Job leases

When a node claims a job, the master queue records `worker_id=node:<node-id>`.

While the job is running the node renews the lease through the authenticated coordinator. If connectivity disappears long enough for the lease to expire, the coordinator may requeue the work. A stale node's later result is rejected because it no longer owns the lease.

This is designed to prevent duplicate/stale workers from overwriting a newer result.

## Revocation

The ESP owner can revoke a node from `/owner/compute-nodes`.

Revocation immediately makes its node credential invalid. The coordinator stores no plaintext copy of the credential that can be recovered later; enroll the machine again if it must rejoin.

## Network requirements

A compute node needs only outbound connectivity to the coordinator URL. For Internet-connected nodes, use the Studio's HTTPS free hostname/public address.

Plain HTTP is refused by the node agent except for loopback testing unless the owner deliberately enables `LSS_NODE_ALLOW_HTTP=true`. HTTPS is strongly recommended even when nodes are on an ESP-controlled LAN because job bundles can contain private audio, lyrics and production directions.

## Physical limits

The compute fabric removes a software dependency on paid cloud GPU APIs, but it does not make GPU computation physically free. Capacity scales with the ESP-owned machines, GPUs, electricity, storage and network bandwidth available.

The architecture is intentionally horizontal: additional authorized machines can be enrolled later without redesigning the customer application or payment system.
