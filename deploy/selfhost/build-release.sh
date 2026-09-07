#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REGISTRY_IMAGE="${ESP_REGISTRY_IMAGE:-}"
ACESTEP_REGISTRY_IMAGE="${ESP_ACESTEP_REGISTRY_IMAGE:-}"
ACESTEP_UPSTREAM_COMMIT="${ESP_ACESTEP_UPSTREAM_COMMIT:-14c0211d5a0653b0f63e27686f4c3f151b4d8629}"
OUTPUT_RELEASE="${ESP_RELEASE_MANIFEST_OUT:-$ROOT/release-manifest.json}"
PLATFORM="${ESP_BUILD_PLATFORM:-linux/amd64}"

usage() {
  cat >&2 <<'EOF'
Usage:
  ESP_REGISTRY_IMAGE=registry.example/elevate-souls/command-center \
  ESP_ACESTEP_REGISTRY_IMAGE=registry.example/elevate-souls/ace-step \
  ESP_CADDY_IMAGE=caddy@sha256:<digest> \
  ESP_SEARXNG_IMAGE=searxng/searxng@sha256:<digest> \
  COSIGN_SIGNING_KEY=/secure/cosign.key \
  COSIGN_VERIFY_KEY=/secure/cosign.pub \
  deploy/selfhost/build-release.sh

Optional:
  ESP_BUILD_PLATFORM=linux/amd64
  ESP_RELEASE_MANIFEST_OUT=/secure/release.json
  ESP_ACESTEP_UPSTREAM_COMMIT=<reviewed ACE-Step commit SHA>
EOF
}

[[ -n "$REGISTRY_IMAGE" && -n "$ACESTEP_REGISTRY_IMAGE" ]] || { usage; exit 64; }
[[ "$REGISTRY_IMAGE" != *":latest" && "$ACESTEP_REGISTRY_IMAGE" != *":latest" ]] || {
  echo "Mutable latest tags are forbidden" >&2
  exit 78
}
[[ "$ACESTEP_UPSTREAM_COMMIT" =~ ^[0-9a-f]{40}$ ]] || {
  echo "ESP_ACESTEP_UPSTREAM_COMMIT must be an exact reviewed Git SHA" >&2
  exit 78
}

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
validate_pinned_image ESP_CADDY_IMAGE "${ESP_CADDY_IMAGE:-}"
validate_pinned_image ESP_SEARXNG_IMAGE "${ESP_SEARXNG_IMAGE:-}"

GIT_SHA="$(git -C "$ROOT" rev-parse HEAD)"
[[ "$GIT_SHA" =~ ^[0-9a-f]{40}$ ]] || { echo "Could not resolve exact Git SHA" >&2; exit 65; }
[[ -z "$(git -C "$ROOT" status --porcelain --untracked-files=no)" ]] || {
  echo "Refusing release build from a modified tracked working tree" >&2
  exit 65
}

[[ -n "${COSIGN_SIGNING_KEY:-}" ]] || {
  echo "COSIGN_SIGNING_KEY is required for ESP production release signing" >&2
  exit 78
}
[[ -n "${COSIGN_VERIFY_KEY:-}" ]] || {
  echo "COSIGN_VERIFY_KEY is required for ESP production release verification" >&2
  exit 78
}

COMMAND_TAGGED_IMAGE="${REGISTRY_IMAGE}:${GIT_SHA}"
ACESTEP_TAGGED_IMAGE="${ACESTEP_REGISTRY_IMAGE}:${ACESTEP_UPSTREAM_COMMIT}"
COMMAND_METADATA="$(mktemp)"
ACESTEP_METADATA="$(mktemp)"
trap 'rm -f "$COMMAND_METADATA" "$ACESTEP_METADATA"' EXIT

# Build both ESP-controlled runtime images from exact source identities with SBOM/provenance.
docker buildx build \
  --platform "$PLATFORM" \
  --provenance=mode=max \
  --sbom=true \
  --metadata-file "$COMMAND_METADATA" \
  --push \
  -t "$COMMAND_TAGGED_IMAGE" \
  "$ROOT"

