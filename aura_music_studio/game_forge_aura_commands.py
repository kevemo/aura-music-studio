from __future__ import annotations

"""Stable import path for Aura Game Forge natural-language commands.

The reviewed v2 command layer adds 3D model upload/binding support while preserving the existing
media command contract and deterministic ambiguity refusal.
"""

from .game_forge_aura_commands_v2 import (
    GameAuraCommandRequest,
    GameAuraCommandResult,
    execute_game_aura_command,
    router,
)

__all__ = [
    "router",
    "GameAuraCommandRequest",
    "GameAuraCommandResult",
    "execute_game_aura_command",
]
