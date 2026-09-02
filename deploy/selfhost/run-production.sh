#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE=""
RELEASE_FILE=""
INFERENCE_FILE=""
WAIT_SECONDS="${ESP_DEPLOY_WAIT_SECONDS:-420}"

usage() {
  echo "Usage: $0 --env /secure/production.env --release /secure/release.json --inference /secure/aura-inference.json" >&2
}

while (($#)); do
  case "$1" in
    --env) ENV_FILE="${2:-}"; shift 2 ;;
    --release) RELEASE_FILE="${2:-}"; shift 2 ;;
    --inference) INFERENCE_FILE="${2:-}"; shift 2 ;;
    *) usage; exit 64 ;;
  esac
done

[[ -n "$ENV_FILE" && -f "$ENV_FILE" ]] || { echo "Missing --env file" >&2; exit 64; }
[[ -n "$RELEASE_FILE" && -f "$RELEASE_FILE" ]] || { echo "Missing --release file" >&2; exit 64; }
[[ -n "$INFERENCE_FILE" && -f "$INFERENCE_FILE" ]] || { echo "Missing --inference file" >&2; exit 64; }

for bin in docker curl python3 git cosign trivy; do
  command -v "$bin" >/dev/null || { echo "Required binary missing: $bin" >&2; exit 69; }
done
docker compose version >/dev/null

mapfile -t RELEASE_VALUES < <(python3 - "$RELEASE_FILE" <<'PY'
import json, re, sys
d=json.load(open(sys.argv[1], encoding="utf-8"))
if d.get("schema_version") != 2: raise SystemExit("Unsupported release manifest schema; schema 2 is required")
if d.get("environment") != "production": raise SystemExit("Release manifest is not production")
if d.get("approved") is not True: raise SystemExit("Release manifest is not approved")
sha=str(d.get("git_sha", "")); command=str(d.get("command_center_image", ""))
ace_commit=str(d.get("ace_step_upstream_commit", ""))
if not re.fullmatch(r"[0-9a-f]{40}", sha): raise SystemExit("git_sha must be an exact 40-character lowercase SHA")
if not re.fullmatch(r"[0-9a-f]{40}", ace_commit): raise SystemExit("ace_step_upstream_commit must be an exact reviewed Git SHA")
def pinned(value, name):
    value=str(value or "")
    if not re.search(r"@sha256:[0-9a-f]{64}$", value): raise SystemExit(f"{name} must use an immutable sha256 digest")
    return value
runtime=d.get("runtime_images") or {}
ace=pinned(runtime.get("ace_step"), "runtime_images.ace_step")
caddy=pinned(runtime.get("caddy"), "runtime_images.caddy")
searx=pinned(runtime.get("searxng"), "runtime_images.searxng")
command=pinned(command, "command_center_image")
evidence=d.get("supply_chain") or {}
required=(
    "buildkit_provenance",
    "buildkit_sbom",
    "trivy_high_critical_gate",
    "trivy_unfixed_high_critical_gate",
    "runtime_images_trivy_high_critical_gate",
    "cosign_signature_verified",
    "ace_step_buildkit_provenance",
    "ace_step_buildkit_sbom",
    "ace_step_cosign_signature_verified",
)
missing=[key for key in required if evidence.get(key) is not True]
if missing: raise SystemExit("Release manifest supply-chain evidence is incomplete: " + ", ".join(missing))
print(sha); print(command); print(ace); print(caddy); print(searx); print(ace_commit)
PY
)
EXPECTED_SHA="${RELEASE_VALUES[0]}"
export ESP_COMMAND_CENTER_IMAGE="${RELEASE_VALUES[1]}"
export ESP_ACESTEP_IMAGE="${RELEASE_VALUES[2]}"
export ESP_CADDY_IMAGE="${RELEASE_VALUES[3]}"
export ESP_SEARXNG_IMAGE="${RELEASE_VALUES[4]}"
EXPECTED_ACESTEP_COMMIT="${RELEASE_VALUES[5]}"

mapfile -t INFERENCE_VALUES < <(python3 - "$INFERENCE_FILE" <<'PY'
import json, re, sys
d=json.load(open(sys.argv[1], encoding="utf-8"))
image=str(d.get("vllm_image", "")); served=str(d.get("served_model_name", "")); model_sha=str(d.get("model_manifest_sha256", ""))
if d.get("schema_version") != 1: raise SystemExit("Unsupported Aura inference manifest schema")
if d.get("approved") is not True: raise SystemExit("Aura inference manifest is not approved")
if not re.search(r"@sha256:[0-9a-f]{64}$", image): raise SystemExit("vllm_image must use an immutable sha256 digest")
if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", served): raise SystemExit("Unsafe Aura served model name")
if not re.fullmatch(r"[0-9a-f]{64}", model_sha): raise SystemExit("Invalid Aura model manifest digest")
evidence=d.get("supply_chain") or {}
required=("trivy_high_critical_gate","trivy_unfixed_high_critical_gate","cosign_signature_verified","model_file_hashes_verified")
missing=[key for key in required if evidence.get(key) is not True]
if missing: raise SystemExit("Aura inference supply-chain evidence is incomplete: " + ", ".join(missing))
print(image); print(served); print(model_sha)
PY
)
export AURA_VLLM_IMAGE="${INFERENCE_VALUES[0]}"
export AURA_SELFHOST_LLM_SERVED_NAME="${INFERENCE_VALUES[1]}"
EXPECTED_MODEL_MANIFEST_SHA="${INFERENCE_VALUES[2]}"

