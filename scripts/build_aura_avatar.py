#!/usr/bin/env python3
"""Build the canonical Aura production GLB/VRM through real external 3D tools.

This orchestrator is deliberately fail-closed. A mesh that merely loads in Three.js is not
sufficient. The candidate must pass reconstruction, rigging, facial/morph finalisation,
authored animation, VRM 1.0 finalisation, mobile packaging, structural validation and the
strict Aura production-quality gate before it can atomically replace the deployed model.

Recommended 2026 build-host stack:
- high-fidelity PBR reconstruction: Microsoft TRELLIS.2
- skeleton + skinning: VAST-AI-Research SkinTokens / TokenRig
- face: production DCC/face-rig stage providing VRM presets + Aura custom expressions +
  ARKit-compatible detailed morph targets
- animation: authored/retargeted Aura action clips
- VRM: VRM 1.0 humanoid + expressions + LookAt + SpringBone
- mobile packaging: KTX2/Basis textures + Meshopt-compatible glTF compression

No third-party foundation model is vendored into Live Sound Studio. Build-host adapters are
configured by environment commands and must create real artifacts or fail.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from aura_music_studio.aura_avatar import validate_aura_model
from aura_music_studio.aura_avatar_quality import validate_aura_production_model


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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _stage_command(env_name: str, default: str = "") -> str:
    return (os.getenv(env_name) or default).strip()


def build(reference_dir: Path, output: Path) -> dict:
    front = reference_dir / "front.png"
    left = reference_dir / "left.png"
    right = reference_dir / "right.png"
    back = reference_dir / "back.png"
    face = reference_dir / "face.png"
    turnaround = reference_dir / "turnaround.png"
    _require_file(front, "Aura reference input", min_bytes=512)

    commands = {
        "reconstruct": _stage_command("AURA_AVATAR_IMAGE_TO_3D_CMD"),
        "rig": _stage_command("AURA_AVATAR_RIG_CMD"),
        "face": _stage_command("AURA_AVATAR_FACE_CMD"),
        "animate": _stage_command("AURA_AVATAR_ANIMATION_CMD"),
        "vrm": _stage_command("AURA_AVATAR_VRM_CMD"),
        "mobile": _stage_command("AURA_AVATAR_MOBILE_CMD"),
    }

    with tempfile.TemporaryDirectory(prefix="aura-avatar-build-") as tmp:
        work = Path(tmp)
        base_mesh = work / "01_aura_pbr.glb"
        rigged_mesh = work / "02_aura_rigged.glb"
        facial_mesh = work / "03_aura_face.glb"
        animated_mesh = work / "04_aura_animated.glb"
        vrm_model = work / "05_aura_vrm.glb"
        mobile_model = work / "06_aura_mobile.glb"

        common = {
            "reference_dir": reference_dir,
            "front": front,
            "left": left if left.exists() else "",
            "right": right if right.exists() else "",
            "back": back if back.exists() else "",
            "face": face if face.exists() else front,
            "turnaround": turnaround if turnaround.exists() else front,
            "spec": "docs/AURA_CANONICAL_3D_CHARACTER.md",
            "identity": "Aura",
        }

        _run(commands["reconstruct"], {**common, "output": base_mesh}, "Aura PBR image-to-3D reconstruction")
        _require_file(base_mesh, "Aura PBR image-to-3D reconstruction")

        _run(commands["rig"], {**common, "input": base_mesh, "output": rigged_mesh}, "Aura humanoid rigging and skinning")
        _require_file(rigged_mesh, "Aura humanoid rigging and skinning")

        _run(
            commands["face"],
            {**common, "input": rigged_mesh, "output": facial_mesh},
            "Aura facial rig, viseme and expression finalisation",
        )
        _require_file(facial_mesh, "Aura facial rig, viseme and expression finalisation")

        _run(
            commands["animate"],
            {**common, "input": facial_mesh, "output": animated_mesh},
            "Aura authored animation and locomotion finalisation",
        )
        _require_file(animated_mesh, "Aura authored animation and locomotion finalisation")

        _run(
            commands["vrm"],
            {**common, "input": animated_mesh, "output": vrm_model},
            "Aura VRM 1.0, LookAt, SpringBone and semantic-material finalisation",
        )
        _require_file(vrm_model, "Aura VRM 1.0 finalisation")

        runtime_validation = validate_aura_model(vrm_model)
        if not runtime_validation.get("ready_for_embodied_runtime"):
            raise BuildError(
                "Aura failed the pre-mobile VRM runtime gate: " + json.dumps(runtime_validation, indent=2)
            )

        _run(
            commands["mobile"],
            {**common, "input": vrm_model, "output": mobile_model},
            "Aura mobile GLB packaging and compression",
        )
        _require_file(mobile_model, "Aura mobile GLB packaging and compression")

        production_validation = validate_aura_production_model(mobile_model)
        if not production_validation.get("production_ready"):
            raise BuildError(
                "Final Aura model failed the strict production gate: "
                + json.dumps(production_validation, indent=2)
            )

        output.parent.mkdir(parents=True, exist_ok=True)
        staging = output.with_suffix(output.suffix + ".staging")
        shutil.copy2(mobile_model, staging)
        os.replace(staging, output)
        return {
            "installed": True,
            "output": str(output),
            "bytes": output.stat().st_size,
            "sha256": _sha256(output),
            "runtime_validation": validate_aura_model(output),
            "production_validation": validate_aura_production_model(output),
            "manual_visual_review_required": True,
            "visual_review_spec": "docs/AURA_CANONICAL_3D_CHARACTER.md",
            "reference_files": {
                name: str(path) for name, path in {
                    "front": front, "left": left, "right": right, "back": back,
                    "face": face, "turnaround": turnaround,
                }.items() if path.exists()
            },
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build and strictly validate the canonical Aura production VRM/GLB")
    parser.add_argument(
        "--references",
        default=os.getenv("AURA_AVATAR_REFERENCE_DIR", "data/aura_character_references"),
        help="Reference directory. front.png is required; left/right/back/face/turnaround are strongly recommended.",
    )
    parser.add_argument(
        "--output",
        default=os.getenv("AURA_AVATAR_MODEL_PATH", "aura_music_studio/static/aura/aura.glb"),
        help="Canonical Aura production GLB output path",
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
