from __future__ import annotations

import hashlib
import json

from .game_forge_asset_bindings import binding_publication_blockers
from .game_forge_assets import asset_integrity_payload, asset_publication_blockers
from .game_forge_cinematics import cinematic_integrity_payload, cinematic_reference_blockers
from .game_forge_model_assets import model_integrity_payload, model_publication_blockers
from .game_forge_model_bindings import model_binding_publication_blockers
from .game_forge_models import GameDNA, GameRatingAssessment
from .game_forge_ratings import assess_game, rating_content_hash
from .game_forge_world import world_rating_payload


def game_integrity_hash(game: GameDNA) -> str:
    """Hash Game DNA, World DNA and every verified runtime asset/cinematic snapshot.

    Public-test approval is bound to this value. Editing the game/world, changing explicit asset
    or model bindings, importing/removing media or models, changing rights, changing snapshot
    bytes, or editing cinematic/VFX DNA therefore invalidates the previous build and assessment.
    """
    payload = {
        "game_rating_payload_hash": rating_content_hash(game),
        "world": world_rating_payload(game.id),
        "assets": asset_integrity_payload(game.id),
        "models": model_integrity_payload(game.id),
        "cinematic": cinematic_integrity_payload(game.id),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def assess_game_integrity(game: GameDNA) -> GameRatingAssessment:
    assessment = assess_game(game)
    assessment.content_hash = game_integrity_hash(game)
    blockers = (
        asset_publication_blockers(game.id)
        + binding_publication_blockers(game.id)
        + model_publication_blockers(game.id)
        + model_binding_publication_blockers(game.id)
        + cinematic_reference_blockers(game.id)
    )
    for blocker in blockers:
        if blocker not in assessment.blockers:
            assessment.blockers.append(blocker)
    if blockers:
        assessment.public_test_allowed = False
    return assessment