ACTUAL_SHA="$(git -C "$ROOT" rev-parse HEAD)"
[[ "$ACTUAL_SHA" == "$EXPECTED_SHA" ]] || { echo "Release identity mismatch: checkout=$ACTUAL_SHA manifest=$EXPECTED_SHA" >&2; exit 65; }
[[ -z "$(git -C "$ROOT" status --porcelain --untracked-files=no)" ]] || { echo "Refusing production deployment from a modified tracked working tree" >&2; exit 65; }

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

required=(
  LSS_PUBLIC_BASE_URL LSS_PUBLIC_SITE_ADDRESS LSS_ADMIN_KEY LSS_PROVENANCE_SECRET ACESTEP_API_KEY
  AURA_MONITORING_TOKEN LSS_BACKUP_AGE_RECIPIENT COSIGN_VERIFY_KEY
  AURA_SELFHOST_LLM_MODEL_DIR AURA_LLM_INTERNAL_API_KEY
  AURA_ACESTEP_CUDA_VISIBLE_DEVICES AURA_LLM_CUDA_VISIBLE_DEVICES
)
for key in "${required[@]}"; do
  [[ -n "${!key:-}" ]] || { echo "Required production secret/config missing: $key" >&2; exit 78; }
done

[[ "${AURA_DEPLOYMENT_ENV:-}" == "production" ]] || { echo "AURA_DEPLOYMENT_ENV must be production" >&2; exit 78; }
[[ "${LSS_DEPLOYMENT_MODE:-}" == "selfhost" ]] || { echo "LSS_DEPLOYMENT_MODE must be selfhost" >&2; exit 78; }
[[ "${LSS_COOKIE_SECURE:-}" == "true" ]] || { echo "LSS_COOKIE_SECURE must be true" >&2; exit 78; }
[[ "${AURA_WEB_ALLOW_HTTP:-}" == "false" ]] || { echo "AURA_WEB_ALLOW_HTTP must be false" >&2; exit 78; }
[[ "${AURA_REQUIRE_LIVE_RENDERER:-}" == "true" ]] || { echo "AURA_REQUIRE_LIVE_RENDERER must be true" >&2; exit 78; }

python3 - "$LSS_PUBLIC_BASE_URL" "$LSS_PUBLIC_SITE_ADDRESS" <<'PY'
from urllib.parse import urlparse
import sys

def origin(raw, name):
    p=urlparse(raw)
    if p.scheme != "https" or not p.hostname or p.username or p.password or p.query or p.fragment:
        raise SystemExit(f"{name} must be a real HTTPS origin without credentials/query/fragment")
    if p.path not in {"", "/"}:
        raise SystemExit(f"{name} must not contain a path")
    host=p.hostname.lower()
    port=f":{p.port}" if p.port and p.port != 443 else ""
    return f"https://{host}{port}"
base=origin(sys.argv[1], "LSS_PUBLIC_BASE_URL")
site=origin(sys.argv[2], "LSS_PUBLIC_SITE_ADDRESS")
if base != site: raise SystemExit("LSS_PUBLIC_BASE_URL and LSS_PUBLIC_SITE_ADDRESS must identify the same HTTPS origin")
PY

python3 - "$AURA_ACESTEP_CUDA_VISIBLE_DEVICES" "$AURA_LLM_CUDA_VISIBLE_DEVICES" <<'PY'
import sys
parse=lambda value: {part.strip() for part in value.split(",") if part.strip()}
a=parse(sys.argv[1]); b=parse(sys.argv[2])
if not a or not b: raise SystemExit("Explicit ACE-Step and Aura GPU assignments are required")
overlap=sorted(a & b)
if overlap: raise SystemExit("ACE-Step and Aura inference GPU assignments overlap: " + ", ".join(overlap))
PY

python3 "$ROOT/deploy/selfhost/aura_model_integrity.py" verify "$AURA_SELFHOST_LLM_MODEL_DIR" "$EXPECTED_MODEL_MANIFEST_SHA" >/dev/null

