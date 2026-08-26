from __future__ import annotations

import hashlib
import json

from .game_forge_models import GameDNA, GameRatingAssessment
from .game_forge_ratings import assess_game, rating_content_hash
from .game_forge_world import world_rating_payload


def game_integrity_hash(game: GameDNA) -> str:
    """Hash Game DNA plus the current Aura World DNA.

    Public-test approval is bound to this value. Editing a world entity, behavior, terrain,
    material, physics rule, procedural rule or performance/world setting therefore invalidates
    the previous build/assessment even when the high-level Game DNA did not change.
    """
    payload = {
        "game_rating_payload_hash": rating_content_hash(game),
        "world": world_rating_payload(game.id),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def assess_game_integrity(game: GameDNA) -> GameRatingAssessment:
    assessment = assess_game(game)
    assessment.content_hash = game_integrity_hash(game)
    return assessment
