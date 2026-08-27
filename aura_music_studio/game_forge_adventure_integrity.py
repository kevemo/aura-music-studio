from __future__ import annotations

from .game_forge_adventure import AdventureStateDNA, adventure_reference_blockers, load_adventure_optional


def canonical_adventure_integrity_payload(game_id: str) -> dict:
    """Return stable Adventure DNA whether or not its empty sidecar has been materialized yet."""
    state = load_adventure_optional(game_id) or AdventureStateDNA(game_id=game_id)
    return state.model_dump(mode="json", exclude={"created_at", "updated_at"})


__all__ = ["canonical_adventure_integrity_payload", "adventure_reference_blockers"]
