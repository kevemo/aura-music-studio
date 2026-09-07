from __future__ import annotations

"""Compatibility and safety entrypoint for Aura's native 3D renderer.

Aura3D v4 retains v3 static model meshes, v2 PBR material maps and HRTF spatial audio, then adds
closed declarative cinematics and built-in bounded particle VFX. The public import path stays stable
and this boundary continues to enforce the aggregate expanded-vertex model budget before rendering.
"""

import json
import os

from .game_forge_accessibility import harden_game_runtime_html
from .game_forge_native3d_v4 import _runtime_payload as _v4_runtime_payload
from .game_forge_native3d_v4 import render_aura3d_playtest as _render_v4

_MAX_RUNTIME_MODEL_VERTICES = max(
    3,
    int(os.getenv("AURA_GAME_RUNTIME_MODEL_MAX_VERTICES", "250000")),
)

_LIVE_SPEED_BOUNDARY = (
    "const len=Math.hypot(dx,dz)||1,speed=6;"
    "player.position.x+=dx/len*speed*dt;"
    "player.position.z-=dz/len*speed*dt;"
)
_LIVE_SPEED_HOOK = (
    "const len=Math.hypot(dx,dz)||1,"
    "speed=Math.max(1.5,Math.min(14,Number(window.Aura3DPlayerTuning?.speed)||6));"
    "player.position.x+=dx/len*speed*dt;"
    "player.position.z-=dz/len*speed*dt;"
)


def _runtime_payload(game, world) -> dict:
    payload = _v4_runtime_payload(game, world)
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
    payload["runtime_contract"]["mobile_touch_controls"] = True
    payload["runtime_contract"]["keyboard_focusable"] = True
    payload["runtime_contract"]["reduced_motion_respected"] = True
    payload["runtime_contract"]["aria_live_status"] = True
    payload["runtime_contract"]["private_live_player_speed_hook"] = True
    payload["runtime_contract"]["private_live_player_speed_range"] = [1.5, 14.0]
    payload["runtime_contract"]["private_live_player_speed_default"] = 6.0
    return payload


def _serialize_runtime_payload(html: str, payload: dict) -> str:
    """Replace the compatibility renderer's cfg with the exact validated v4 contract.

    Aura3D v4 intentionally layers its reviewed rendering hooks over v3. The v3 renderer therefore
    creates the HTML shell first, but the browser must receive the v4 payload rather than stale v3
    capability metadata. Fail closed if that stable serialization boundary ever changes.
    """
    prefix = "const cfg="
    suffix = ";\nconst canvas="
    start = html.find(prefix)
    end = html.find(suffix, start + len(prefix)) if start >= 0 else -1
    if start < 0 or end < 0:
        raise ValueError("Aura3D compatibility payload contract changed; v4 serialization requires review")
    serialized = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    return html[: start + len(prefix)] + serialized + html[end:]


def _inject_private_live_player_speed_hook(html: str) -> str:
    """Replace only the reviewed Aura3D movement constant with a bounded local runtime override.

    Public/exported games keep the exact default speed because they never create Aura3DPlayerTuning.
    The private live-creation layer can set only a numeric value inside the 1.5–14 units/second clamp.
    Fail closed if the reviewed renderer movement boundary changes instead of guessing at new code.
    """
    if html.count(_LIVE_SPEED_BOUNDARY) != 1:
        raise ValueError("Aura3D movement boundary changed; private live player-speed hook requires review")
    return html.replace(_LIVE_SPEED_BOUNDARY, _LIVE_SPEED_HOOK, 1)


def render_aura3d_playtest(game, world, *, csp: str) -> str:
    # Validate the exact model/cinematic set and serialize that same closed payload into the page.
    # Raw models and creator-authored executable code are never loaded or run by the browser.
    payload = _runtime_payload(game, world)
    html = _render_v4(game, world, csp=csp)
    html = _serialize_runtime_payload(html, payload)
    html = _inject_private_live_player_speed_hook(html)
    return harden_game_runtime_html(html)


__all__ = ["render_aura3d_playtest", "_runtime_payload", "_inject_private_live_player_speed_hook"]
