#!/usr/bin/env python3
"""Generate Aura's first real rigged GLB through the public VAST-AI AniGen Space.

This script is intended to run in GitHub Actions where outbound HTTPS is available. It does
not pretend a 2D preview is a 3D model: success requires a binary glTF containing a skin,
joints and mesh primitives. The generated asset is an intermediate reconstruction/rig input
for the stricter Aura VRM/face/energy pipeline, not automatically the production canonical
`aura.glb`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import struct
import sys
from pathlib import Path
from typing import Any

from gradio_client import Client, handle_file


def _extract_paths(value: Any) -> list[Path]:
    paths: list[Path] = []
    if isinstance(value, str):
        p = Path(value)
        if p.exists():
            paths.append(p)
    elif isinstance(value, dict):
        for key in ("path", "name", "value"):
            item = value.get(key)
            if isinstance(item, str):
                p = Path(item)
                if p.exists():
                    paths.append(p)
        for item in value.values():
            paths.extend(_extract_paths(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            paths.extend(_extract_paths(item))
    return paths


def _glb_json(path: Path) -> dict[str, Any]:
    with path.open("rb") as fh:
        header = fh.read(12)
        if len(header) != 12:
            raise RuntimeError("GLB header is incomplete")
        magic, version, total = struct.unpack("<4sII", header)
        if magic != b"glTF" or version != 2 or total != path.stat().st_size:
            raise RuntimeError("AniGen output is not a valid glTF 2.0 binary")
        while fh.tell() < total:
            chunk_header = fh.read(8)
            if len(chunk_header) != 8:
                break
            length, kind = struct.unpack("<II", chunk_header)
            payload = fh.read(length)
            if kind == 0x4E4F534A:
                return json.loads(payload.decode("utf-8").rstrip("\x00 \t\r\n"))
    raise RuntimeError("GLB JSON chunk is missing")


def _validate_rigged_glb(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size < 4096:
        raise RuntimeError("AniGen did not produce a usable GLB")
    doc = _glb_json(path)
    meshes = doc.get("meshes") or []
    skins = doc.get("skins") or []
    nodes = doc.get("nodes") or []
    primitives = sum(len(mesh.get("primitives") or []) for mesh in meshes)
    joints = sorted({int(j) for skin in skins for j in (skin.get("joints") or [])})
    skinned_nodes = [node for node in nodes if node.get("skin") is not None]
    if not meshes or primitives < 1:
        raise RuntimeError("AniGen GLB has no mesh primitives")
    if not skins or len(joints) < 15 or not skinned_nodes:
        raise RuntimeError(
            f"AniGen GLB is not sufficiently rigged (skins={len(skins)}, joints={len(joints)}, skinned_nodes={len(skinned_nodes)})"
        )
    return {
        "bytes": path.stat().st_size,
        "meshes": len(meshes),
        "primitives": primitives,
        "skins": len(skins),
        "joint_nodes": len(joints),
        "skinned_nodes": len(skinned_nodes),
        "materials": len(doc.get("materials") or []),
        "images": len(doc.get("images") or []),
        "animations": len(doc.get("animations") or []),
        "extensions_used": doc.get("extensionsUsed") or [],
    }


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _client(space: str) -> Client:
    token = os.getenv("HF_TOKEN", "").strip() or None
    kwargs = {"hf_token": token} if token else {}
    return Client(space, **kwargs)


def _call(client: Client, api_name: str, *args):
    print(f"[Aura3D] Calling {api_name} ...", flush=True)
    return client.predict(*args, api_name=api_name)


def generate(input_image: Path, output: Path, metadata: Path) -> dict[str, Any]:
    if not input_image.is_file():
        raise RuntimeError(f"Aura reference image not found: {input_image}")

    client = _client("VAST-AI/AniGen")
    try:
        api = client.view_api(return_format="dict")
        metadata.parent.mkdir(parents=True, exist_ok=True)
        (metadata.parent / "anigen_api.json").write_text(json.dumps(api, indent=2, default=str), encoding="utf-8")
    except Exception as exc:
        print(f"[Aura3D] Could not persist API schema: {exc}", flush=True)

    # Gradio State components are session-managed by gradio_client and therefore omitted from
    # the public function arguments. Use the same Client instance for the whole chained flow.
    _call(client, "/prepare_input_for_generation", handle_file(str(input_image)))

    # ss_flow_duet is the AniGen option documented for a more detailed skeleton, including
    # character fingers. slat_flow_auto lets the model choose a coherent joint count.
    preview = _call(
        client,
        "/generate_preview",
        42,                # deterministic seed
        "ss_flow_duet",   # detailed humanoid skeleton
        "slat_flow_auto",
        8.0,               # sparse-structure guidance
        30,                # sparse-structure sampling steps
        3.5,               # structured-latent guidance
        30,                # structured-latent sampling steps
        1,                 # ignored by auto joint-count mode
    )
    print(f"[Aura3D] Preview returned: {type(preview).__name__}", flush=True)

    extracted = _call(
        client,
        "/extract_glb",
        2048,  # texture resolution
        0.88,  # high-detail but mobile-friendly starting simplification
        True,  # fill holes
    )

    candidates = [p for p in _extract_paths(extracted) if p.suffix.lower() == ".glb"]
    if not candidates:
        raise RuntimeError(f"AniGen extraction returned no local GLB path: {extracted!r}")

    # First Model3D output from extract_glb is the textured rigged mesh; the second may be a
    # skeleton visualisation. Validate candidates and pick the richest valid skinned model.
    valid: list[tuple[int, Path, dict[str, Any]]] = []
    for candidate in candidates:
        try:
            info = _validate_rigged_glb(candidate)
            score = info["joint_nodes"] * 1000 + info["materials"] * 10 + info["bytes"] // 1024
            valid.append((score, candidate, info))
        except Exception as exc:
            print(f"[Aura3D] Rejected candidate {candidate}: {exc}", flush=True)
    if not valid:
        raise RuntimeError("AniGen returned GLB files, but none passed the rigged-mesh acceptance gate")

    valid.sort(key=lambda item: item[0], reverse=True)
    _, chosen, info = valid[0]
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(chosen, output)
    final_info = _validate_rigged_glb(output)
    result = {
        "generator": "VAST-AI/AniGen",
        "source": "Hugging Face ZeroGPU",
        "status": "rigged_intermediate_ready",
        "canonical_production_ready": False,
        "reason_not_canonical": (
            "The AniGen output is a real rigged/skinned GLB, but production Aura still requires "
            "canonical humanoid mapping, detailed facial morphs, VRM 1.0 expressions/LookAt/SpringBone, "
            "semantic emissive eye/heart/circuit materials, authored interaction clips, mobile packaging "
            "and manual likeness approval."
        ),
        "output": str(output),
        "sha256": _sha256(output),
        **final_info,
    }
    metadata.parent.mkdir(parents=True, exist_ok=True)
    metadata.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2), flush=True)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--metadata", required=True)
    args = parser.parse_args()
    try:
        generate(Path(args.input), Path(args.output), Path(args.metadata))
    except Exception as exc:
        print(f"Aura AniGen build failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
