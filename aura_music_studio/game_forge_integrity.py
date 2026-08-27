from __future__ import annotations

import hashlib
import json

from .game_forge_assets import asset_integrity_payload, asset_publication_blockers
from .game_forge_models import GameDNA, GameRatingAssessment
from .game_forge_ratings import assess_game, rating_content_hash
from .game_forge_world import world_rating_payload


def game_integrity_hash(game: GameDNA) -> str:
    """Hash Game DNA plus Aura World DNA and imported Game Forge asset snapshots.

    Public-test approval is bound to this value. Editing the game/world, importing or removing
    an asset, changing asset rights, or changing an imported asset snapshot therefore invalidates
    the previous build/assessment.
    """
    payload = {
        "game_rating_payload_hash": rating_content_hash(game),
        "world": world_rating_payload(game.id),
        "assets": asset_integrity_payload(game.id),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def assess_game_integrity(game: GameDNA) -> GameRatingAssessment:
    assessment = assess_game(game)
    assessment.content_hash = game_integrity_hash(game)
    asset_blockers = asset_publication_blockers(game.id)
    for blocker in asset_blockers:
        if blocker not in assessment.blockers:
            assessment.blockers.append(blocker)
    if asset_blockers:
        assessment.public_test_allowed = False
    return assessment
