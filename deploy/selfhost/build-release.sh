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

GIT_SHA="$(git -C "$ROOT" rev-parse HEAD)"
[[ "$GIT_SHA" =~ ^[0-9a-f]{40}$ ]] || { echo "Could not resolve exact Git SHA" >&2; exit 65; }
[[ -z "$(git -C "$ROOT" status --porcelain --untracked-files=no)" ]] || {
  echo "Refusing release build from a modified tracked working tree" >&2
  exit 65
}

# The registry login is deliberately external to this script. Credentials belong in the
# operator's secret store/credential helper, never on the command line or in the repository.
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

# Scan what was actually pushed. Every HIGH/CRITICAL vulnerability blocks release by default,
# including vulnerabilities without a current fix. Any exception must live in a separately
# reviewed Trivy policy rather than being silently ignored by this release path.
trivy image \
  --exit-code 1 \
  --severity HIGH,CRITICAL \
  --scanners vuln \
  "$IMMUTABLE_IMAGE"

# Production signing never guesses a verification key or silently falls back to public OIDC.
# Paths may also be supported KMS URIs, but both signing and verification identities are explicit.
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
cosign verify --key "$COSIGN_VERIFY_KEY" \
  -a "git_sha=$GIT_SHA" \
  "$IMMUTABLE_IMAGE" >/dev/null

python3 - "$OUTPUT_RELEASE" "$GIT_SHA" "$IMMUTABLE_IMAGE" <<'PY'
import json, os, sys
path, sha, image = sys.argv[1:]
payload = {
    "schema_version": 1,
    "git_sha": sha,
    "command_center_image": image,
    "environment": "production",
    "approved": False,
    "supply_chain": {
        "buildkit_provenance": True,
        "buildkit_sbom": True,
        "trivy_high_critical_gate": True,
        "trivy_unfixed_high_critical_gate": True,
        "cosign_signature_verified": True,
    },
    "notes": "Scanner/signature gates passed. Owner/release approval is still required before deployment."
}
os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
with open(path, "w", encoding="utf-8") as f:
    json.dump(payload, f, indent=2)
    f.write("\n")
print(path)
PY

echo "Release candidate built, scanned and signed: $IMMUTABLE_IMAGE"
echo "Manifest remains approved=false until the production release gate is explicitly approved."
