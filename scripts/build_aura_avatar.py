#!/usr/bin/env python3
"""Build the canonical Aura GLB/VRM through externally installed 3D tools.

This script intentionally does not vendor or pretend to implement third-party 3D foundation
models. It orchestrates real configured render/rig/VRM commands, verifies every produced
artifact, and only installs the result when the Live Sound Studio Aura validator accepts it.

Suggested external stages:
- image/multiview -> textured PBR GLB: TRELLIS.2 or Hunyuan3D
- GLB -> humanoid skeleton + skinning: UniRig or RigAnything
- humanoid rig -> VRM 1.0 + facial expressions: a production DCC/VRM export adapter

The final visual likeness must still be manually reviewed against the approved Aura references.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from aura_music_studio.aura_avatar import validate_aura_model


class BuildError(RuntimeError):
    pass


def _run(template: str, values: dict[str, Path | str], stage: str) -> None:
    if not template.strip():
        raise BuildError(f"{stage} command is not configured")
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace("{" + key + "}", shlex.quote(str(value)))
    completed = subprocess.run(rendered, shell=True, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        raise BuildError(
            f"{stage} failed ({completed.returncode}): "
            + (completed.stderr.strip() or completed.stdout.strip() or "unknown error")[-6000:]
        )


def _require_file(path: Path, stage: str, min_bytes: int = 4096) -> None:
    if not path.is_file() or path.stat().st_size < min_bytes:
        raise BuildError(f"{stage} did not produce a valid artifact: {path}")


def build(reference_dir: Path, output: Path) -> dict:
    front = reference_dir / "front.png"
    left = reference_dir / "left.png"
    right = reference_dir / "right.png"
    back = reference_dir / "back.png"
    for required in (front,):
        _require_file(required, "Aura reference input", min_bytes=512)

    image_to_3d_cmd = os.getenv("AURA_AVATAR_IMAGE_TO_3D_CMD", "")
    rig_cmd = os.getenv("AURA_AVATAR_RIG_CMD", "")
    vrm_cmd = os.getenv("AURA_AVATAR_VRM_CMD", "")

    with tempfile.TemporaryDirectory(prefix="aura-avatar-build-") as tmp:
        work = Path(tmp)
        base_mesh = work / "aura_base.glb"
        rigged_mesh = work / "aura_rigged.glb"
        final_model = work / "aura.glb"

        common = {
            "reference_dir": reference_dir,
            "front": front,
            "left": left if left.exists() else "",
            "right": right if right.exists() else "",
            "back": back if back.exists() else "",
        }

        _run(
            image_to_3d_cmd,
            {**common, "output": base_mesh},
            "Aura image-to-3D reconstruction",
        )
        _require_file(base_mesh, "Aura image-to-3D reconstruction")

        _run(
            rig_cmd,
            {**common, "input": base_mesh, "output": rigged_mesh},
            "Aura humanoid auto-rigging",
        )
        _require_file(rigged_mesh, "Aura humanoid auto-rigging")

        _run(
            vrm_cmd,
            {
                **common,
                "input": rigged_mesh,
                "output": final_model,
                "identity": "Aura",
                "spec": "docs/AURA_CANONICAL_3D_CHARACTER.md",
            },
            "Aura VRM 1.0/facial finalization",
        )
        _require_file(final_model, "Aura VRM 1.0/facial finalization")

        validation = validate_aura_model(final_model)
        if not validation.get("ready_for_embodied_runtime"):
            raise BuildError("Final Aura model failed the structural acceptance gate: " + json.dumps(validation, indent=2))

        output.parent.mkdir(parents=True, exist_ok=True)
        staging = output.with_suffix(output.suffix + ".staging")
        shutil.copy2(final_model, staging)
        os.replace(staging, output)
        return {
            "installed": True,
            "output": str(output),
            "bytes": output.stat().st_size,
            "validation": validation,
            "manual_visual_review_required": True,
            "visual_review_spec": "docs/AURA_CANONICAL_3D_CHARACTER.md",
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build and validate the canonical Aura VRM/GLB")
    parser.add_argument(
        "--references",
        default=os.getenv("AURA_AVATAR_REFERENCE_DIR", "data/aura_character_references"),
        help="Directory containing at least front.png; left/right/back are strongly recommended",
    )
    parser.add_argument(
        "--output",
        default=os.getenv("AURA_AVATAR_MODEL_PATH", "aura_music_studio/static/aura/aura.glb"),
        help="Canonical Aura GLB output path",
    )
    args = parser.parse_args()
    try:
        result = build(Path(args.references).resolve(), Path(args.output).resolve())
    except Exception as exc:
        print(f"Aura avatar build failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
