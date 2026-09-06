from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

import aura_music_studio.game_forge.state as game_state_module
from aura_music_studio.game_forge.procedural import GenerationRequest, generate
from aura_music_studio.game_forge.state import (
    MAX_STATE_BYTES,
    GameStateConflictError,
    GameStateIntegrityError,
    GameStateManifest,
    GameStateMutationError,
    GameStateStore,
    ProjectGameReward,
)


def generation(generator_type: str, *, seed: int | str = 42, parameters: dict | None = None):
    return generate(
        GenerationRequest(
            generator_type=generator_type,
            generator_version="v1",
            seed=seed,
            parameters=parameters or {},
        )
    )


def initialized_store(tmp_path: Path, *, capacity: int = 40) -> GameStateStore:
    store = GameStateStore(tmp_path / "project")
    manifest = store.initialize(project_name="test-game", inventory_capacity=capacity)
    assert manifest.state_revision == 0
    return store


def test_initialize_and_reload_round_trip(tmp_path):
    store = initialized_store(tmp_path)
    loaded = store.load()

    assert loaded.project_name == "test-game"
    assert loaded.schema_version == 1
    assert loaded.state_revision == 0
    assert loaded.inventory.capacity == 40
    assert loaded.inventory.stacks == []
    assert store.path.name == "game_forge_state.json"


def test_initialize_is_idempotent_for_existing_state(tmp_path):
    store = initialized_store(tmp_path)
    first = store.give_item("health-pack", 2, max_stack=10)

    second = store.initialize(project_name="ignored", inventory_capacity=1)

    assert second == first
    assert second.project_name == "test-game"
    assert second.inventory.capacity == 40
    assert second.inventory.stacks[0].quantity == 2


def test_inventory_give_equip_remove_and_auto_unequip_persist(tmp_path):
    store = initialized_store(tmp_path)
    store.give_item("laser-rifle", 1, max_stack=1, metadata={"rarity": "rare"})
    store.give_item("health-pack", 2, max_stack=5)
    store.give_item("health-pack", 1, max_stack=5)
    equipped = store.equip_item("primary", "laser-rifle")

    assert equipped.inventory.equipped == {"primary": "laser-rifle"}
    health = next(item for item in equipped.inventory.stacks if item.item_id == "health-pack")
    assert health.quantity == 3

    store.remove_item("laser-rifle", 1)
    reloaded = store.load()
    assert {item.item_id for item in reloaded.inventory.stacks} == {"health-pack"}
    assert reloaded.inventory.equipped == {}


def test_inventory_capacity_and_stack_limits_fail_closed(tmp_path):
    store = initialized_store(tmp_path, capacity=1)
    store.give_item("item-a", 1, max_stack=2)

    with pytest.raises(GameStateMutationError, match="inventory is at capacity"):
        store.give_item("item-b", 1)
    with pytest.raises(GameStateMutationError, match="exceed max_stack"):
        store.give_item("item-a", 2, max_stack=2)
    with pytest.raises(GameStateMutationError, match="max_stack does not match"):
        store.give_item("item-a", 1, max_stack=3)
    with pytest.raises(GameStateMutationError, match="more items than are present"):
        store.remove_item("item-a", 2)


def test_drop_item_is_bounded_inventory_removal_not_world_spawn_claim(tmp_path):
    store = initialized_store(tmp_path)
    store.give_item("ore", 4, max_stack=10)

    result = store.drop_item("ore", 2)

    assert result.inventory.stacks[0].quantity == 2


def test_equip_requires_existing_inventory_item(tmp_path):
    store = initialized_store(tmp_path)

    with pytest.raises(GameStateMutationError, match="cannot equip missing"):
        store.equip_item("primary", "missing-rifle")


