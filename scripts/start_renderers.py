#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from aura_music_studio.renderer_host import host_renderer_status, start_renderers, stop_renderers


def main() -> int:
    parser = argparse.ArgumentParser(description="ESP Live Sound Studio neural renderer launcher")
    parser.add_argument("--mode", choices=("ace", "yue", "both"), default="ace")
    parser.add_argument("--env", type=Path, default=Path(".env"))
    parser.add_argument("--no-build", action="store_true", help="Start from existing images without rebuilding")
    parser.add_argument("--stop", action="store_true", help="Stop this renderer topology but preserve model/data volumes")
    parser.add_argument("--doctor", action="store_true", help="Only report Docker/NVIDIA host readiness")
    args = parser.parse_args()

    status = host_renderer_status()
    if args.doctor:
        print(json.dumps(status.__dict__, indent=2))
        return 0 if status.ready_to_start else 2

    if args.stop:
        print(json.dumps(stop_renderers(mode=args.mode, env_path=args.env), indent=2))
        return 0

    if not status.ready_to_start:
        print(json.dumps(status.__dict__, indent=2))
        print("\nRenderer host is not ready. Install/enable Docker, the NVIDIA driver and NVIDIA Container Toolkit first.")
        return 2

    result = start_renderers(mode=args.mode, env_path=args.env, build=not args.no_build)
    print(json.dumps(result, indent=2))
    print("\nModel downloads/initialization can take time on the first start.")
    print("When services are healthy, run the printed next_smoke_test command to prove real waveform generation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
