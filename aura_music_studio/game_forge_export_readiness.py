from __future__ import annotations

from .game_forge_export import _validate_exportable
from .game_forge_models import GameDNA


def aura_web_export_readiness(game: GameDNA) -> dict:
    """Return truthful, side-effect-free Aura Web export readiness for the current Game DNA.

    The authoritative validation remains in ``game_forge_export._validate_exportable`` so the
    project workspace cannot drift from the actual export admission rules. This helper never
    creates an export and never exposes private storage paths.
    """
    try:
        content_hash = _validate_exportable(game, "aura_web")
    except (OSError, ValueError) as exc:
        return {
            "target": "aura_web",
            "production_ready_target": True,
            "ready": False,
            "reason": str(exc),
            "content_hash": None,
            "export_studio_url": f"/game-creation/export/{game.id}",
            "side_effect_free_check": True,
            "private_paths_exposed": False,
        }
    return {
        "target": "aura_web",
        "production_ready_target": True,
        "ready": True,
        "reason": None,
        "content_hash": content_hash,
        "export_studio_url": f"/game-creation/export/{game.id}",
        "side_effect_free_check": True,
        "private_paths_exposed": False,
    }


__all__ = ["aura_web_export_readiness"]
