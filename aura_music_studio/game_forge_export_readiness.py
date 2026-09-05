from __future__ import annotations

from .game_forge_export import _validate_exportable
from .game_forge_models import GameDNA


def aura_web_export_readiness(game: GameDNA) -> dict:
    """Return truthful, side-effect-free Aura Web package readiness for current Game DNA.

    Package generation readiness is deliberately distinct from production release readiness.
    The latter remains false until independently trusted publisher signing evidence is verified.
    This helper never creates an export and never exposes private storage paths.
    """
    try:
        content_hash = _validate_exportable(game, "aura_web")
    except (OSError, ValueError) as exc:
        return {
            "target": "aura_web",
            "package_ready_target": True,
            "package_ready": False,
            # Compatibility alias: ready means package-generation readiness only.
            "ready": False,
            "production_ready_target": False,
            "production_release_ready": False,
            "release_blockers": ["package_preflight_failed", "publisher_authenticity_not_verified"],
            "reason": str(exc),
            "content_hash": None,
            "export_studio_url": f"/game-creation/export/{game.id}",
            "side_effect_free_check": True,
            "private_paths_exposed": False,
        }
    return {
        "target": "aura_web",
        "package_ready_target": True,
        "package_ready": True,
        # Compatibility alias: ready means package-generation readiness only.
        "ready": True,
        "production_ready_target": False,
        "production_release_ready": False,
        "release_blockers": ["publisher_authenticity_not_verified"],
        "reason": None,
        "content_hash": content_hash,
        "export_studio_url": f"/game-creation/export/{game.id}",
        "side_effect_free_check": True,
        "private_paths_exposed": False,
    }


__all__ = ["aura_web_export_readiness"]
