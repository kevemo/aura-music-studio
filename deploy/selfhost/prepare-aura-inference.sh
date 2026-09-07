#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUTPUT="${AURA_INFERENCE_MANIFEST_OUT:-$ROOT/aura-inference-manifest.json}"
IMAGE="${AURA_VLLM_IMAGE:-}"
MODEL_DIR="${AURA_SELFHOST_LLM_MODEL_DIR:-}"
SERVED_NAME="${AURA_SELFHOST_LLM_SERVED_NAME:-aura-reasoning}"

for bin in python3 cosign trivy; do
  command -v "$bin" >/dev/null || { echo "Required inference release tool missing: $bin" >&2; exit 69; }
done
[[ "$IMAGE" =~ @sha256:[0-9a-f]{64}$ ]] || { echo "AURA_VLLM_IMAGE must be an immutable sha256 digest" >&2; exit 78; }
[[ -n "$MODEL_DIR" && -d "$MODEL_DIR" ]] || { echo "AURA_SELFHOST_LLM_MODEL_DIR is required" >&2; exit 78; }
[[ -n "${COSIGN_VERIFY_KEY:-}" ]] || { echo "COSIGN_VERIFY_KEY is required" >&2; exit 78; }

MODEL_MANIFEST_SHA="$(python3 "$ROOT/deploy/selfhost/aura_model_integrity.py" verify "$MODEL_DIR")"

trivy image --exit-code 1 --severity HIGH,CRITICAL --scanners vuln "$IMAGE"
cosign verify --key "$COSIGN_VERIFY_KEY" \
  -a "component=aura-selfhost-inference" \
  "$IMAGE" >/dev/null

python3 - "$OUTPUT" "$IMAGE" "$SERVED_NAME" "$MODEL_MANIFEST_SHA" <<'PY'
import json, os, re, sys
path, image, served, model_sha = sys.argv[1:]
if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", served):
    raise SystemExit("Unsafe served model name")
payload = {
    "schema_version": 1,
    "vllm_image": image,
    "served_model_name": served,
    "model_manifest_sha256": model_sha,
    "approved": False,
    "supply_chain": {
        "trivy_high_critical_gate": True,
        "trivy_unfixed_high_critical_gate": True,
        "cosign_signature_verified": True,
        "model_file_hashes_verified": True,
    },
    "notes": "Runtime image and local model integrity passed. Explicit production approval is still required."
}
os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
with open(path, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2)
    handle.write("\n")
print(path)
PY

echo "Aura inference candidate verified. Manifest remains approved=false until release approval."
