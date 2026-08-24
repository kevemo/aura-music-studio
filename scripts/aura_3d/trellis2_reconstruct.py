#!/usr/bin/env python3
"""High-fidelity Aura image-to-3D reconstruction adapter for Microsoft TRELLIS.2.

Run this on a Linux NVIDIA build host with the official microsoft/TRELLIS.2 repository
and gated model dependencies already installed/authorised. This script does not vendor the
foundation model. It produces an intermediate PBR GLB only; it is not allowed to become the
production Aura asset until rigging, facial finalisation, VRM metadata, animation and mobile
optimisation pass the Live Sound Studio production gate.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _fail(message: str) -> None:
    raise SystemExit(f"TRELLIS.2 Aura reconstruction failed: {message}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Canonical front/three-quarter Aura image")
    parser.add_argument("--output", required=True, help="Intermediate PBR GLB")
    parser.add_argument("--model", default=os.getenv("AURA_TRELLIS2_MODEL", "microsoft/TRELLIS.2-4B"))
    parser.add_argument("--decimation-target", type=int, default=int(os.getenv("AURA_TRELLIS2_DECIMATION", "220000")))
    parser.add_argument("--texture-size", type=int, default=int(os.getenv("AURA_TRELLIS2_TEXTURE_SIZE", "4096")))
    args = parser.parse_args()

    source = Path(args.input).resolve()
    output = Path(args.output).resolve()
    if not source.is_file():
        _fail(f"reference image does not exist: {source}")

    try:
        import torch
    except Exception as exc:  # pragma: no cover - build-host only
        _fail(f"PyTorch unavailable: {exc}")
    if not torch.cuda.is_available():  # pragma: no cover - build-host only
        _fail("CUDA GPU is required")

    try:  # pragma: no cover - build-host only
        props = torch.cuda.get_device_properties(0)
        vram_gb = props.total_memory / (1024 ** 3)
    except Exception:
        vram_gb = 0
    min_vram = float(os.getenv("AURA_TRELLIS2_MIN_VRAM_GB", "23"))
    if vram_gb and vram_gb < min_vram:
        _fail(f"{vram_gb:.1f} GB VRAM detected; configured minimum is {min_vram:.1f} GB")

    try:  # pragma: no cover - build-host only
        os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")
        os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
        from PIL import Image
        from trellis2.pipelines import Trellis2ImageTo3DPipeline
        import o_voxel
    except Exception as exc:
        _fail(
            "Official TRELLIS.2 environment is not installed. Clone microsoft/TRELLIS.2 "
            f"and install its dependencies first. Import error: {exc}"
        )

    image = Image.open(source).convert("RGBA")
    pipeline = Trellis2ImageTo3DPipeline.from_pretrained(args.model)
    pipeline.cuda()
    mesh = pipeline.run(image)[0]
    # Keep a high-fidelity intermediate, then let the dedicated mobile stage perform the
    # final character-aware simplification after rigging and facial morph creation.
    mesh.simplify(16777216)
    output.parent.mkdir(parents=True, exist_ok=True)
    glb = o_voxel.postprocess.to_glb(
        vertices=mesh.vertices,
        faces=mesh.faces,
        attr_volume=mesh.attrs,
        coords=mesh.coords,
        attr_layout=mesh.layout,
        voxel_size=mesh.voxel_size,
        aabb=[[-0.5, -0.5, -0.5], [0.5, 0.5, 0.5]],
        decimation_target=max(100000, int(args.decimation_target)),
        texture_size=max(1024, min(4096, int(args.texture_size))),
        remesh=True,
        remesh_band=1,
        remesh_project=0,
        verbose=True,
    )
    # TRELLIS.2 can export PBR GLB directly. WebP here is only intermediate; the final
    # mobile packager is required to convert textures to KTX2/Basis.
    glb.export(str(output), extension_webp=True)
    if not output.is_file() or output.stat().st_size < 4096:
        _fail("TRELLIS.2 did not create a usable GLB")
    print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
