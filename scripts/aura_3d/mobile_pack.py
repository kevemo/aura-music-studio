#!/usr/bin/env python3
"""Package Aura for high-quality mobile/web delivery.

Requires @gltf-transform/cli plus KTX-Software on the dedicated 3D build host.
The pipeline preserves morph targets and animation while applying Meshopt and high-quality
KTX2/Basis compression. The strict Aura production validator runs after this stage.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def _run(cmd: list[str], stage: str) -> None:
    completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(
            f"{stage} failed: " +
            (completed.stderr.strip() or completed.stdout.strip() or f"exit {completed.returncode}")[-6000:]
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--cli", default=os.getenv("AURA_GLTF_TRANSFORM", "gltf-transform"))
    parser.add_argument("--meshopt-level", default=os.getenv("AURA_AVATAR_MESHOPT_LEVEL", "medium"))
    args = parser.parse_args()

    source = Path(args.input).resolve()
    output = Path(args.output).resolve()
    if not source.is_file():
        raise SystemExit(f"Aura mobile input missing: {source}")
    if not shutil.which(args.cli):
        raise SystemExit(
            f"{args.cli!r} is unavailable. Install @gltf-transform/cli on the 3D build host."
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="aura-mobile-pack-") as tmp:
        tmpdir = Path(tmp)
        meshopt = tmpdir / "aura_meshopt.glb"
        ktx2 = tmpdir / "aura_ktx2.glb"

        _run(
            [args.cli, "meshopt", str(source), str(meshopt), "--level", args.meshopt_level],
            "Aura Meshopt compression",
        )
        if not meshopt.is_file():
            raise RuntimeError("Meshopt stage did not create a GLB")

        # UASTC is chosen for Aura's face, emissive circuitry, normals and close conversational
        # framing. It is larger than ETC1S but avoids visible degradation in the canonical identity.
        _run(
            [
                args.cli, "uastc", str(meshopt), str(ktx2),
                "--level", "4", "--rdo", "--rdo-lambda", "4", "--zstd", "18", "--verbose",
            ],
            "Aura KTX2/Basis UASTC texture compression",
        )
        if not ktx2.is_file() or ktx2.stat().st_size < 4096:
            raise RuntimeError("KTX2 stage did not create a usable GLB")

        # A final copy keeps the stage deterministic and avoids any implicit network operation.
        shutil.copy2(ktx2, output)

    print(output)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Aura mobile packaging failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
