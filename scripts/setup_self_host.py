#!/usr/bin/env python3
"""Prepare Elevate Souls Productions Command Center self-host settings."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Running from a Git checkout: make the repository root importable without pip install.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aura_music_studio.self_host_setup import initialize_self_host  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare Elevate Souls Productions Content Creation Command Center self-host configuration"
    )
    parser.add_argument("--provider", choices=["none", "direct", "freedns", "duckdns"], default="direct")
    parser.add_argument("--hostname", help="FreeDNS/other free hostname")
    parser.add_argument("--duckdns-subdomain", help="DuckDNS subdomain without .duckdns.org")
    parser.add_argument("--env", default=str(ROOT / ".env"), help="Environment file to create/update")
    args = parser.parse_args()

    result = initialize_self_host(
        provider=args.provider,
        hostname=args.hostname,
        duckdns_subdomain=args.duckdns_subdomain,
        env_path=args.env,
        template_path=ROOT / ".env.example",
    )
    payload = result.__dict__.copy()
    admin_key = payload.pop("admin_key", None)
    print(json.dumps(payload, indent=2))
    if admin_key:
        print("\nNEW ESP OWNER ADMIN KEY — STORE SAFELY; SHOWN ONCE:")
        print(admin_key)
    if result.missing_private_settings:
        print("\nPrivate settings still required in .env:")
        for item in result.missing_private_settings:
            print(f"- {item}")
    print(f"\nNext command:\n{result.next_command}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
