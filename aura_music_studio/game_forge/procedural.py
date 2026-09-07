"""Deterministic, versioned procedural generation for Game Forge Creative Labs.

This module translates useful deterministic-generation concepts recovered from legacy
Game Forge source resources into the canonical Shared Skies Media runtime. It is a
clean-room, provider-neutral implementation: generators are explicitly registered,
versioned, bounded, pure with respect to external systems, and cannot dynamically
load code or obtain network/filesystem/commerce/LIVE authority.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from threading import RLock
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator

MAX_PARAMETER_BYTES = 16_384
MAX_OUTPUT_BYTES = 65_536
MAX_VALUE_DEPTH = 8
MAX_COLLECTION_ITEMS = 128
MAX_TOTAL_VALUES = 1_024
MAX_STRING_LENGTH = 512
MAX_GENERATOR_TYPE_LENGTH = 64
MAX_GENERATOR_VERSION_LENGTH = 32
MAX_ABS_NUMBER = 1_000_000_000_000

_GENERATOR_ID_RE = re.compile(r"^[a-z][a-z0-9_.-]*$")
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class GenerationError(ValueError):
    """Base class for fail-closed Game Forge generation failures."""


class GenerationInputError(GenerationError):
    """Raised when a request or generator parameter violates a bounded contract."""


class UnknownGeneratorError(GenerationError):
    """Raised when a generator type/version is not explicitly registered."""


class DuplicateGeneratorError(GenerationError):
    """Raised when code attempts to replace an already-registered generator."""


class GenerationExecutionError(GenerationError):
    """Raised when a registered generator fails or emits unsafe/unbounded output."""


def _validate_generator_key(generator_type: str, generator_version: str) -> tuple[str, str]:
    if len(generator_type) > MAX_GENERATOR_TYPE_LENGTH or not _GENERATOR_ID_RE.fullmatch(
        generator_type
    ):
        raise GenerationInputError("generator_type must be a bounded canonical identifier")
    if len(generator_version) > MAX_GENERATOR_VERSION_LENGTH or not _VERSION_RE.fullmatch(
        generator_version
    ):
        raise GenerationInputError("generator_version must be a bounded version identifier")
    return generator_type, generator_version


def _canonicalize(
    value: Any,
    *,
    path: str,
    max_bytes: int,
    depth: int = 0,
    counter: list[int] | None = None,
) -> Any:
    if counter is None:
        counter = [0]
    counter[0] += 1
    if counter[0] > MAX_TOTAL_VALUES:
        raise GenerationInputError(f"{path} contains too many values")
    if depth > MAX_VALUE_DEPTH:
        raise GenerationInputError(f"{path} exceeds maximum nesting depth")

    if value is None or isinstance(value, bool):
        normalized = value
    elif isinstance(value, int):
        if abs(value) > MAX_ABS_NUMBER:
            raise GenerationInputError(f"{path} contains an out-of-range integer")
        normalized = value
    elif isinstance(value, float):
        if not math.isfinite(value) or abs(value) > MAX_ABS_NUMBER:
            raise GenerationInputError(f"{path} contains an invalid floating-point value")
        normalized = 0.0 if value == 0 else value
    elif isinstance(value, str):
        if len(value) > MAX_STRING_LENGTH:
            raise GenerationInputError(f"{path} contains an overlong string")
        normalized = value
    elif isinstance(value, list):
        if len(value) > MAX_COLLECTION_ITEMS:
            raise GenerationInputError(f"{path} contains too many list items")
        normalized = [
            _canonicalize(
                item,
                path=f"{path}[{index}]",
                max_bytes=max_bytes,
                depth=depth + 1,
                counter=counter,
            )
            for index, item in enumerate(value)
        ]
    elif isinstance(value, dict):
        if len(value) > MAX_COLLECTION_ITEMS:
            raise GenerationInputError(f"{path} contains too many mapping entries")
        normalized = {}
        for key in sorted(value):
            if not isinstance(key, str):
                raise GenerationInputError(f"{path} mapping keys must be strings")
            if len(key) > MAX_STRING_LENGTH:
                raise GenerationInputError(f"{path} contains an overlong mapping key")
            normalized[key] = _canonicalize(
                value[key],
                path=f"{path}.{key}",
                max_bytes=max_bytes,
                depth=depth + 1,
                counter=counter,
            )
    else:
        raise GenerationInputError(f"{path} contains unsupported value type {type(value).__name__}")

    if depth == 0:
        encoded = _canonical_json(normalized).encode("utf-8")
        if len(encoded) > max_bytes:
            raise GenerationInputError(f"{path} exceeds the {max_bytes}-byte bounded payload limit")
    return normalized


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class GenerationRequest(BaseModel):
    """A reproducible generator request whose identity includes version, seed and inputs."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    generator_type: str = Field(min_length=1, max_length=MAX_GENERATOR_TYPE_LENGTH)
    generator_version: str = Field(min_length=1, max_length=MAX_GENERATOR_VERSION_LENGTH)
    seed: StrictInt | StrictStr
    parameters: dict[str, Any] = Field(default_factory=dict)

    @field_validator("generator_type")
    @classmethod
    def _validate_type(cls, value: str) -> str:
        _validate_generator_key(value, "v1")
        return value

    @field_validator("generator_version")
    @classmethod
    def _validate_version(cls, value: str) -> str:
        _validate_generator_key("generator", value)
        return value

    @field_validator("seed")
    @classmethod
    def _validate_seed(cls, value: int | str) -> int | str:
        if isinstance(value, int):
            if abs(value) > MAX_ABS_NUMBER:
                raise ValueError("seed integer is outside the bounded range")
            return value
        if not value or len(value) > MAX_STRING_LENGTH:
            raise ValueError("seed string must be non-empty and bounded")
        return value

    @field_validator("parameters")
    @classmethod
    def _validate_parameters(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _canonicalize(value, path="parameters", max_bytes=MAX_PARAMETER_BYTES)


class GenerationResult(BaseModel):
    """Canonical output plus deterministic provenance needed for save/reload evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    generator_type: str
    generator_version: str
    seed: StrictInt | StrictStr
    parameters: dict[str, Any]
    output: Any
    provenance_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class DeterministicRng:
    """Small SHA-256 counter RNG with stable behaviour independent of global random state."""

    def __init__(self, seed_material: bytes) -> None:
        if not seed_material:
            raise GenerationInputError("deterministic RNG requires seed material")
        self._seed_material = bytes(seed_material)
        self._counter = 0

    def _uint64(self) -> int:
        block = hashlib.sha256(
            self._seed_material + self._counter.to_bytes(16, byteorder="big", signed=False)
        ).digest()
        self._counter += 1
        return int.from_bytes(block[:8], byteorder="big", signed=False)

    def randbelow(self, upper_bound: int) -> int:
        if not isinstance(upper_bound, int) or isinstance(upper_bound, bool) or upper_bound <= 0:
            raise GenerationInputError("randbelow upper_bound must be a positive integer")
        modulus = 1 << 64
        limit = modulus - (modulus % upper_bound)
        while True:
            candidate = self._uint64()
            if candidate < limit:
                return candidate % upper_bound

    def randint(self, minimum: int, maximum: int) -> int:
        if minimum > maximum:
            raise GenerationInputError("randint minimum cannot exceed maximum")
        return minimum + self.randbelow(maximum - minimum + 1)

    def fraction(self) -> float:
        return self._uint64() / float(1 << 64)

    def uniform(self, minimum: float, maximum: float) -> float:
        if not math.isfinite(minimum) or not math.isfinite(maximum) or minimum > maximum:
            raise GenerationInputError("uniform bounds must be finite and ordered")
        return minimum + ((maximum - minimum) * self.fraction())

    def choice(self, values: Sequence[Any]) -> Any:
        if not values:
            raise GenerationInputError("choice requires at least one candidate")
        return values[self.randbelow(len(values))]

    def sample(self, values: Sequence[Any], count: int) -> list[Any]:
        if count < 0 or count > len(values):
            raise GenerationInputError("sample count is outside candidate bounds")
        pool = list(values)
        selected: list[Any] = []
        for _ in range(count):
            index = self.randbelow(len(pool))
            selected.append(pool.pop(index))
        return selected

    def weighted_choice(self, weighted_values: Sequence[tuple[Any, int]]) -> Any:
        if not weighted_values or any(weight <= 0 for _, weight in weighted_values):
            raise GenerationInputError("weighted choices require positive integer weights")
        total = sum(weight for _, weight in weighted_values)
        draw = self.randbelow(total)
        cursor = 0
        for value, weight in weighted_values:
            cursor += weight
            if draw < cursor:
                return value
        raise GenerationExecutionError("weighted choice failed closed")


GeneratorHandler = Callable[[Mapping[str, Any], DeterministicRng], Any]


class GeneratorRegistry:
    """Closed, explicit registry for canonical Game Forge procedural generators."""

    def __init__(self) -> None:
        self._handlers: dict[tuple[str, str], GeneratorHandler] = {}
        self._lock = RLock()

    def register(
        self,
        generator_type: str,
        generator_version: str,
        handler: GeneratorHandler,
    ) -> None:
        key = _validate_generator_key(generator_type, generator_version)
        if not callable(handler):
            raise GenerationInputError("generator handler must be callable")
        with self._lock:
            if key in self._handlers:
                raise DuplicateGeneratorError(
                    f"generator {generator_type}@{generator_version} is already registered"
                )
            self._handlers[key] = handler

    def registered_generators(self) -> tuple[tuple[str, str], ...]:
        with self._lock:
            return tuple(sorted(self._handlers))

    def generate(self, request: GenerationRequest) -> GenerationResult:
        key = (request.generator_type, request.generator_version)
        with self._lock:
            handler = self._handlers.get(key)
        if handler is None:
            raise UnknownGeneratorError(
                f"generator {request.generator_type}@{request.generator_version} is not registered"
            )

        parameters = _canonicalize(
            request.parameters,
            path="parameters",
            max_bytes=MAX_PARAMETER_BYTES,
        )
        identity = {
            "generator_type": request.generator_type,
            "generator_version": request.generator_version,
            "seed": request.seed,
            "parameters": parameters,
        }
        identity_bytes = _canonical_json(identity).encode("utf-8")
        rng = DeterministicRng(hashlib.sha256(identity_bytes).digest())

        # Give generators a detached JSON-safe copy so a handler cannot mutate the request.
        detached_parameters = json.loads(_canonical_json(parameters))
        try:
            raw_output = handler(detached_parameters, rng)
        except GenerationError:
            raise
        except Exception as exc:  # pragma: no cover - defensive boundary
            raise GenerationExecutionError(
                f"generator {request.generator_type}@{request.generator_version} failed closed"
            ) from exc

        try:
            output = _canonicalize(raw_output, path="output", max_bytes=MAX_OUTPUT_BYTES)
        except GenerationInputError as exc:
            raise GenerationExecutionError(str(exc)) from exc

        provenance_payload = {**identity, "output": output}
        provenance_hash = hashlib.sha256(
            _canonical_json(provenance_payload).encode("utf-8")
        ).hexdigest()
        return GenerationResult(
            generator_type=request.generator_type,
            generator_version=request.generator_version,
            seed=request.seed,
            parameters=parameters,
            output=output,
            provenance_hash=provenance_hash,
        )


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


def _generate_weapon(parameters: Mapping[str, Any], rng: DeterministicRng) -> dict[str, Any]:
    classes = ("sidearm", "rifle", "shotgun", "melee", "launcher")
    weapon_class = _choice_parameter(parameters, "weapon_class", allowed=classes, rng=rng)
    difficulty = _number_parameter(
        parameters,
        "difficulty",
        default=1.0,
        minimum=0.5,
        maximum=3.0,
    )
    rarity = parameters.get("rarity")
    rarity_weights = (("common", 50), ("uncommon", 28), ("rare", 15), ("epic", 6), ("legendary", 1))
    if rarity is None:
        rarity = rng.weighted_choice(rarity_weights)
    elif rarity not in {name for name, _ in rarity_weights}:
        raise GenerationInputError("rarity is not a supported weapon rarity")

    profiles = {
        "sidearm": (12.0, 24.0, 2.5, 5.5, 20.0, 55.0),
        "rifle": (18.0, 34.0, 4.0, 9.0, 45.0, 120.0),
        "shotgun": (30.0, 60.0, 0.7, 1.8, 8.0, 30.0),
        "melee": (22.0, 50.0, 0.8, 2.2, 1.0, 3.5),
        "launcher": (55.0, 110.0, 0.25, 0.8, 30.0, 100.0),
    }
    min_damage, max_damage, min_rate, max_rate, min_range, max_range = profiles[weapon_class]
    rarity_scale = {
        "common": 1.0,
        "uncommon": 1.08,
        "rare": 1.17,
        "epic": 1.28,
        "legendary": 1.4,
    }[str(rarity)]
    damage = round(rng.uniform(min_damage, max_damage) * difficulty * rarity_scale, 2)
    fire_rate = round(rng.uniform(min_rate, max_rate), 2)
    effective_range = round(rng.uniform(min_range, max_range), 2)

    modifier_count = {"common": 0, "uncommon": 1, "rare": 1, "epic": 2, "legendary": 3}[
        str(rarity)
    ]
    modifiers = rng.sample(
        ("precision", "stability", "quick_reload", "critical_focus", "lightweight"),
        modifier_count,
    )
    prefixes = ("Solar", "Aether", "Nova", "Ember", "Astral", "Echo", "Vanguard")
    nouns = {
        "sidearm": ("Spark", "Needle", "Sentinel"),
        "rifle": ("Lance", "Vector", "Longstar"),
        "shotgun": ("Breaker", "Thunder", "Comet"),
        "melee": ("Edge", "Fang", "Arc"),
        "launcher": ("Meteor", "Hammer", "Tempest"),
    }
    name = f"{rng.choice(prefixes)} {rng.choice(nouns[weapon_class])}"
    return {
        "name": name,
        "weapon_class": weapon_class,
        "rarity": rarity,
        "damage": damage,
        "fire_rate_per_second": fire_rate,
        "effective_range_m": effective_range,
        "modifiers": modifiers,
        "balance_profile": "game_forge_weapon_v1",
    }


def _generate_mission(parameters: Mapping[str, Any], rng: DeterministicRng) -> dict[str, Any]:
    styles = ("exploration", "combat", "delivery", "rescue", "collection", "stealth")
    style = _choice_parameter(parameters, "style", allowed=styles, rng=rng)
    difficulty = _int_parameter(parameters, "difficulty", default=3, minimum=1, maximum=10)
    objective_count = _int_parameter(
        parameters,
        "objective_count",
        default=1 + rng.randbelow(3),
        minimum=1,
        maximum=5,
    )
    target_pool = {
        "exploration": ("ancient beacon", "uncharted ridge", "hidden chamber"),
        "combat": ("hostile patrol", "raider captain", "defence wave"),
        "delivery": ("medical cache", "navigation core", "research parcel"),
        "rescue": ("stranded explorer", "disabled crew", "lost survey team"),
        "collection": ("crystal sample", "archive fragment", "rare alloy"),
        "stealth": ("sensor relay", "guarded archive", "restricted terminal"),
    }[style]
    verbs = {
        "exploration": "Discover",
        "combat": "Defeat",
        "delivery": "Deliver",
        "rescue": "Rescue",
        "collection": "Recover",
        "stealth": "Infiltrate",
    }
    objectives = []
    for index in range(objective_count):
        target = rng.choice(target_pool)
        objectives.append(
            {
                "id": f"objective-{index + 1}",
                "description": f"{verbs[style]} {target}",
                "required_progress": 1 + (rng.randbelow(max(1, min(difficulty, 5))) if style == "collection" else 0),
            }
        )
    reward = 75 + (difficulty * 40) + rng.randint(0, 25)
    title_words = ("Signal", "Frontier", "Echo", "Horizon", "Beacon", "Rift", "Wayfinder")
    return {
        "mission_id": f"mission-{rng.randint(100000, 999999)}",
        "title": f"{rng.choice(title_words)}: {style.title()}",
        "style": style,
        "difficulty": difficulty,
        "objectives": objectives,
        "reward": {
            "currency_namespace": "project_game_currency",
            "amount": reward,
            "commercial_value": False,
        },
    }


def _generate_star_system(parameters: Mapping[str, Any], rng: DeterministicRng) -> dict[str, Any]:
    planet_count_value = parameters.get("planet_count")
    if planet_count_value is None:
        planet_count = rng.randint(3, 9)
    else:
        planet_count = _int_parameter(
            parameters,
            "planet_count",
            default=3,
            minimum=1,
            maximum=12,
        )
    star_class = _choice_parameter(
        parameters,
        "star_class",
        allowed=("red_dwarf", "orange_dwarf", "yellow_dwarf", "white_star", "blue_white_star"),
        rng=rng,
    )
    syllables = ("Astra", "Cyr", "Ely", "Nova", "Orin", "Rhea", "Sol", "Vela", "Zyra")
    system_name = f"{rng.choice(syllables)}-{rng.randint(100, 999)}"
    biomes = ("rocky", "oceanic", "desert", "ice", "volcanic", "gas_giant", "forest")
    atmospheres = ("none", "thin", "temperate", "dense", "toxic")
    planets = []
    for index in range(planet_count):
        biome = str(rng.choice(biomes))
        atmosphere = "dense" if biome == "gas_giant" else str(rng.choice(atmospheres))
        planets.append(
            {
                "id": f"planet-{index + 1}",
                "name": f"{system_name} {index + 1}",
                "biome": biome,
                "atmosphere": atmosphere,
                "gravity_g": round(rng.uniform(0.15, 2.4), 2),
                "settlement_probability": round(rng.uniform(0.0, 0.8), 3),
                "points_of_interest": rng.randint(0, 6),
            }
        )
    return {
        "system_id": system_name.lower(),
        "name": system_name,
        "star_class": star_class,
        "planet_count": planet_count,
        "planets": planets,
        "simulation_fidelity": "gameplay_descriptor",
    }


def build_default_generator_registry() -> GeneratorRegistry:
    from .procedural_expansion import register_procedural_expansion

    registry = GeneratorRegistry()
    registry.register("weapon", "v1", _generate_weapon)
    registry.register("mission", "v1", _generate_mission)
    registry.register("star_system", "v1", _generate_star_system)
    register_procedural_expansion(registry)
    return registry


DEFAULT_GENERATOR_REGISTRY = build_default_generator_registry()


def generate(request: GenerationRequest) -> GenerationResult:
    """Generate through the immutable canonical default registry."""

    return DEFAULT_GENERATOR_REGISTRY.generate(request)