def test_generation_record_persists_exact_provenance(tmp_path):
    store = initialized_store(tmp_path)
    result = generation("weapon", seed="persist-me", parameters={"weapon_class": "rifle"})

    saved = store.record_generation(result)
    loaded = GameStateStore(store.project_dir).load()

    assert len(saved.generation_history) == 1
    assert loaded.generation_history[0].provenance_hash == result.provenance_hash
    assert loaded.generation_history[0].output == result.output
    assert loaded.generation_history[0].seed == "persist-me"


def test_duplicate_generation_record_is_idempotent_without_revision_bump(tmp_path):
    store = initialized_store(tmp_path)
    result = generation("weapon", seed=7)
    first = store.record_generation(result)
    second = store.record_generation(result)

    assert first.state_revision == second.state_revision
    assert len(second.generation_history) == 1


def test_generation_history_tampering_is_detected_on_reload(tmp_path):
    store = initialized_store(tmp_path)
    result = generation("weapon", seed="tamper-check")
    store.record_generation(result)

    payload = json.loads(store.path.read_text(encoding="utf-8"))
    payload["generation_history"][0]["output"]["damage"] += 1
    store.path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(GameStateIntegrityError, match="failed canonical validation"):
        store.load()


def test_oversized_saved_state_is_rejected_before_model_parsing(tmp_path):
    store = initialized_store(tmp_path)
    store.path.write_bytes(b"{" + (b" " * MAX_STATE_BYTES) + b"}")

    with pytest.raises(GameStateIntegrityError, match="bounded file size"):
        store.load()


def test_generated_mission_becomes_persisted_quest_and_records_source(tmp_path):
    store = initialized_store(tmp_path)
    result = generation("mission", seed="quest-seed", parameters={"difficulty": 6})

    saved = store.start_mission_from_generation(result)
    quest = saved.quests[0]

    assert quest.quest_id == result.output["mission_id"]
    assert quest.title == result.output["title"]
    assert quest.status == "active"
    assert quest.source_generation_hash == result.provenance_hash
    assert quest.reward.currency_namespace == "project_game_currency"
    assert quest.reward.commercial_value is False
    assert len(saved.generation_history) == 1


def test_generated_mission_progress_completes_and_survives_reload(tmp_path):
    store = initialized_store(tmp_path)
    result = generation(
        "mission",
        seed="complete-quest",
        parameters={"style": "collection", "difficulty": 4, "objective_count": 3},
    )
    started = store.start_mission_from_generation(result)
    quest_id = started.quests[0].quest_id

    for objective in list(started.quests[0].objectives):
        store.update_objective(
            quest_id,
            objective.objective_id,
            objective.required_progress,
            mode="set",
        )

    reloaded = GameStateStore(store.project_dir).load()
    quest = reloaded.quests[0]
    assert quest.status == "completed"
    assert quest.completed_at is not None
    assert quest.failed_at is None
    assert all(objective.completed for objective in quest.objectives)


def test_objective_progress_is_clamped_and_invalid_mutations_reject(tmp_path):
    store = initialized_store(tmp_path)
    started = store.start_mission_from_generation(
        generation("mission", seed="objective-bounds", parameters={"objective_count": 1})
    )
    quest = started.quests[0]
    objective = quest.objectives[0]

    completed = store.update_objective(
        quest.quest_id,
        objective.objective_id,
        objective.required_progress + 999,
        mode="increment",
    )
    assert completed.quests[0].objectives[0].current_progress == objective.required_progress

    with pytest.raises(GameStateMutationError, match="only active quests"):
        store.update_objective(quest.quest_id, objective.objective_id, 1)
    with pytest.raises(GameStateMutationError, match="non-negative"):
        store.update_objective(quest.quest_id, objective.objective_id, -1)


