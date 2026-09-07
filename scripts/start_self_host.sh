#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PROVIDER="${LSS_SETUP_PROVIDER:-direct}"
HOSTNAME_VALUE="${LSS_SETUP_HOSTNAME:-}"
DUCK_SUB="${LSS_SETUP_DUCKDNS_SUBDOMAIN:-}"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required to launch the self-hosted Command Center stack." >&2
  exit 1
fi

if [[ ! -f .env ]]; then
  args=(python3 scripts/setup_self_host.py --provider "$PROVIDER")
  [[ -n "$HOSTNAME_VALUE" ]] && args+=(--hostname "$HOSTNAME_VALUE")
  [[ -n "$DUCK_SUB" ]] && args+=(--duckdns-subdomain "$DUCK_SUB")
  "${args[@]}"
  echo
  echo "Review .env and add any private DDNS/SMTP credentials shown as missing, then run this script again."
  exit 0
fi

if grep -Eq '^LSS_DDNS_PROVIDER=(direct|freedns|duckdns)$' .env; then
  docker compose --profile public up -d --build
else
  docker compose up -d --build
fi

echo
printf 'Elevate Souls Productions Content Creation Command Center started. Local owner access: http://127.0.0.1:8000\n'
printf 'Run: docker compose exec live-sound-studio aura public-address --refresh\n'
