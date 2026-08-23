#!/usr/bin/env python3
"""Pre-install enrollment helper for ESP Live Sound Studio compute nodes.

Uses only the Python standard library. The long-lived node secret is written to .env.node and is not
printed to stdout. For public/non-loopback coordinators HTTPS is required.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import platform
import socket
import ssl
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def hardware() -> dict:
    result = {
        "hostname": socket.gethostname(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
    }
    try:
        text = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"],
            text=True,
            timeout=10,
        ).strip()
        if text:
            result["nvidia_gpus"] = [line.strip() for line in text.splitlines() if line.strip()]
    except Exception:
        pass
    return result


def post_json(url: str, payload: dict) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "ESP-Compute-Node-Bootstrap/1"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30, context=ssl.create_default_context()) as response:
            return json.loads(response.read(1024 * 1024).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read(8192).decode("utf-8", errors="replace")
        raise SystemExit(f"Enrollment failed ({exc.code}): {body}") from exc


def write_env(path: Path, values: dict[str, str]) -> None:
    lines = ["# ESP Live Sound Studio compute-node credential file — do not commit or share"]
    lines.extend(f"{key}={value}" for key, value in sorted(values.items()))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Enroll this machine as an ESP Aura compute node")
    parser.add_argument("--coordinator", required=True, help="HTTPS ESP Live Sound Studio URL")
    parser.add_argument("--name", default=socket.gethostname())
    parser.add_argument("--capabilities", default="music_generation,engineering")
    parser.add_argument("--env", default=".env.node")
    args = parser.parse_args()

    base = args.coordinator.strip().rstrip("/")
    parsed = urllib.parse.urlparse(base)
    loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if parsed.scheme != "https" and not (parsed.scheme == "http" and loopback):
        raise SystemExit("Public ESP compute-node enrollment requires an HTTPS coordinator URL")

    token = getpass.getpass("ESP one-time enrollment token: ").strip()
    if len(token) < 20:
        raise SystemExit("Enrollment token is missing or too short")
    capabilities = [x.strip().lower() for x in args.capabilities.split(",") if x.strip()]
    result = post_json(
        f"{base}/node-coordinator/enroll",
        {
            "token": token,
            "name": args.name,
            "capabilities": capabilities,
            "hardware": hardware(),
            "software": {
                "bootstrap": True,
                "python": platform.python_version(),
                # Once the Docker image starts it reports the exact Studio version during heartbeat.
                "live_sound_studio_version": None,
            },
        },
    )
    target = Path(args.env)
    write_env(target, {
        "LSS_NODE_COORDINATOR_URL": base,
        "LSS_NODE_ID": result["node_id"],
        "LSS_NODE_SECRET": result["node_secret"],
        "LSS_NODE_NAME": result.get("name") or args.name,
        "LSS_NODE_CAPABILITIES": ",".join(result.get("capabilities") or capabilities),
        "LSS_NODE_REQUIRE_SAME_VERSION": "true",
        "LSS_NODE_WORK_DIR": "data/node_work",
    })
    print(json.dumps({
        "enrolled": True,
        "node_id": result["node_id"],
        "name": result.get("name") or args.name,
        "credential_file": str(target),
        "node_secret_printed": False,
        "next": "docker compose -f docker-compose.node.yml up -d --build",
    }, indent=2))


if __name__ == "__main__":
    main()
