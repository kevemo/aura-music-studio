from __future__ import annotations

import math
import random

import pytest
from pydantic import ValidationError

from aura_music_studio.game_forge.procedural import (
    DEFAULT_GENERATOR_REGISTRY,
    DuplicateGeneratorError,
    GenerationExecutionError,
    GenerationInputError,
    GenerationRequest,
    GeneratorRegistry,
    UnknownGeneratorError,
    generate,
)


def request(
    generator_type: str,
    *,
    seed: int | str = 42,
    version: str = "v1",
    parameters: dict | None = None,
) -> GenerationRequest:
    return GenerationRequest(
        generator_type=generator_type,
        generator_version=version,
        seed=seed,
        parameters=parameters or {},
    )


def test_same_version_seed_and_inputs_produce_identical_output_and_provenance():
    first = generate(request("weapon", seed="shared-skies", parameters={"difficulty": 1.4}))
    second = generate(request("weapon", seed="shared-skies", parameters={"difficulty": 1.4}))

    assert first.output == second.output
    assert first.provenance_hash == second.provenance_hash
    assert len(first.provenance_hash) == 64


def test_parameter_mapping_order_does_not_change_generation_identity():
    first = generate(
        request("weapon", seed=7, parameters={"weapon_class": "rifle", "difficulty": 1.25})
    )
    second = generate(
        request("weapon", seed=7, parameters={"difficulty": 1.25, "weapon_class": "rifle"})
    )

    assert first.output == second.output
    assert first.provenance_hash == second.provenance_hash


def test_different_seed_changes_deterministic_output():
    first = generate(request("star_system", seed="alpha"))
    second = generate(request("star_system", seed="beta"))

    assert first.output != second.output
    assert first.provenance_hash != second.provenance_hash


def test_generation_does_not_mutate_global_random_state():
    random.seed(12345)
    expected = [random.random() for _ in range(4)]
    random.seed(12345)

    generate(request("mission", seed=88))
    observed = [random.random() for _ in range(4)]

    assert observed == expected


def test_unknown_generator_or_version_fails_closed():
    with pytest.raises(UnknownGeneratorError, match="not registered"):
        generate(request("weapon", version="v999"))

    with pytest.raises(UnknownGeneratorError, match="not registered"):
        generate(request("world"))


def test_registry_rejects_duplicate_generator_registration():
    registry = GeneratorRegistry()
    registry.register("test", "v1", lambda params, rng: {"value": 1})

    with pytest.raises(DuplicateGeneratorError, match="already registered"):
        registry.register("test", "v1", lambda params, rng: {"value": 2})


def test_registered_generator_receives_detached_parameters():
    original = {"nested": {"value": 1}}
    generation_request = request("test", parameters=original)
    registry = GeneratorRegistry()

    def mutate_copy(params, rng):
        params["nested"]["value"] = 99
        return params

    registry.register("test", "v1", mutate_copy)
    result = registry.generate(generation_request)

    assert generation_request.parameters == {"nested": {"value": 1}}
    assert original == {"nested": {"value": 1}}
    assert result.output == {"nested": {"value": 99}}


def test_non_json_parameter_types_and_non_finite_numbers_are_rejected():
    with pytest.raises(ValidationError):
        request("weapon", parameters={"unsafe": object()})

    with pytest.raises(ValidationError):
        request("weapon", parameters={"nan": math.nan})


def test_parameter_depth_and_collection_bounds_are_enforced():
    too_deep: dict = {"value": 1}
    for _ in range(10):
        too_deep = {"nested": too_deep}

    with pytest.raises(ValidationError):
        request("weapon", parameters=too_deep)

    with pytest.raises(ValidationError):
        request("weapon", parameters={"items": list(range(129))})


def test_generator_output_must_remain_json_safe_and_bounded():
    registry = GeneratorRegistry()
    registry.register("bad", "v1", lambda params, rng: {"unsafe": object()})

    with pytest.raises(GenerationExecutionError, match="unsupported value type"):
        registry.generate(request("bad"))


def test_weapon_generator_uses_bounded_gameplay_stats_and_metadata():
    result = generate(
        request(
            "weapon",
            seed="rifle-build",
            parameters={"weapon_class": "rifle", "difficulty": 3.0, "rarity": "legendary"},
        )
    )
    weapon = result.output

    assert weapon["weapon_class"] == "rifle"
    assert weapon["rarity"] == "legendary"
    assert 75.6 <= weapon["damage"] <= 142.8
    assert 4.0 <= weapon["fire_rate_per_second"] <= 9.0
    assert 45.0 <= weapon["effective_range_m"] <= 120.0
    assert len(weapon["modifiers"]) == 3
    assert weapon["balance_profile"] == "game_forge_weapon_v1"


@pytest.mark.parametrize(
    ("parameters", "message"),
    [
        ({"weapon_class": "admin"}, "weapon_class must be one of"),
        ({"difficulty": 99}, "difficulty must be between"),
        ({"rarity": "mythic"}, "rarity is not a supported"),
    ],
)
def test_weapon_generator_rejects_out_of_contract_inputs(parameters, message):
    with pytest.raises(GenerationInputError, match=message):
        generate(request("weapon", parameters=parameters))


def test_mission_rewards_are_explicitly_project_game_currency_only():
    result = generate(request("mission", seed=5, parameters={"difficulty": 6}))
    reward = result.output["reward"]

    assert reward["currency_namespace"] == "project_game_currency"
    assert reward["commercial_value"] is False
    assert reward["amount"] > 0


def test_star_system_generator_defaults_to_reference_bounded_planet_count():
    result = generate(request("star_system", seed="system-seed"))
    system = result.output

    assert 3 <= system["planet_count"] <= 9
    assert len(system["planets"]) == system["planet_count"]
    assert system["simulation_fidelity"] == "gameplay_descriptor"


def test_star_system_generator_supports_explicit_bounded_planet_count():
    result = generate(request("star_system", seed="twelve", parameters={"planet_count": 12}))

    assert result.output["planet_count"] == 12
    assert len(result.output["planets"]) == 12

    with pytest.raises(GenerationInputError, match="planet_count must be between"):
        generate(request("star_system", parameters={"planet_count": 13}))


def test_default_registry_is_explicit_and_contains_only_canonical_generators():
    assert DEFAULT_GENERATOR_REGISTRY.registered_generators() == (
        ("mission", "v1"),
        ("planet", "v1"),
        ("ship", "v1"),
        ("skill_tree", "v1"),
        ("star_system", "v1"),
        ("vehicle", "v1"),
        ("weapon", "v1"),
    )
