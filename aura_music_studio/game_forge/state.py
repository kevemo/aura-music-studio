"""Persisted canonical gameplay state for Game Forge Creative Labs.

This layer deliberately reuses the platform project/tenant storage authority rather than
creating a second account or project model. It persists bounded gameplay state beside an
existing project, records deterministic-generation provenance, and provides inventory and
mission/quest mutations that fail closed on stale revisions or malformed state.

It is not a commerce ledger. Project rewards are explicitly non-commercial game currency
and cannot represent Shared Skies subscriptions, Cosmic Creation Coins, LIVE Gifts or any
other Chat 6 payment/entitlement authority.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError, model_validator

from ..tenant_storage import project_path
from .procedural import GenerationResult

STATE_FILENAME = "game_forge_state.json"
MAX_STATE_BYTES = 2_000_000
MAX_INVENTORY_SLOTS = 500
MAX_ITEM_QUANTITY = 999_999
MAX_QUESTS = 500
MAX_OBJECTIVES_PER_QUEST = 64
MAX_GENERATION_RECORDS = 500
MAX_METADATA_KEYS = 128
MAX_METADATA_BYTES = 32_768

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SLOT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")

QuestStatus = Literal["active", "completed", "failed"]
ObjectiveUpdateMode = Literal["increment", "set"]


class GameStateError(ValueError):
    """Base class for persisted Game Forge state failures."""


class GameStateIntegrityError(GameStateError):
    """Raised when saved state is malformed, tampered with or exceeds a safety bound."""


class GameStateConflictError(GameStateError):
    """Raised when an optimistic state revision no longer matches the persisted revision."""


class GameStateMutationError(GameStateError):
    """Raised when a requested gameplay mutation violates the canonical state contract."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _bounded_metadata(value: dict[str, Any], *, label: str) -> dict[str, Any]:
    if len(value) > MAX_METADATA_KEYS:
        raise ValueError(f"{label} contains too many keys")
    try:
        encoded = _canonical_json(value).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be JSON-safe") from exc
    if len(encoded) > MAX_METADATA_BYTES:
        raise ValueError(f"{label} exceeds the bounded metadata payload")
    return json.loads(encoded.decode("utf-8"))


def _validate_id(value: str, *, label: str) -> str:
    if not _ID_RE.fullmatch(value):
        raise ValueError(f"{label} must be a bounded canonical identifier")
    return value


def _generation_hash_payload(record: "GenerationRecord") -> dict[str, Any]:
    return {
        "generator_type": record.generator_type,
        "generator_version": record.generator_version,
        "seed": record.seed,
        "parameters": record.parameters,
        "output": record.output,
    }


def _generation_hash(record: "GenerationRecord") -> str:
    return hashlib.sha256(_canonical_json(_generation_hash_payload(record)).encode("utf-8")).hexdigest()


class GenerationRecord(BaseModel):
    """Persisted deterministic-generation result with tamper-evident provenance."""

    model_config = ConfigDict(extra="forbid")

    generator_type: str = Field(min_length=1, max_length=64)
    generator_version: str = Field(min_length=1, max_length=32)
    seed: StrictInt | StrictStr
    parameters: dict[str, Any] = Field(default_factory=dict)
    output: Any
    provenance_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def from_result(cls, result: GenerationResult) -> "GenerationRecord":
        return cls.model_validate(result.model_dump(mode="json"))

    @model_validator(mode="after")
    def verify_provenance(self) -> "GenerationRecord":
        if _generation_hash(self) != self.provenance_hash:
            raise ValueError("generation provenance hash does not match the persisted result")
        return self


