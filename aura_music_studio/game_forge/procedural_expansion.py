"""Additional deterministic content generators for Game Forge Creative Labs.

These generators extend the canonical procedural registry with bounded, provider-neutral
ship, vehicle, planet and skill-tree descriptors recovered as useful requirements from the
legacy Fractalis source audit. They are clean-room Game Forge implementations, not Unreal
runtime ports, and they carry no filesystem, network, authentication, LIVE, payment, Coin,
Gift or arbitrary-code authority.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from .procedural import DeterministicRng, GenerationInputError, GeneratorRegistry


def _int_parameter(
    parameters: Mapping[str, Any],
    key: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    value = parameters.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise GenerationInputError(f"{key} must be an integer")
    if value < minimum or value > maximum:
        raise GenerationInputError(f"{key} must be between {minimum} and {maximum}")
    return value


def _number_parameter(
    parameters: Mapping[str, Any],
    key: str,
    *,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    value = parameters.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GenerationInputError(f"{key} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result < minimum or result > maximum:
        raise GenerationInputError(f"{key} must be between {minimum} and {maximum}")
    return result


def _choice_parameter(
    parameters: Mapping[str, Any],
    key: str,
    *,
    allowed: Sequence[str],
    rng: DeterministicRng,
) -> str:
    value = parameters.get(key)
    if value is None:
        return str(rng.choice(allowed))
    if not isinstance(value, str) or value not in allowed:
        raise GenerationInputError(f"{key} must be one of: {', '.join(allowed)}")
    return value


def _generate_ship(parameters: Mapping[str, Any], rng: DeterministicRng) -> dict[str, Any]:
    ship_class = _choice_parameter(
        parameters,
        "ship_class",
        allowed=("scout", "fighter", "freighter", "corvette", "explorer"),
        rng=rng,
    )
    tier = _int_parameter(parameters, "tier", default=1, minimum=1, maximum=10)
    profiles = {
        "scout": {"hull": (70, 110), "shield": (40, 80), "cargo": (8, 20), "speed": (150, 220)},
        "fighter": {"hull": (100, 160), "shield": (80, 135), "cargo": (6, 14), "speed": (125, 190)},
        "freighter": {"hull": (180, 300), "shield": (70, 130), "cargo": (80, 180), "speed": (55, 95)},
        "corvette": {"hull": (220, 360), "shield": (140, 230), "cargo": (24, 60), "speed": (85, 135)},
        "explorer": {"hull": (130, 210), "shield": (95, 165), "cargo": (35, 85), "speed": (95, 150)},
    }[ship_class]
    tier_scale = 1.0 + ((tier - 1) * 0.12)
    prefixes = ("Astra", "Horizon", "Nova", "Rhea", "Vanguard", "Wayfinder", "Solaris")
    nouns = {
        "scout": ("Swift", "Needle", "Skimmer"),
        "fighter": ("Lancer", "Falcon", "Sentinel"),
        "freighter": ("Carrier", "Atlas", "Hauler"),
        "corvette": ("Ward", "Spear", "Guardian"),
        "explorer": ("Seeker", "Voyager", "Pathfinder"),
    }[ship_class]
    hardpoints = {
        "scout": (1, 2),
        "fighter": (2, 4),
        "freighter": (1, 2),
        "corvette": (3, 5),
        "explorer": (1, 3),
    }[ship_class]
    utility_slots = {
        "scout": (1, 3),
        "fighter": (1, 2),
        "freighter": (3, 6),
        "corvette": (2, 4),
        "explorer": (3, 5),
    }[ship_class]
    return {
        "ship_id": f"ship-{rng.randint(100000, 999999)}",
        "name": f"{rng.choice(prefixes)} {rng.choice(nouns)}",
        "ship_class": ship_class,
        "tier": tier,
        "hull_points": int(round(rng.randint(*profiles["hull"]) * tier_scale)),
        "shield_points": int(round(rng.randint(*profiles["shield"]) * tier_scale)),
        "cargo_capacity": int(round(rng.randint(*profiles["cargo"]) * tier_scale)),
        "max_speed_units": round(rng.uniform(*profiles["speed"]) * (1.0 + ((tier - 1) * 0.03)), 2),
        "turn_rate": round(rng.uniform(0.35, 1.8), 3),
        "energy_capacity": int(round(rng.randint(80, 180) * tier_scale)),
        "weapon_hardpoints": rng.randint(*hardpoints),
        "utility_slots": rng.randint(*utility_slots),
        "travel_profile": "bounded_gameplay_spaceflight",
        "commercial_value": False,
    }


def _generate_vehicle(parameters: Mapping[str, Any], rng: DeterministicRng) -> dict[str, Any]:
    vehicle_class = _choice_parameter(
        parameters,
        "vehicle_class",
        allowed=("wheeled", "tracked", "hover", "air", "water"),
        rng=rng,
    )
    tier = _int_parameter(parameters, "tier", default=1, minimum=1, maximum=10)
    profiles = {
        "wheeled": {"durability": (90, 160), "speed": (70, 150), "cargo": (8, 40)},
        "tracked": {"durability": (180, 320), "speed": (35, 75), "cargo": (20, 80)},
        "hover": {"durability": (100, 180), "speed": (85, 165), "cargo": (10, 45)},
        "air": {"durability": (80, 150), "speed": (120, 260), "cargo": (4, 24)},
        "water": {"durability": (130, 240), "speed": (45, 105), "cargo": (25, 100)},
    }[vehicle_class]
    tier_scale = 1.0 + ((tier - 1) * 0.1)
    terrain_tags = {
        "wheeled": ["road", "firm_ground"],
        "tracked": ["rough_ground", "mud", "snow"],
        "hover": ["flat_ground", "shallow_water"],
        "air": ["airborne"],
        "water": ["water"],
    }[vehicle_class]
    prefixes = ("Aether", "Arc", "Ember", "Nova", "Rift", "Solar", "Vela")
    nouns = ("Runner", "Rover", "Nomad", "Courier", "Ranger", "Drifter", "Pioneer")
    return {
        "vehicle_id": f"vehicle-{rng.randint(100000, 999999)}",
        "name": f"{rng.choice(prefixes)} {rng.choice(nouns)}",
        "vehicle_class": vehicle_class,
        "tier": tier,
        "durability": int(round(rng.randint(*profiles["durability"]) * tier_scale)),
        "max_speed_units": round(rng.uniform(*profiles["speed"]) * (1.0 + ((tier - 1) * 0.025)), 2),
        "cargo_capacity": int(round(rng.randint(*profiles["cargo"]) * tier_scale)),
        "boost_multiplier": round(rng.uniform(1.1, 1.75), 3),
        "handling": round(rng.uniform(0.35, 1.0), 3),
        "seat_count": rng.randint(1, 6),
        "terrain_tags": terrain_tags,
        "movement_profile": "bounded_gameplay_vehicle",
        "commercial_value": False,
    }


def _generate_planet(parameters: Mapping[str, Any], rng: DeterministicRng) -> dict[str, Any]:
    biome = _choice_parameter(
        parameters,
        "biome",
        allowed=("rocky", "oceanic", "desert", "ice", "volcanic", "gas_giant", "forest"),
        rng=rng,
    )
    atmosphere = parameters.get("atmosphere")
    allowed_atmospheres = ("none", "thin", "temperate", "dense", "toxic")
    if atmosphere is None:
        atmosphere = "dense" if biome == "gas_giant" else str(rng.choice(allowed_atmospheres))
    elif not isinstance(atmosphere, str) or atmosphere not in allowed_atmospheres:
        raise GenerationInputError(
            "atmosphere must be one of: none, thin, temperate, dense, toxic"
        )
    index = _int_parameter(parameters, "planet_index", default=1, minimum=1, maximum=999)
    gravity_scale = _number_parameter(
        parameters,
        "gravity_scale",
        default=1.0,
        minimum=0.25,
        maximum=3.0,
    )
    name_stems = ("Astra", "Cinder", "Elyra", "Neris", "Orion", "Rhea", "Vela", "Zyra")
    temperature_bands = {
        "rocky": ("cold", "temperate", "hot"),
        "oceanic": ("cool", "temperate", "warm"),
        "desert": ("warm", "hot", "extreme_hot"),
        "ice": ("extreme_cold", "cold"),
        "volcanic": ("hot", "extreme_hot"),
        "gas_giant": ("cold", "temperate", "hot"),
        "forest": ("cool", "temperate", "warm"),
    }[biome]
    hazard_pool = {
        "rocky": ("dust_storms", "seismic_activity", "radiation_pockets"),
        "oceanic": ("storms", "deep_water", "tidal_surges"),
        "desert": ("heat", "sandstorms", "low_water"),
        "ice": ("extreme_cold", "whiteout", "thin_ice"),
        "volcanic": ("lava", "ash", "toxic_vents"),
        "gas_giant": ("pressure", "storms", "electrical_activity"),
        "forest": ("dense_growth", "wildlife", "storms"),
    }[biome]
    hazard_count = rng.randint(0, min(2, len(hazard_pool)))
    hazards = rng.sample(hazard_pool, hazard_count)
    base_gravity = 1.35 if biome == "gas_giant" else rng.uniform(0.2, 1.9)
    habitability = 0 if biome in {"gas_giant", "volcanic"} else rng.randint(0, 100)
    return {
        "planet_id": f"planet-{index}-{rng.randint(1000, 9999)}",
        "name": f"{rng.choice(name_stems)} {index}",
        "biome": biome,
        "atmosphere": atmosphere,
        "gravity_g": round(min(4.0, base_gravity * gravity_scale), 3),
        "temperature_band": rng.choice(temperature_bands),
        "habitability_score": habitability,
        "settlement_probability": round((habitability / 100.0) * rng.uniform(0.15, 0.9), 3),
        "points_of_interest": rng.randint(0, 8),
        "hazards": hazards,
        "simulation_fidelity": "gameplay_descriptor",
    }


def _generate_skill_tree(parameters: Mapping[str, Any], rng: DeterministicRng) -> dict[str, Any]:
    archetype = _choice_parameter(
        parameters,
        "archetype",
        allowed=("combat", "exploration", "engineering", "support", "survival"),
        rng=rng,
    )
    node_count = _int_parameter(parameters, "node_count", default=12, minimum=3, maximum=30)
    max_tier = _int_parameter(parameters, "max_tier", default=5, minimum=2, maximum=6)
    names = {
        "combat": ("Precision", "Fortitude", "Momentum", "Tactics", "Overwatch", "Resolve"),
        "exploration": ("Pathfinding", "Survey", "Navigation", "Discovery", "Traversal", "Awareness"),
        "engineering": ("Repair", "Efficiency", "Fabrication", "Systems", "Overclock", "Diagnostics"),
        "support": ("Recovery", "Coordination", "Shielding", "Inspiration", "Logistics", "Aid"),
        "survival": ("Resilience", "Scavenging", "Shelter", "Tracking", "Endurance", "Adaptation"),
    }[archetype]
    effects = {
        "combat": ("damage_bonus", "critical_bonus", "defence_bonus", "cooldown_reduction"),
        "exploration": ("scan_range", "movement_bonus", "discovery_bonus", "travel_efficiency"),
        "engineering": ("repair_bonus", "craft_efficiency", "energy_bonus", "system_resilience"),
        "support": ("healing_bonus", "shield_bonus", "team_efficiency", "resource_efficiency"),
        "survival": ("health_bonus", "hazard_resistance", "resource_yield", "stamina_bonus"),
    }[archetype]
    nodes: list[dict[str, Any]] = []
    for index in range(node_count):
        tier = 1 + min(max_tier - 1, (index * max_tier) // node_count)
        node_id = f"skill-{index + 1}"
        prerequisite_ids: list[str] = []
        if index > 0:
            previous_index = max(0, index - rng.randint(1, min(index, 3)))
            prerequisite_ids.append(f"skill-{previous_index + 1}")
        if index > 4 and rng.randbelow(4) == 0:
            candidate = f"skill-{rng.randint(1, index - 1)}"
            if candidate not in prerequisite_ids:
                prerequisite_ids.append(candidate)
        nodes.append(
            {
                "id": node_id,
                "name": f"{rng.choice(names)} {tier}",
                "tier": tier,
                "cost": 1 + tier + rng.randbelow(3),
                "currency_namespace": "project_skill_points",
                "effect": rng.choice(effects),
                "magnitude": round(rng.uniform(0.02, 0.18) * (1.0 + ((tier - 1) * 0.2)), 3),
                "prerequisites": sorted(prerequisite_ids),
            }
        )
    return {
        "tree_id": f"{archetype}-tree-{rng.randint(1000, 9999)}",
        "archetype": archetype,
        "node_count": node_count,
        "max_tier": max_tier,
        "nodes": nodes,
        "progression_currency": "project_skill_points",
        "commercial_value": False,
        "progression_profile": "game_forge_skill_tree_v1",
    }


def register_procedural_expansion(registry: GeneratorRegistry) -> None:
    """Register the bounded v1 expansion on one explicit Game Forge registry."""

    registry.register("planet", "v1", _generate_planet)
    registry.register("ship", "v1", _generate_ship)
    registry.register("skill_tree", "v1", _generate_skill_tree)
    registry.register("vehicle", "v1", _generate_vehicle)