def test_fail_quest_persists_terminal_state_and_blocks_progress(tmp_path):
    store = initialized_store(tmp_path)
    started = store.start_mission_from_generation(generation("mission", seed="fail-me"))
    quest = started.quests[0]

    failed = store.fail_quest(quest.quest_id)
    assert failed.quests[0].status == "failed"
    assert failed.quests[0].failed_at is not None

    with pytest.raises(GameStateMutationError, match="only active quests"):
        store.update_objective(quest.quest_id, quest.objectives[0].objective_id, 1)
    assert GameStateStore(store.project_dir).load().quests[0].status == "failed"


def test_only_mission_generator_output_can_start_generated_quest(tmp_path):
    store = initialized_store(tmp_path)

    with pytest.raises(GameStateMutationError, match="only canonical mission"):
        store.start_mission_from_generation(generation("weapon", seed=9))


def test_duplicate_generated_quest_is_rejected(tmp_path):
    store = initialized_store(tmp_path)
    result = generation("mission", seed="same-mission")
    store.start_mission_from_generation(result)

    with pytest.raises(GameStateMutationError, match="quest already exists"):
        store.start_mission_from_generation(result)


def test_project_game_reward_cannot_be_relabelled_as_commercial_value():
    with pytest.raises(ValidationError):
        ProjectGameReward(
            currency_namespace="project_game_currency",
            amount=10,
            commercial_value=True,
        )
    with pytest.raises(ValidationError):
        ProjectGameReward(
            currency_namespace="cosmic_creation_coins",
            amount=10,
            commercial_value=False,
        )


def test_checkpoint_and_scene_state_survive_reload(tmp_path):
    store = initialized_store(tmp_path)
    stored = store.set_checkpoint("checkpoint-3", active_scene_id="scene-moonbase")

    assert stored.checkpoint_id == "checkpoint-3"
    assert stored.active_scene_id == "scene-moonbase"
    reloaded = store.load()
    assert reloaded.checkpoint_id == "checkpoint-3"
    assert reloaded.active_scene_id == "scene-moonbase"


def test_optimistic_revision_rejects_stale_state_write(tmp_path):
    store = initialized_store(tmp_path)
    first_reader = store.load()
    stale_reader = store.load()

    first_reader.metadata["writer"] = "first"
    committed = store.save(first_reader)
    assert committed.state_revision == 1

    stale_reader.metadata["writer"] = "stale"
    with pytest.raises(GameStateConflictError, match="stale Game Forge state revision"):
        store.save(stale_reader)

    assert store.load().metadata["writer"] == "first"


def test_save_revalidates_mutated_nested_state_before_disk(tmp_path):
    store = initialized_store(tmp_path)
    manifest = store.give_item("tool", 1, max_stack=1)
    manifest.inventory.equipped["primary"] = "missing"

    with pytest.raises(GameStateIntegrityError, match="validation failed"):
        store.save(manifest)


def test_for_project_routes_through_canonical_tenant_project_path(tmp_path, monkeypatch):
    project_dir = tmp_path / "member" / "safe-project"
    project_dir.mkdir(parents=True)
    calls = []

    def fake_project_path(name: str, *, must_exist: bool = True) -> Path:
        calls.append((name, must_exist))
        return project_dir

    monkeypatch.setattr(game_state_module, "project_path", fake_project_path)
    store = GameStateStore.for_project("safe-project", must_exist=True)

    assert store.project_dir == project_dir.resolve()
    assert calls == [("safe-project", True)]


def test_manifest_rejects_duplicate_quest_and_generation_ids(tmp_path):
    store = initialized_store(tmp_path)
    result = generation("mission", seed="duplicates")
    saved = store.start_mission_from_generation(result)
    payload = saved.model_dump(mode="json")
    payload["quests"].append(payload["quests"][0])

    with pytest.raises(ValidationError, match="duplicate quest IDs"):
        GameStateManifest.model_validate(payload)

    payload = saved.model_dump(mode="json")
    payload["generation_history"].append(payload["generation_history"][0])
    with pytest.raises(ValidationError, match="duplicate provenance hashes"):
        GameStateManifest.model_validate(payload)
