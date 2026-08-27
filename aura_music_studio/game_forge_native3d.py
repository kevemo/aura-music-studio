from __future__ import annotations

"""Compatibility entrypoint for Aura's native 3D renderer.

The public import path stays stable while the reviewed native runtime advances. Aura3D v3 retains
v2 PBR material maps and HRTF spatial audio and adds server-validated glTF/GLB static model meshes
without introducing external engines, generated game code, or browser model-network loading.
"""

from .game_forge_native3d_v3 import _runtime_payload, render_aura3d_playtest

__all__ = ["render_aura3d_playtest", "_runtime_payload"]
