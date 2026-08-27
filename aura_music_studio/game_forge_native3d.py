from __future__ import annotations

"""Compatibility entrypoint for Aura's native 3D renderer.

The public import path is intentionally stable while the reviewed native runtime advances. Aura3D
v2 adds verified PBR material maps and HRTF spatial audio without introducing external engines,
generated game code, or runtime network access.
"""

from .game_forge_native3d_v2 import _runtime_payload, render_aura3d_playtest

__all__ = ["render_aura3d_playtest", "_runtime_payload"]
