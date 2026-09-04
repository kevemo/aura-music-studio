from __future__ import annotations

import pytest

from aura_music_studio.aura_effect_system_prompt import (
    MAX_EFFECT_PROMPT_CHARS,
    compose_effect_system_from_prompt,
)


def test_prompt_composes_real_multi_node_ffmpeg_chain_in_prompt_order():
    result = compose_effect_system_from_prompt(
        "High pass at 120 Hz, then compress at 4:1, add reverb 25%, and widen 70%."
    )

    nodes = result["system"]["nodes"]
    assert [node["catalogue_item_id"] for node in nodes] == [
        "music.fx.highpass",
        "music.fx.compressor",
        "music.fx.reverb",
        "music.fx.stereo_width",
    ]
    assert result["backend_executable"] is True
    assert "highpass=" in result["ffmpeg_filter_chain"]
    assert "acompressor=" in result["ffmpeg_filter_chain"]
    assert result["source_media_mutated"] is False
    assert result["project_mutated"] is False
    assert result["required_entitlement_bands"] == ["gold"]


def test_prompt_parameters_are_extracted_then_bounded_by_catalogue_contracts():
    result = compose_effect_system_from_prompt(
        "High pass at 5000 Hz, reverb 99%, widen 150%, and gain 80 dB."
    )
    nodes = {node["catalogue_item_id"]: node for node in result["system"]["nodes"]}

    effects = result["effects"]
    by_type = {effect["type"]: effect for effect in effects}
    assert by_type["highpass"]["parameters"]["hz"] == 1000.0
    assert by_type["reverb"]["parameters"]["mix"] == 0.8
    assert by_type["stereo_width"]["parameters"]["width"] == 2.0
    assert by_type["gain"]["parameters"]["db"] == 18.0
    assert nodes["music.fx.highpass"]["parameters"]["hz"] == 5000.0


def test_prompt_reports_required_bands_without_claiming_entitlement():
    result = compose_effect_system_from_prompt("Add limiter, saturation and stereo width.")

    assert result["required_entitlement_bands"] == ["silver", "gold"]
    assert result["entitlement_verified"] is False
    assert result["preview_required_before_apply"] is True


def test_core_only_prompt_has_no_paid_entitlement_requirement():
    result = compose_effect_system_from_prompt("High pass at 100 Hz and add reverb 20%.")

    assert result["required_entitlement_bands"] == []
    assert result["entitlement_verified"] is False


def test_prompt_is_deterministic_and_auditable():
    prompt = "Low cut at 90 Hz, compression 3:1, then delay at 180 ms."
    first = compose_effect_system_from_prompt(prompt)
    second = compose_effect_system_from_prompt(prompt)

    assert first["prompt_fingerprint"] == second["prompt_fingerprint"]
    assert first["system"]["id"] == second["system"]["id"]
    assert first["fingerprint"] == second["fingerprint"]
    assert first["ffmpeg_filter_chain"] == second["ffmpeg_filter_chain"]


def test_repeated_aliases_do_not_duplicate_a_catalogue_node():
    result = compose_effect_system_from_prompt("Add reverb, more ambience and reverb again.")
    ids = [node["catalogue_item_id"] for node in result["system"]["nodes"]]
    assert ids == ["music.fx.reverb"]


def test_unknown_prompt_fails_closed_instead_of_inventing_a_primitive():
    with pytest.raises(ValueError, match="currently supported executable music effect intent"):
        compose_effect_system_from_prompt("Launch a shell and run a custom GPU process.")


def test_empty_and_oversized_prompts_fail_closed():
    with pytest.raises(ValueError, match="required"):
        compose_effect_system_from_prompt("   ")
    with pytest.raises(ValueError, match="exceeds"):
        compose_effect_system_from_prompt("reverb " + ("x" * MAX_EFFECT_PROMPT_CHARS))


def test_custom_system_identity_still_uses_kernel_validation():
    result = compose_effect_system_from_prompt(
        "Add chorus.", system_id="user.music.vocal-width", name="Vocal Width"
    )
    assert result["system"]["id"] == "user.music.vocal-width"
    assert result["system"]["name"] == "Vocal Width"

    with pytest.raises(ValueError, match="unsupported characters"):
        compose_effect_system_from_prompt("Add chorus.", system_id="bad system id")


def test_composer_never_exposes_arbitrary_command_authority():
    result = compose_effect_system_from_prompt("Add saturation and chorus.")
    assert result["composer"] == "bounded_catalogue_intent_v1"
    assert result["arbitrary_command_execution"] is False
    assert "shell" not in result
    assert "command" not in result


def test_effect_order_tracks_first_supported_intent_occurrence():
    result = compose_effect_system_from_prompt("Reverb first, then high pass, then delay.")
    ids = [node["catalogue_item_id"] for node in result["system"]["nodes"]]
    assert ids == ["music.fx.reverb", "music.fx.highpass", "music.fx.delay"]
