from __future__ import annotations

"""Compatibility and safety entrypoint for Aura's native 3D renderer.

Aura3D v3 retains v2 PBR material maps and HRTF spatial audio and adds server-validated glTF/GLB
static model meshes. The public import path stays stable and this boundary enforces an aggregate
expanded-vertex budget before the reviewed renderer serializes model geometry into playtest HTML.
"""

import os

from .game_forge_native3d_v3 import _runtime_payload as _v3_runtime_payload
from .game_forge_native3d_v3 import render_aura3d_playtest as _render_v3

_MAX_RUNTIME_MODEL_VERTICES = max(
    3,
    int(os.getenv("AURA_GAME_RUNTIME_MODEL_MAX_VERTICES", "250000")),
)


def _runtime_payload(game, world) -> dict:
    payload = _v3_runtime_payload(game, world)
    total_vertices = sum(
        int(row.get("mesh", {}).get("vertex_count") or 0)
        for row in payload.get("models", [])
    )
    if total_vertices > _MAX_RUNTIME_MODEL_VERTICES:
        raise ValueError(
            f"Aura3D model geometry exceeds the {_MAX_RUNTIME_MODEL_VERTICES} expanded-vertex runtime budget"
        )
    payload["runtime_contract"]["model_runtime_vertex_budget"] = _MAX_RUNTIME_MODEL_VERTICES
    payload["runtime_contract"]["model_runtime_vertex_count"] = total_vertices
    return payload


def render_aura3d_playtest(game, world, *, csp: str) -> str:
    # Validate the exact model set before the renderer serializes it. The renderer performs the same
    # checksum/parser verification again while building its closed payload; no raw model is loaded by JS.
    _runtime_payload(game, world)
    return _render_v3(game, world, csp=csp)


__all__ = ["render_aura3d_playtest", "_runtime_payload"]