class InventoryStack(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str = Field(min_length=1, max_length=128)
    quantity: int = Field(ge=1, le=MAX_ITEM_QUANTITY)
    max_stack: int = Field(default=99, ge=1, le=MAX_ITEM_QUANTITY)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_stack(self) -> "InventoryStack":
        _validate_id(self.item_id, label="item_id")
        if self.quantity > self.max_stack:
            raise ValueError("quantity cannot exceed max_stack")
        self.metadata = _bounded_metadata(self.metadata, label="inventory metadata")
        return self


class InventoryState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capacity: int = Field(default=40, ge=1, le=MAX_INVENTORY_SLOTS)
    stacks: list[InventoryStack] = Field(default_factory=list, max_length=MAX_INVENTORY_SLOTS)
    equipped: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_inventory(self) -> "InventoryState":
        if len(self.stacks) > self.capacity:
            raise ValueError("inventory stack count exceeds capacity")
        item_ids = [stack.item_id for stack in self.stacks]
        if len(set(item_ids)) != len(item_ids):
            raise ValueError("inventory cannot contain duplicate item_id stacks")
        if len(self.equipped) > 64:
            raise ValueError("too many equipment slots")
        known_items = set(item_ids)
        normalized: dict[str, str] = {}
        for slot, item_id in self.equipped.items():
            if not _SLOT_RE.fullmatch(slot):
                raise ValueError("equipment slot must be a bounded canonical identifier")
            _validate_id(item_id, label="equipped item_id")
            if item_id not in known_items:
                raise ValueError(f"equipped item is not present in inventory: {item_id}")
            normalized[slot] = item_id
        self.equipped = normalized
        return self


class QuestObjectiveState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objective_id: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=500)
    required_progress: int = Field(default=1, ge=1, le=1_000_000)
    current_progress: int = Field(default=0, ge=0, le=1_000_000)
    completed: bool = False

    @model_validator(mode="after")
    def validate_objective(self) -> "QuestObjectiveState":
        _validate_id(self.objective_id, label="objective_id")
        if self.current_progress > self.required_progress:
            self.current_progress = self.required_progress
        self.completed = self.current_progress >= self.required_progress
        return self


class ProjectGameReward(BaseModel):
    """A game-only reward marker with no real-money or platform-commercial authority."""

    model_config = ConfigDict(extra="forbid")

    currency_namespace: Literal["project_game_currency"] = "project_game_currency"
    amount: int = Field(default=0, ge=0, le=1_000_000_000)
    commercial_value: Literal[False] = False


class QuestState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quest_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=240)
    status: QuestStatus = "active"
    difficulty: int = Field(default=1, ge=1, le=100)
    objectives: list[QuestObjectiveState] = Field(
        default_factory=list,
        min_length=1,
        max_length=MAX_OBJECTIVES_PER_QUEST,
    )
    reward: ProjectGameReward = Field(default_factory=ProjectGameReward)
    source_generation_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    started_at: str = Field(default_factory=utc_now)
    completed_at: str | None = None
    failed_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_quest(self) -> "QuestState":
        _validate_id(self.quest_id, label="quest_id")
        objective_ids = [objective.objective_id for objective in self.objectives]
        if len(set(objective_ids)) != len(objective_ids):
            raise ValueError("quest objectives must have unique IDs")
        all_complete = all(objective.completed for objective in self.objectives)
        if self.status == "completed" and not all_complete:
            raise ValueError("completed quests require every objective to be complete")
        if self.status == "completed":
            self.completed_at = self.completed_at or utc_now()
            self.failed_at = None
        elif self.status == "failed":
            self.failed_at = self.failed_at or utc_now()
            self.completed_at = None
        else:
            self.completed_at = None
            self.failed_at = None
        self.metadata = _bounded_metadata(self.metadata, label="quest metadata")
        return self