docker buildx build \
  --platform "$PLATFORM" \
  --provenance=mode=max \
  --sbom=true \
  --metadata-file "$ACESTEP_METADATA" \
  --push \
  -t "$ACESTEP_TAGGED_IMAGE" \
  "https://github.com/ace-step/ACE-Step-1.5.git#${ACESTEP_UPSTREAM_COMMIT}"

read_digest() {
  python3 - "$1" <<'PY'
import json, re, sys
metadata=json.load(open(sys.argv[1], encoding="utf-8"))
digest=metadata.get("containerimage.digest", "")
if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
    raise SystemExit("BuildKit did not return an immutable image digest")
print(digest)
PY
}

COMMAND_DIGEST="$(read_digest "$COMMAND_METADATA")"
ACESTEP_DIGEST="$(read_digest "$ACESTEP_METADATA")"
IMMUTABLE_COMMAND_IMAGE="${REGISTRY_IMAGE}@${COMMAND_DIGEST}"
IMMUTABLE_ACESTEP_IMAGE="${ACESTEP_REGISTRY_IMAGE}@${ACESTEP_DIGEST}"

# Scan the exact production images. HIGH/CRITICAL vulnerabilities fail the release.
for image in "$IMMUTABLE_COMMAND_IMAGE" "$IMMUTABLE_ACESTEP_IMAGE" "$ESP_CADDY_IMAGE" "$ESP_SEARXNG_IMAGE"; do
  trivy image --exit-code 1 --severity HIGH,CRITICAL --scanners vuln "$image"
done

cosign sign --yes --key "$COSIGN_SIGNING_KEY" \
  -a "git_sha=$GIT_SHA" \
  -a "product=Elevate Souls Productions Content Creation Command Center" \
  "$IMMUTABLE_COMMAND_IMAGE"
cosign sign --yes --key "$COSIGN_SIGNING_KEY" \
  -a "component=ace-step" \
  -a "upstream_commit=$ACESTEP_UPSTREAM_COMMIT" \
  "$IMMUTABLE_ACESTEP_IMAGE"

cosign verify --key "$COSIGN_VERIFY_KEY" -a "git_sha=$GIT_SHA" "$IMMUTABLE_COMMAND_IMAGE" >/dev/null
cosign verify --key "$COSIGN_VERIFY_KEY" \
  -a "component=ace-step" \
  -a "upstream_commit=$ACESTEP_UPSTREAM_COMMIT" \
  "$IMMUTABLE_ACESTEP_IMAGE" >/dev/null

python3 - "$OUTPUT_RELEASE" "$GIT_SHA" "$IMMUTABLE_COMMAND_IMAGE" "$IMMUTABLE_ACESTEP_IMAGE" "$ACESTEP_UPSTREAM_COMMIT" "$ESP_CADDY_IMAGE" "$ESP_SEARXNG_IMAGE" <<'PY'
import json, os, sys
path, sha, command, ace_step, ace_step_commit, caddy, searxng = sys.argv[1:]
payload = {
    "schema_version": 2,
    "git_sha": sha,
    "command_center_image": command,
    "runtime_images": {
        "ace_step": ace_step,
        "caddy": caddy,
        "searxng": searxng,
    },
    "ace_step_upstream_commit": ace_step_commit,
    "environment": "production",
    "approved": False,
    "supply_chain": {
        "buildkit_provenance": True,
        "buildkit_sbom": True,
        "trivy_high_critical_gate": True,
        "trivy_unfixed_high_critical_gate": True,
        "runtime_images_trivy_high_critical_gate": True,
        "cosign_signature_verified": True,
        "ace_step_buildkit_provenance": True,
        "ace_step_buildkit_sbom": True,
        "ace_step_cosign_signature_verified": True,
    },
    "notes": "Command Center and ACE-Step were built from exact source identities, scanned and signed; Caddy/SearXNG are digest-pinned and scanned. Owner/release approval is still required before deployment."
}
os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
with open(path, "w", encoding="utf-8") as f:
    json.dump(payload, f, indent=2)
    f.write("\n")
print(path)
PY

echo "Command Center release candidate built, scanned and signed: $IMMUTABLE_COMMAND_IMAGE"
echo "ACE-Step release image built from $ACESTEP_UPSTREAM_COMMIT, scanned and signed: $IMMUTABLE_ACESTEP_IMAGE"
echo "Manifest remains approved=false until the production release gate is explicitly approved."