# Re-verify exact release artifacts at deployment time. ACE-Step must match the reviewed source
# commit recorded in the approved schema-2 release manifest.
cosign verify --key "$COSIGN_VERIFY_KEY" -a "git_sha=$EXPECTED_SHA" "$ESP_COMMAND_CENTER_IMAGE" >/dev/null
trivy image --exit-code 1 --severity HIGH,CRITICAL --scanners vuln "$ESP_COMMAND_CENTER_IMAGE"
cosign verify --key "$COSIGN_VERIFY_KEY" \
  -a "component=ace-step" \
  -a "upstream_commit=$EXPECTED_ACESTEP_COMMIT" \
  "$ESP_ACESTEP_IMAGE" >/dev/null
for image in "$ESP_ACESTEP_IMAGE" "$ESP_CADDY_IMAGE" "$ESP_SEARXNG_IMAGE"; do
  trivy image --exit-code 1 --severity HIGH,CRITICAL --scanners vuln "$image"
done
cosign verify --key "$COSIGN_VERIFY_KEY" -a "component=aura-selfhost-inference" "$AURA_VLLM_IMAGE" >/dev/null
trivy image --exit-code 1 --severity HIGH,CRITICAL --scanners vuln "$AURA_VLLM_IMAGE"

if [[ "${AURA_GPU_REQUIRED:-true}" == "true" ]]; then
  command -v nvidia-smi >/dev/null || { echo "GPU production requires nvidia-smi" >&2; exit 69; }
  nvidia-smi -L >/dev/null || { echo "No usable NVIDIA GPU detected" >&2; exit 69; }
fi

# Caddy is mandatory in production. Social publishing remains explicitly opt-in.
COMPOSE=(docker compose --profile public --env-file "$ENV_FILE")
if [[ "${AURA_SOCIAL_PUBLISH_WORKER_ENABLED:-false}" == "true" ]]; then
  COMPOSE+=(--profile social-publishing)
fi
COMPOSE+=(
  -f "$ROOT/docker-compose.yml"
  -f "$ROOT/docker-compose.gpu.yml"
  -f "$ROOT/deploy/production/docker-compose.production.yml"
  -f "$ROOT/deploy/selfhost/compose.release.yml"
  -f "$ROOT/deploy/selfhost/compose.aura-inference.yml"
)

"${COMPOSE[@]}" config >/dev/null
PULL_SERVICES=(live-sound-studio aura-worker aura-task-worker aura-backup-scheduler aura-address-manager ace-step searxng aura-llm caddy)
REQUIRED_SERVICES=(live-sound-studio aura-worker aura-task-worker aura-backup-scheduler aura-address-manager ace-step searxng aura-llm caddy)
if [[ "${AURA_SOCIAL_PUBLISH_WORKER_ENABLED:-false}" == "true" ]]; then
  PULL_SERVICES+=(esp-social-publish-worker)
  REQUIRED_SERVICES+=(esp-social-publish-worker)
fi
"${COMPOSE[@]}" pull "${PULL_SERVICES[@]}"
"${COMPOSE[@]}" up -d --no-build --remove-orphans

mapfile -t RUNNING < <("${COMPOSE[@]}" ps --services --status running)
for service in "${REQUIRED_SERVICES[@]}"; do
  printf '%s\n' "${RUNNING[@]}" | grep -Fxq "$service" || {
    echo "Required production service is not running: $service" >&2
    "${COMPOSE[@]}" ps >&2 || true
    exit 70
  }
done

"${COMPOSE[@]}" exec -T ace-step curl -fsS http://127.0.0.1:8001/health >/dev/null
"${COMPOSE[@]}" exec -T aura-llm python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=10).read()"

"${COMPOSE[@]}" exec -T live-sound-studio python - <<'PY'
import os, requests
base=os.environ["AURA_LLM_BASE_URL"].rstrip("/")
key=os.environ.get("AURA_LLM_API_KEY", "")
headers={"Authorization": f"Bearer {key}"} if key else {}
r=requests.get(base + "/v1/models", headers=headers, timeout=30)
r.raise_for_status()
models=r.json().get("data") or []
served=os.environ["AURA_INTELLIGENCE_MODEL"]
if not any(str(row.get("id")) == served for row in models):
    raise SystemExit(f"Approved Aura model {served!r} is not served by the private endpoint")
PY

READY_URL="${LSS_PUBLIC_BASE_URL%/}/health/ready"
DEADLINE=$((SECONDS + WAIT_SECONDS))
until curl --fail --silent --show-error --max-time 10 "$READY_URL" >/dev/null; do
  if (( SECONDS >= DEADLINE )); then
    echo "Production readiness failed through public HTTPS ingress: $READY_URL" >&2
    "${COMPOSE[@]}" ps >&2 || true
    "${COMPOSE[@]}" logs --tail=120 live-sound-studio aura-worker ace-step aura-llm caddy >&2 || true
    exit 70
  fi
  sleep 5
done

"${COMPOSE[@]}" ps
echo "ESP authoritative self-host release is READY through $READY_URL (git $EXPECTED_SHA)"