class GameStateManifest(BaseModel):
    """Versioned gameplay state persisted inside one canonical project root."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    project_name: str = Field(min_length=1, max_length=120)
    state_revision: int = Field(default=0, ge=0)
    inventory: InventoryState = Field(default_factory=InventoryState)
    quests: list[QuestState] = Field(default_factory=list, max_length=MAX_QUESTS)
    generation_history: list[GenerationRecord] = Field(
        default_factory=list,
        max_length=MAX_GENERATION_RECORDS,
    )
    active_scene_id: str | None = Field(default=None, max_length=128)
    checkpoint_id: str | None = Field(default=None, max_length=128)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_manifest(self) -> "GameStateManifest":
        if len(self.quests) > MAX_QUESTS:
            raise ValueError("too many quests in gameplay state")
        quest_ids = [quest.quest_id for quest in self.quests]
        if len(set(quest_ids)) != len(quest_ids):
            raise ValueError("gameplay state cannot contain duplicate quest IDs")
        hashes = [record.provenance_hash for record in self.generation_history]
        if len(set(hashes)) != len(hashes):
            raise ValueError("generation history cannot contain duplicate provenance hashes")
        if self.active_scene_id is not None:
            _validate_id(self.active_scene_id, label="active_scene_id")
        if self.checkpoint_id is not None:
            _validate_id(self.checkpoint_id, label="checkpoint_id")
        self.metadata = _bounded_metadata(self.metadata, label="gameplay state metadata")
        return self


class GameStateStore:
    """Atomic, optimistic-revision store for one canonical Game Forge project.

    Public/member-facing callers should normally construct this through ``for_project`` so
    the current tenant storage boundary is applied before any state file is addressed.
    """

    def __init__(self, project_dir: Path):
        self.project_dir = Path(project_dir).resolve()
        self.path = self.project_dir / STATE_FILENAME

    @classmethod
    def for_project(cls, project_name: str, *, must_exist: bool = True) -> "GameStateStore":
        return cls(project_path(project_name, must_exist=must_exist))

    def exists(self) -> bool:
        return self.path.is_file()

    def initialize(
        self,
        *,
        project_name: str,
        inventory_capacity: int = 40,
        metadata: dict[str, Any] | None = None,
    ) -> GameStateManifest:
        if self.exists():
            return self.load()
        self.project_dir.mkdir(parents=True, exist_ok=True)
        manifest = GameStateManifest(
            project_name=project_name,
            inventory=InventoryState(capacity=inventory_capacity),
            metadata=metadata or {},
        )
        return self.save(manifest, expected_revision=-1)

    def _read_bytes(self) -> bytes:
        try:
            payload = self.path.read_bytes()
        except FileNotFoundError:
            raise
        if len(payload) > MAX_STATE_BYTES:
            raise GameStateIntegrityError("saved Game Forge state exceeds the bounded file size")
        return payload

    def load(self) -> GameStateManifest:
        if not self.exists():
            raise FileNotFoundError(self.path)
        try:
            return GameStateManifest.model_validate_json(self._read_bytes())
        except GameStateIntegrityError:
            raise
        except (ValidationError, ValueError, TypeError) as exc:
            raise GameStateIntegrityError("saved Game Forge state failed canonical validation") from exc

    def _current_revision(self) -> int:
        if not self.exists():
            return -1
        return self.load().state_revision

    def save(
        self,
        manifest: GameStateManifest,
        *,
        expected_revision: int | None = None,
    ) -> GameStateManifest:
        self.project_dir.mkdir(parents=True, exist_ok=True)
        current_revision = self._current_revision()
        expected = manifest.state_revision if expected_revision is None else expected_revision
        if expected != current_revision:
            raise GameStateConflictError(
                f"stale Game Forge state revision: expected {expected}, persisted {current_revision}"
            )

        # Round-trip through validation so mutated nested models cannot bypass provenance,
        # equipment, quest or reward invariants before reaching disk.
        try:
            validated = GameStateManifest.model_validate(manifest.model_dump(mode="json"))
        except ValidationError as exc:
            raise GameStateIntegrityError("Game Forge state cannot be saved because validation failed") from exc
        validated.state_revision = current_revision + 1
        validated.updated_at = utc_now()
        payload = json.dumps(validated.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n"
        encoded = payload.encode("utf-8")
        if len(encoded) > MAX_STATE_BYTES:
            raise GameStateIntegrityError("Game Forge state exceeds the bounded save size")

        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_bytes(encoded)
        temporary.replace(self.path)
        return validated

    def _load_for_mutation(self) -> tuple[GameStateManifest, int]:
        manifest = self.load()
        return manifest, manifest.state_revision

    def record_generation(self, result: GenerationResult) -> GameStateManifest:
        record = GenerationRecord.from_result(result)
        manifest, revision = self._load_for_mutation()
        if any(existing.provenance_hash == record.provenance_hash for existing in manifest.generation_history):
            return manifest
        if len(manifest.generation_history) >= MAX_GENERATION_RECORDS:
            raise GameStateMutationError("generation history is at capacity")
        manifest.generation_history.append(record)
        return self.save(manifest, expected_revision=revision)

    def give_item(
        self,
        item_id: str,
        quantity: int,
        *,
        max_stack: int = 99,
        metadata: dict[str, Any] | None = None,
    ) -> GameStateManifest:
        _validate_id(item_id, label="item_id")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
            raise GameStateMutationError("item quantity must be a positive integer")
        if isinstance(max_stack, bool) or not isinstance(max_stack, int) or not (1 <= max_stack <= MAX_ITEM_QUANTITY):
            raise GameStateMutationError("max_stack is outside the bounded range")
        manifest, revision = self._load_for_mutation()
        existing = next((stack for stack in manifest.inventory.stacks if stack.item_id == item_id), None)
        if existing is None:
            if len(manifest.inventory.stacks) >= manifest.inventory.capacity:
                raise GameStateMutationError("inventory is at capacity")
            stack = InventoryStack(
                item_id=item_id,
                quantity=quantity,
                max_stack=max_stack,
                metadata=metadata or {},
            )
            manifest.inventory.stacks.append(stack)
        else:
            if existing.max_stack != max_stack:
                raise GameStateMutationError("existing item max_stack does not match the mutation")
            new_quantity = existing.quantity + quantity
            if new_quantity > existing.max_stack:
                raise GameStateMutationError("item quantity would exceed max_stack")
            existing.quantity = new_quantity
            if metadata is not None:
                existing.metadata = _bounded_metadata(
                    {**existing.metadata, **metadata},
                    label="inventory metadata",
                )
        return self.save(manifest, expected_revision=revision)

    def remove_item(self, item_id: str, quantity: int) -> GameStateManifest:
        _validate_id(item_id, label="item_id")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
            raise GameStateMutationError("item quantity must be a positive integer")
        manifest, revision = self._load_for_mutation()
        stack = next((item for item in manifest.inventory.stacks if item.item_id == item_id), None)
        if stack is None:
            raise GameStateMutationError(f"item is not present in inventory: {item_id}")
        if quantity > stack.quantity:
            raise GameStateMutationError("cannot remove more items than are present")
        stack.quantity -= quantity
        if stack.quantity == 0:
            manifest.inventory.stacks = [item for item in manifest.inventory.stacks if item.item_id != item_id]
            manifest.inventory.equipped = {
                slot: equipped_id
                for slot, equipped_id in manifest.inventory.equipped.items()
                if equipped_id != item_id
            }
        return self.save(manifest, expected_revision=revision)

    def drop_item(self, item_id: str, quantity: int) -> GameStateManifest:
        """Remove a bounded quantity from inventory; world-drop spawning is a later runtime gate."""

        return self.remove_item(item_id, quantity)

    def equip_item(self, slot: str, item_id: str) -> GameStateManifest:
        if not _SLOT_RE.fullmatch(slot):
            raise GameStateMutationError("equipment slot must be a bounded canonical identifier")
        _validate_id(item_id, label="item_id")
        manifest, revision = self._load_for_mutation()
        if not any(stack.item_id == item_id for stack in manifest.inventory.stacks):
            raise GameStateMutationError(f"cannot equip missing inventory item: {item_id}")
        manifest.inventory.equipped[slot] = item_id
        return self.save(manifest, expected_revision=revision)

    def unequip_item(self, slot: str) -> GameStateManifest:
        if not _SLOT_RE.fullmatch(slot):
            raise GameStateMutationError("equipment slot must be a bounded canonical identifier")
        manifest, revision = self._load_for_mutation()
        manifest.inventory.equipped.pop(slot, None)
        return self.save(manifest, expected_revision=revision)

    def start_mission_from_generation(self, result: GenerationResult) -> GameStateManifest:
        if result.generator_type != "mission":
            raise GameStateMutationError("only canonical mission generator output can start a generated mission")
        record = GenerationRecord.from_result(result)
        if not isinstance(result.output, dict):
            raise GameStateMutationError("mission generator output must be a mapping")
        output = result.output
        try:
            quest_id = str(output["mission_id"])
            title = str(output["title"])
            difficulty = int(output["difficulty"])
            raw_objectives = output["objectives"]
            raw_reward = output["reward"]
        except (KeyError, TypeError, ValueError) as exc:
            raise GameStateMutationError("mission generator output is missing canonical fields") from exc
        if not isinstance(raw_objectives, list) or not raw_objectives:
            raise GameStateMutationError("generated mission requires at least one objective")
        if not isinstance(raw_reward, dict):
            raise GameStateMutationError("generated mission reward must be a mapping")

        objectives: list[QuestObjectiveState] = []
        try:
            for raw in raw_objectives:
                if not isinstance(raw, dict):
                    raise TypeError("objective is not a mapping")
                objectives.append(
                    QuestObjectiveState(
                        objective_id=str(raw["id"]),
                        description=str(raw["description"]),
                        required_progress=int(raw["required_progress"]),
                    )
                )
            reward = ProjectGameReward.model_validate(raw_reward)
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            raise GameStateMutationError("generated mission violates the persisted quest contract") from exc

        quest = QuestState(
            quest_id=quest_id,
            title=title,
            difficulty=difficulty,
            objectives=objectives,
            reward=reward,
            source_generation_hash=record.provenance_hash,
            metadata={
                "generator_type": record.generator_type,
                "generator_version": record.generator_version,
            },
        )
        manifest, revision = self._load_for_mutation()
        if any(existing.quest_id == quest.quest_id for existing in manifest.quests):
            raise GameStateMutationError(f"quest already exists: {quest.quest_id}")
        if len(manifest.quests) >= MAX_QUESTS:
            raise GameStateMutationError("quest state is at capacity")
        if not any(existing.provenance_hash == record.provenance_hash for existing in manifest.generation_history):
            if len(manifest.generation_history) >= MAX_GENERATION_RECORDS:
                raise GameStateMutationError("generation history is at capacity")
            manifest.generation_history.append(record)
        manifest.quests.append(quest)
        return self.save(manifest, expected_revision=revision)

    def update_objective(
        self,
        quest_id: str,
        objective_id: str,
        value: int = 1,
        *,
        mode: ObjectiveUpdateMode = "increment",
    ) -> GameStateManifest:
        _validate_id(quest_id, label="quest_id")
        _validate_id(objective_id, label="objective_id")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise GameStateMutationError("objective progress value must be a non-negative integer")
        if mode not in {"increment", "set"}:
            raise GameStateMutationError("objective update mode must be increment or set")

        manifest, revision = self._load_for_mutation()
        quest = next((item for item in manifest.quests if item.quest_id == quest_id), None)
        if quest is None:
            raise GameStateMutationError(f"unknown quest: {quest_id}")
        if quest.status != "active":
            raise GameStateMutationError("only active quests can update objective progress")
        objective = next(
            (item for item in quest.objectives if item.objective_id == objective_id),
            None,
        )
        if objective is None:
            raise GameStateMutationError(f"unknown quest objective: {objective_id}")

        target = objective.current_progress + value if mode == "increment" else value
        objective.current_progress = min(target, objective.required_progress)
        objective.completed = objective.current_progress >= objective.required_progress
        if all(item.completed for item in quest.objectives):
            quest.status = "completed"
            quest.completed_at = utc_now()
            quest.failed_at = None
        return self.save(manifest, expected_revision=revision)

    def fail_quest(self, quest_id: str) -> GameStateManifest:
        _validate_id(quest_id, label="quest_id")
        manifest, revision = self._load_for_mutation()
        quest = next((item for item in manifest.quests if item.quest_id == quest_id), None)
        if quest is None:
            raise GameStateMutationError(f"unknown quest: {quest_id}")
        if quest.status != "active":
            raise GameStateMutationError("only active quests can fail")
        quest.status = "failed"
        quest.failed_at = utc_now()
        quest.completed_at = None
        return self.save(manifest, expected_revision=revision)

    def set_checkpoint(
        self,
        checkpoint_id: str | None,
        *,
        active_scene_id: str | None = None,
    ) -> GameStateManifest:
        if checkpoint_id is not None:
            _validate_id(checkpoint_id, label="checkpoint_id")
        if active_scene_id is not None:
            _validate_id(active_scene_id, label="active_scene_id")
        manifest, revision = self._load_for_mutation()
        manifest.checkpoint_id = checkpoint_id
        if active_scene_id is not None:
            manifest.active_scene_id = active_scene_id
        return self.save(manifest, expected_revision=revision)
