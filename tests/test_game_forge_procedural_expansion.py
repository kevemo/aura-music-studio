from __future__ import annotations

import pytest

from aura_music_studio.game_forge.procedural import (
    GenerationInputError,
    GenerationRequest,
    GeneratorRegistry,
)
from aura_music_studio.game_forge.procedural_expansion import register_procedural_expansion


def request(
    generator_type: str,
    *,
    seed: int | str = 42,
    parameters: dict | None = None,
) -> GenerationRequest:
    return GenerationRequest(
        generator_type=generator_type,
        generator_version="v1",
        seed=seed,
        parameters=parameters or {},
    )


def expanded_registry() -> GeneratorRegistry:
    registry = GeneratorRegistry()
    register_procedural_expansion(registry)
    return registry


def test_expansion_registry_contains_only_bounded_v1_content_generators():
    assert expanded_registry().registered_generators() == (
        ("planet", "v1"),
        ("ship", "v1"),
        ("skill_tree", "v1"),
        ("vehicle", "v1"),
    )


def test_ship_generation_is_deterministic_and_bounded():
    registry = expanded_registry()
    generation_request = request(
        "ship",
        seed="rhea-fleet",
        parameters={"ship_class": "fighter", "tier": 10},
    )

    first = registry.generate(generation_request)
    second = registry.generate(generation_request)
    ship = first.output

    assert first.output == second.output
    assert first.provenance_hash == second.provenance_hash
    assert ship["ship_class"] == "fighter"
    assert ship["tier"] == 10
    assert 1 <= ship["weapon_hardpoints"] <= 4
    assert 1 <= ship["utility_slots"] <= 2
    assert ship["hull_points"] > 0
    assert ship["shield_points"] > 0
    assert ship["cargo_capacity"] > 0
    assert ship["max_speed_units"] > 0
    assert ship["travel_profile"] == "bounded_gameplay_spaceflight"
    assert ship["commercial_value"] is False


def test_vehicle_generation_uses_allowlisted_movement_classes_and_game_only_value():
    vehicle = expanded_registry().generate(
        request("vehicle", seed=99, parameters={"vehicle_class": "tracked", "tier": 4})
    ).output

    assert vehicle["vehicle_class"] == "tracked"
    assert vehicle["tier"] == 4
    assert vehicle["movement_profile"] == "bounded_gameplay_vehicle"
    assert vehicle["commercial_value"] is False
    assert set(vehicle["terrain_tags"]) == {"rough_ground", "mud", "snow"}
    assert 1 <= vehicle["seat_count"] <= 6
    assert 1.1 <= vehicle["boost_multiplier"] <= 1.75
    assert 0.35 <= vehicle["handling"] <= 1.0


def test_planet_generation_is_explicitly_a_gameplay_descriptor():
    planet = expanded_registry().generate(
        request(
            "planet",
            seed="ocean-world",
            parameters={
                "biome": "oceanic",
                "atmosphere": "temperate",
                "planet_index": 7,
                "gravity_scale": 1.25,
            },
        )
    ).output

    assert planet["biome"] == "oceanic"
    assert planet["atmosphere"] == "temperate"
    assert planet["simulation_fidelity"] == "gameplay_descriptor"
    assert planet["planet_id"].startswith("planet-7-")
    assert 0.0 <= planet["settlement_probability"] <= 0.9
    assert 0 <= planet["habitability_score"] <= 100
    assert 0 <= planet["points_of_interest"] <= 8
    assert 0 < planet["gravity_g"] <= 4.0
    assert len(planet["hazards"]) <= 2


def test_gas_giant_defaults_to_dense_atmosphere_and_zero_habitability():
    planet = expanded_registry().generate(
        request("planet", seed="giant", parameters={"biome": "gas_giant"})
    ).output

    assert planet["atmosphere"] == "dense"
    assert planet["habitability_score"] == 0
    assert planet["settlement_probability"] == 0.0


def test_skill_tree_is_acyclic_bounded_and_noncommercial():
    tree = expanded_registry().generate(
        request(
            "skill_tree",
            seed="engineer-tree",
            parameters={"archetype": "engineering", "node_count": 30, "max_tier": 6},
        )
    ).output

    assert tree["archetype"] == "engineering"
    assert tree["node_count"] == 30
    assert tree["max_tier"] == 6
    assert len(tree["nodes"]) == 30
    assert tree["progression_currency"] == "project_skill_points"
    assert tree["commercial_value"] is False
    assert tree["progression_profile"] == "game_forge_skill_tree_v1"

    ids = [node["id"] for node in tree["nodes"]]
    assert len(set(ids)) == len(ids)
    seen: set[str] = set()
    for node in tree["nodes"]:
        assert 1 <= node["tier"] <= 6
        assert node["currency_namespace"] == "project_skill_points"
        assert 0.02 <= node["magnitude"] <= 0.36
        assert set(node["prerequisites"]).issubset(seen)
        seen.add(node["id"])


@pytest.mark.parametrize(
    ("generator_type", "parameters", "message"),
    [
        ("ship", {"ship_class": "admin"}, "ship_class must be one of"),
        ("ship", {"tier": 0}, "tier must be between"),
        ("vehicle", {"vehicle_class": "teleporter"}, "vehicle_class must be one of"),
        ("vehicle", {"tier": 11}, "tier must be between"),
        ("planet", {"atmosphere": "magic"}, "atmosphere must be one of"),
        ("planet", {"gravity_scale": 10}, "gravity_scale must be between"),
        ("skill_tree", {"node_count": 31}, "node_count must be between"),
        ("skill_tree", {"max_tier": 1}, "max_tier must be between"),
    ],
)
def test_expansion_generators_reject_out_of_contract_inputs(generator_type, parameters, message):
    with pytest.raises(GenerationInputError, match=message):
        expanded_registry().generate(request(generator_type, parameters=parameters))


def test_different_seed_changes_expanded_generator_outputs():
    registry = expanded_registry()

    first = registry.generate(request("ship", seed="alpha"))
    second = registry.generate(request("ship", seed="beta"))

    assert first.output != second.output
    assert first.provenance_hash != second.provenance_hash
