#!/usr/bin/env python3
"""Rig Aura with VAST-AI-Research SkinTokens / TokenRig.

SkinTokens is the 2026 successor to UniRig. It predicts a skeleton and dense skinning weights
in a unified autoregressive pass. This adapter deliberately uses the upstream CLI rather than
forking model code into Live Sound Studio.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--root", default=os.getenv("AURA_SKINTOKENS_ROOT", ""))
    parser.add_argument("--python", default=os.getenv("AURA_SKINTOKENS_PYTHON", sys.executable))
    args = parser.parse_args()

    source = Path(args.input).resolve()
    output = Path(args.output).resolve()
    root = Path(args.root).expanduser().resolve() if args.root else None
    if not source.is_file():
        raise SystemExit(f"SkinTokens input is missing: {source}")
    if not root or not (root / "demo.py").is_file():
        raise SystemExit(
            "AURA_SKINTOKENS_ROOT must point at an installed clone of "
            "VAST-AI-Research/SkinTokens"
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        args.python,
        str(root / "demo.py"),
        "--input", str(source),
        "--output", str(output),
        "--use_transfer",
        "--use_postprocess",
    ]
    completed = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise SystemExit(
            "SkinTokens rigging failed: " +
            (completed.stderr.strip() or completed.stdout.strip() or f"exit {completed.returncode}")[-6000:]
        )
    if not output.is_file() or output.stat().st_size < 4096:
        raise SystemExit("SkinTokens completed without producing a usable rigged GLB")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
