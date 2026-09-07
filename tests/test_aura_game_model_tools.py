from __future__ import annotations

from aura_music_studio.aura_game_tools import _explicit_game_write_allowed


def test_model_write_intent_gate_allows_explicit_model_mutations_without_substring_matching():
    assert _explicit_game_write_allowed(
        "apply_game_media_command",
        "Use Dragon Knight as the 3D model on Castle Guard",
    ) is True
    assert _explicit_game_write_allowed(
        "bind_game_model_asset",
        "Assign this mesh to the Castle Guard",
    ) is True
    assert _explicit_game_write_allowed(
        "unbind_game_model_asset",
        "Remove the model from Castle Guard",
    ) is True

    # Read-only mentions of models must not authorize a mutation tool.
    assert _explicit_game_write_allowed(
        "bind_game_model_asset",
        "Tell me which models are available",
    ) is False
    assert _explicit_game_write_allowed(
        "apply_game_media_command",
        "What model would suit this character?",
    ) is False
