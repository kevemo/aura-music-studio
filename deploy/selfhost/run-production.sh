#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE=""
RELEASE_FILE=""
WAIT_SECONDS="${ESP_DEPLOY_WAIT_SECONDS:-420}"

usage() {
  echo "Usage: $0 --env /secure/production.env --release /secure/release.json" >&2
}

while (($#)); do
  case "$1" in
    --env) ENV_FILE="${2:-}"; shift 2 ;;
    --release) RELEASE_FILE="${2:-}"; shift 2 ;;
    *) usage; exit 64 ;;
  esac
done

[[ -n "$ENV_FILE" && -f "$ENV_FILE" ]] || { echo "Missing --env file" >&2; exit 64; }
[[ -n "$RELEASE_FILE" && -f "$RELEASE_FILE" ]] || { echo "Missing --release file" >&2; exit 64; }

for bin in docker curl python3 git cosign trivy; do
  command -v "$bin" >/dev/null || { echo "Required binary missing: $bin" >&2; exit 69; }
done
docker compose version >/dev/null

python3 - "$RELEASE_FILE" <<'PY'
import json, re, sys
p=sys.argv[1]
data=json.load(open(p, encoding="utf-8"))
sha=str(data.get("git_sha", ""))
image=str(data.get("command_center_image", ""))
if data.get("schema_version") != 1:
    raise SystemExit("Unsupported release manifest schema")
if data.get("environment") != "production":
    raise SystemExit("Release manifest is not production")
if data.get("approved") is not True:
    raise SystemExit("Release manifest is not approved")
if not re.fullmatch(r"[0-9a-f]{40}", sha):
    raise SystemExit("git_sha must be an exact 40-character lowercase SHA")
if not re.search(r"@sha256:[0-9a-f]{64}$", image):
    raise SystemExit("command_center_image must use an immutable sha256 digest")
evidence=data.get("supply_chain") or {}
required_evidence=(
    "buildkit_provenance",
    "buildkit_sbom",
    "trivy_high_critical_gate",
    "trivy_unfixed_high_critical_gate",
    "cosign_signature_verified",
)
missing=[key for key in required_evidence if evidence.get(key) is not True]
if missing:
    raise SystemExit("Release manifest supply-chain evidence is incomplete: " + ", ".join(missing))
print(sha)
print(image)
PY

mapfile -t RELEASE_VALUES < <(python3 - "$RELEASE_FILE" <<'PY'
import json, sys
d=json.load(open(sys.argv[1], encoding="utf-8"))
print(d["git_sha"])
print(d["command_center_image"])
PY
)
EXPECTED_SHA="${RELEASE_VALUES[0]}"
export ESP_COMMAND_CENTER_IMAGE="${RELEASE_VALUES[1]}"

ACTUAL_SHA="$(git -C "$ROOT" rev-parse HEAD)"
[[ "$ACTUAL_SHA" == "$EXPECTED_SHA" ]] || {
  echo "Release identity mismatch: checkout=$ACTUAL_SHA manifest=$EXPECTED_SHA" >&2
  exit 65
}

if [[ -n "$(git -C "$ROOT" status --porcelain --untracked-files=no)" ]]; then
  echo "Refusing production deployment from a modified tracked working tree" >&2
  exit 65
fi

# shellcheck disable=SC1090
set -a
source "$ENV_FILE"
set +a

required=(
  LSS_PUBLIC_BASE_URL LSS_ADMIN_KEY LSS_PROVENANCE_SECRET
  ACESTEP_API_KEY AURA_MONITORING_TOKEN LSS_BACKUP_AGE_RECIPIENT
  COSIGN_VERIFY_KEY
)
for key in "${required[@]}"; do
  [[ -n "${!key:-}" ]] || { echo "Required production secret/config missing: $key" >&2; exit 78; }
done

[[ "${AURA_DEPLOYMENT_ENV:-production}" == "production" ]] || { echo "AURA_DEPLOYMENT_ENV must be production" >&2; exit 78; }
[[ "${LSS_COOKIE_SECURE:-}" == "true" ]] || { echo "LSS_COOKIE_SECURE must be true" >&2; exit 78; }
[[ "${AURA_WEB_ALLOW_HTTP:-}" == "false" ]] || { echo "AURA_WEB_ALLOW_HTTP must be false" >&2; exit 78; }
[[ "${AURA_REQUIRE_LIVE_RENDERER:-}" == "true" ]] || { echo "AURA_REQUIRE_LIVE_RENDERER must be true" >&2; exit 78; }

# Re-verify the exact image at deployment time. A manifest cannot substitute for registry trust.
cosign verify --key "$COSIGN_VERIFY_KEY" \
  -a "git_sha=$EXPECTED_SHA" \
  "$ESP_COMMAND_CENTER_IMAGE" >/dev/null

# Re-scan the immutable digest using the deployment host's current vulnerability database.
# All HIGH/CRITICAL findings block launch, whether or not a fix is currently published.
trivy image \
  --exit-code 1 \
  --severity HIGH,CRITICAL \
  --scanners vuln \
  "$ESP_COMMAND_CENTER_IMAGE"

if [[ "${AURA_GPU_REQUIRED:-true}" == "true" ]]; then
  command -v nvidia-smi >/dev/null || { echo "GPU production requires nvidia-smi" >&2; exit 69; }
  nvidia-smi -L >/dev/null || { echo "No usable NVIDIA GPU detected" >&2; exit 69; }
fi

COMPOSE=(
  docker compose
  --env-file "$ENV_FILE"
  -f "$ROOT/docker-compose.yml"
  -f "$ROOT/docker-compose.gpu.yml"
  -f "$ROOT/deploy/production/docker-compose.production.yml"
  -f "$ROOT/deploy/selfhost/compose.release.yml"
)

"${COMPOSE[@]}" config >/dev/null
"${COMPOSE[@]}" pull live-sound-studio aura-worker aura-task-worker aura-backup-scheduler aura-address-manager
"${COMPOSE[@]}" up -d --no-build --remove-orphans

# Prove the renderer internally rather than publishing its port.
"${COMPOSE[@]}" exec -T ace-step curl -fsS http://127.0.0.1:8001/health >/dev/null

READY_URL="${LSS_PUBLIC_BASE_URL%/}/health/ready"
DEADLINE=$((SECONDS + WAIT_SECONDS))
until curl --fail --silent --show-error --max-time 10 "$READY_URL" >/dev/null; do
  if (( SECONDS >= DEADLINE )); then
    echo "Production readiness failed: $READY_URL" >&2
    "${COMPOSE[@]}" ps >&2 || true
    "${COMPOSE[@]}" logs --tail=120 live-sound-studio aura-worker ace-step >&2 || true
    exit 70
  fi
  sleep 5
done

"${COMPOSE[@]}" ps
echo "ESP self-host release is READY at $READY_URL (git $EXPECTED_SHA)"
