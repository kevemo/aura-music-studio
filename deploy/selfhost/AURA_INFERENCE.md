# Aura AI — ESP-owned self-hosted inference

Aura's existing model adapter is already offline-first and supports an OpenAI-compatible local endpoint. This layer makes that endpoint a private ESP-controlled vLLM service rather than introducing a second Aura architecture.

## Runtime boundary

`compose.aura-inference.yml` puts vLLM on an `internal: true` Docker network with no host `ports:` mapping. Only the Command Center web process and Aura worker join that network. vLLM's API key is defense in depth; network isolation is authoritative because vLLM documents that its API key covers `/v1`, `/v2`, and `/inference` prefixes but not every inference-capable endpoint.

Production uses only locally held model weights mounted read-only at `/models/aura`. The runtime does not need to download model weights from Hugging Face or another model hub.

## Recommended model classes

The runtime is model-agnostic. For an ESP-owned reasoning deployment, OpenAI's Apache-2.0 gpt-oss models are strong candidates:

- `gpt-oss-120b` — primary high-capacity Aura reasoning; designed to fit on a single 80-GB GPU.
- `gpt-oss-20b` — lower-latency, lower-memory Aura tier/failover; can run in about 16 GB of memory.

The actual production model must still pass ESP quality, tool-routing, safety, latency and licence/policy review. A model name in documentation is not production evidence.

## Model integrity

After obtaining model weights through an approved source and reviewing their licence/model card, place them on ESP-controlled storage and seal them:

```bash
python3 deploy/selfhost/aura_model_integrity.py seal /srv/esp/models/aura-primary
```

The command writes `MODEL_SHA256SUMS.json` and prints its SHA-256 digest. It rejects symlinks and non-regular files, requires `config.json` plus recognized model weights, and hashes every file.

Production verification recomputes every file hash. Extra, missing or changed model files fail deployment.

## Inference runtime supply chain

Mirror the reviewed exact vLLM image digest into the ESP Harbor registry. Scan it and sign the mirrored digest with the ESP Cosign signing key using the annotation:

```text
component=aura-selfhost-inference
```

Then generate an inference candidate manifest:

```bash
AURA_VLLM_IMAGE='registry.example/esp/vllm-openai@sha256:...' \
AURA_SELFHOST_LLM_MODEL_DIR=/srv/esp/models/aura-primary \
COSIGN_VERIFY_KEY=/secure/esp-cosign.pub \
AURA_INFERENCE_MANIFEST_OUT=/secure/aura-inference.json \
deploy/selfhost/prepare-aura-inference.sh
```

The candidate remains `approved=false`. Promotion is a separate explicit release decision.

## GPU isolation

The single-host production wrapper requires explicit non-overlapping GPU assignments for ACE-Step and Aura inference:

```text
AURA_ACESTEP_CUDA_VISIBLE_DEVICES=0
AURA_LLM_CUDA_VISIBLE_DEVICES=1
```

This prevents music rendering and a large reasoning model from silently fighting over the same VRAM. On a production cluster, use separate GPU node pools/taints/affinity for reasoning, music, image, video and voice workloads.

## Production execution

Production now consumes both the Command Center release manifest and Aura inference manifest:

```bash
deploy/selfhost/run-production.sh \
  --env /secure/production.env \
  --release /secure/release.json \
  --inference /secure/aura-inference.json
```

The wrapper verifies the exact vLLM image signature and vulnerability state again, verifies all local model-file hashes, checks disjoint GPU assignments, starts the private inference service, proves its health, then proves Command Center `/health/ready` before reporting the release as ready.

## Scale-out

The Docker path is the safe immediate single-host runtime. Multi-node Aura inference moves to Kubernetes only after the existing PostgreSQL/object-storage/durable-queue gates are complete. At that point vLLM can use dedicated GPU pools and tensor/data parallelism, while KEDA/HPA can scale stateless frontends and queue-backed workers based on real workload metrics.
