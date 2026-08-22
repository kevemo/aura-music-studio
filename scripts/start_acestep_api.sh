#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ACE="$ROOT/engines/ace-step-1.5"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required. Install it first, then rerun this script." >&2
  exit 1
fi
if [[ ! -d "$ACE" ]]; then
  echo "ACE-Step 1.5 is not cloned at $ACE. Run: aura engines --bootstrap" >&2
  exit 1
fi

echo "Aura Music Studio — starting ACE-Step 1.5 REST API"
cd "$ACE"
if [[ ! -d .venv ]]; then
  echo "Preparing ACE-Step environment with uv sync..."
  uv sync
fi

if [[ -n "${ACESTEP_API_KEY:-}" ]]; then
  exec uv run acestep-api --api-key "$ACESTEP_API_KEY"
else
  echo "Starting local API without an API key. Keep this bound to a trusted machine/network."
  exec uv run acestep-api
fi
