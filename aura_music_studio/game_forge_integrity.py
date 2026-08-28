from __future__ import annotations

import hashlib
import json

from .game_forge_adventure_integrity import adventure_reference_blockers, canonical_adventure_integrity_payload
from .game_forge_asset_bindings import binding_publication_blockers
from .game_forge_assets import asset_integrity_payload, asset_publication_blockers
from .game_forge_cinematics import cinematic_integrity_payload, cinematic_reference_blockers
from .game_forge_gameplay import gameplay_publication_blockers
from .game_forge_model_assets import model_integrity_payload, model_publication_blockers
from .game_forge_model_bindings import model_binding_publication_blockers
from .game_forge_models import GameDNA, GameRatingAssessment
from .game_forge_ratings import assess_game, rating_content_hash
from .game_forge_state_machine import state_machine_publication_blockers
from .game_forge_world import world_rating_payload
from .game_forge_world_events import world_event_publication_blockers
from .game_forge_world_logic import world_logic_publication_blockers


def game_integrity_hash(game: GameDNA) -> str:
    """Hash Game DNA, World DNA and every trusted runtime-sidecar snapshot.

    Public-test approval is bound to this value. Editing the game/world, declarative gameplay,
    Advanced World Logic, World Events & Atmosphere, typed State Machines, Adventure State,
    asset/model bindings, verified media/models, cinematic/VFX DNA, rights or snapshot bytes
    therefore invalidates the previous build and assessment.
    """
    payload = {
        "game_rating_payload_hash": rating_content_hash(game),
        "world": world_rating_payload(game.id),
        "assets": asset_integrity_payload(game.id),
        "models": model_integrity_payload(game.id),
        "cinematic": cinematic_integrity_payload(game.id),
        "adventure": canonical_adventure_integrity_payload(game.id),
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
        + gameplay_publication_blockers(game.id)
        + adventure_reference_blockers(game.id)
        + world_logic_publication_blockers(game.id)
        + world_event_publication_blockers(game.id)
        + state_machine_publication_blockers(game.id)
    )
    for blocker in blockers:
        if blocker not in assessment.blockers:
            assessment.blockers.append(blocker)
    if blockers:
        assessment.public_test_allowed = False
    return assessment
