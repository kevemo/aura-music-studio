#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REGISTRY_IMAGE="${ESP_REGISTRY_IMAGE:-}"
OUTPUT_RELEASE="${ESP_RELEASE_MANIFEST_OUT:-$ROOT/release-manifest.json}"
PLATFORM="${ESP_BUILD_PLATFORM:-linux/amd64}"

usage() {
  cat >&2 <<'EOF'
Usage:
  ESP_REGISTRY_IMAGE=registry.example/elevate-souls/command-center \
  ESP_ACESTEP_IMAGE=registry.example/ace-step@sha256:<digest> \
  ESP_CADDY_IMAGE=caddy@sha256:<digest> \
  ESP_SEARXNG_IMAGE=searxng/searxng@sha256:<digest> \
  COSIGN_SIGNING_KEY=/secure/cosign.key \
  COSIGN_VERIFY_KEY=/secure/cosign.pub \
  deploy/selfhost/build-release.sh

Optional:
  ESP_BUILD_PLATFORM=linux/amd64
  ESP_RELEASE_MANIFEST_OUT=/secure/release.json
EOF
}

[[ -n "$REGISTRY_IMAGE" ]] || { usage; exit 64; }
[[ "$REGISTRY_IMAGE" != *":latest" ]] || { echo "Mutable latest tags are forbidden" >&2; exit 78; }

for bin in docker git python3 cosign trivy; do
  command -v "$bin" >/dev/null || { echo "Required release tool missing: $bin" >&2; exit 69; }
done
docker buildx version >/dev/null

validate_pinned_image() {
  local name="$1" value="$2"
  [[ "$value" =~ @sha256:[0-9a-f]{64}$ ]] || {
    echo "$name must be supplied as an immutable @sha256 digest reference" >&2
    exit 78
  }
}
validate_pinned_image ESP_ACESTEP_IMAGE "${ESP_ACESTEP_IMAGE:-}"
validate_pinned_image ESP_CADDY_IMAGE "${ESP_CADDY_IMAGE:-}"
validate_pinned_image ESP_SEARXNG_IMAGE "${ESP_SEARXNG_IMAGE:-}"

GIT_SHA="$(git -C "$ROOT" rev-parse HEAD)"
[[ "$GIT_SHA" =~ ^[0-9a-f]{40}$ ]] || { echo "Could not resolve exact Git SHA" >&2; exit 65; }
[[ -z "$(git -C "$ROOT" status --porcelain --untracked-files=no)" ]] || {
  echo "Refusing release build from a modified tracked working tree" >&2
  exit 65
}

TAGGED_IMAGE="${REGISTRY_IMAGE}:${GIT_SHA}"
METADATA="$(mktemp)"
trap 'rm -f "$METADATA"' EXIT

docker buildx build \
  --platform "$PLATFORM" \
  --provenance=mode=max \
  --sbom=true \
  --metadata-file "$METADATA" \
  --push \
  -t "$TAGGED_IMAGE" \
  "$ROOT"

DIGEST="$(python3 - "$METADATA" <<'PY'
import json, re, sys
m=json.load(open(sys.argv[1], encoding="utf-8"))
d=m.get("containerimage.digest", "")
if not re.fullmatch(r"sha256:[0-9a-f]{64}", d):
    raise SystemExit("BuildKit did not return an immutable image digest")
print(d)
PY
)"
IMMUTABLE_IMAGE="${REGISTRY_IMAGE}@${DIGEST}"

# Scan the exact command-center image and every externally supplied production runtime image.
for image in "$IMMUTABLE_IMAGE" "$ESP_ACESTEP_IMAGE" "$ESP_CADDY_IMAGE" "$ESP_SEARXNG_IMAGE"; do
  trivy image --exit-code 1 --severity HIGH,CRITICAL --scanners vuln "$image"
done

[[ -n "${COSIGN_SIGNING_KEY:-}" ]] || {
  echo "COSIGN_SIGNING_KEY is required for ESP production release signing" >&2
  exit 78
}
[[ -n "${COSIGN_VERIFY_KEY:-}" ]] || {
  echo "COSIGN_VERIFY_KEY is required for ESP production release verification" >&2
  exit 78
}
cosign sign --yes --key "$COSIGN_SIGNING_KEY" \
  -a "git_sha=$GIT_SHA" \
  -a "product=Elevate Souls Productions Content Creation Command Center" \
  "$IMMUTABLE_IMAGE"
cosign verify --key "$COSIGN_VERIFY_KEY" -a "git_sha=$GIT_SHA" "$IMMUTABLE_IMAGE" >/dev/null

python3 - "$OUTPUT_RELEASE" "$GIT_SHA" "$IMMUTABLE_IMAGE" "$ESP_ACESTEP_IMAGE" "$ESP_CADDY_IMAGE" "$ESP_SEARXNG_IMAGE" <<'PY'
import json, os, sys
path, sha, command, ace_step, caddy, searxng = sys.argv[1:]
payload = {
    "schema_version": 2,
    "git_sha": sha,
    "command_center_image": command,
    "runtime_images": {
        "ace_step": ace_step,
        "caddy": caddy,
        "searxng": searxng,
    },
    "environment": "production",
    "approved": False,
    "supply_chain": {
        "buildkit_provenance": True,
        "buildkit_sbom": True,
        "trivy_high_critical_gate": True,
        "trivy_unfixed_high_critical_gate": True,
        "runtime_images_trivy_high_critical_gate": True,
        "cosign_signature_verified": True,
    },
    "notes": "All production runtime images are digest-pinned and scanned. Owner/release approval is still required before deployment."
}
os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
with open(path, "w", encoding="utf-8") as f:
    json.dump(payload, f, indent=2)
    f.write("\n")
print(path)
PY

echo "Release candidate built, scanned and signed: $IMMUTABLE_IMAGE"
echo "Manifest remains approved=false until the production release gate is explicitly approved."
