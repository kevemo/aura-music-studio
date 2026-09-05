from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .game_forge_models import GameDNA
from .game_forge_store import game_dir, load_game, remove_public_snapshot, save_game
from .game_forge_world import load_world_optional
from .plans import GAME_CREATE

router = APIRouter(tags=["Aura Game Adventure State"])

ObjectiveKind = Literal["collect", "reach", "talk", "flag", "timer"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class AdventureItemDNA(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: _id("item"), min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=600)
    max_stack: int = Field(default=99, ge=1, le=9999)
    consumable: bool = False


class ObjectiveDNA(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: _id("objective"), min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=800)
    kind: ObjectiveKind
    target_entity_id: str | None = Field(default=None, max_length=160)
    target_count: int = Field(default=1, ge=1, le=1_000_000)
    flag: str | None = Field(default=None, max_length=120)
    seconds: float | None = Field(default=None, ge=0.1, le=86_400.0)
    reward_item_id: str | None = Field(default=None, max_length=120)
    reward_quantity: int = Field(default=1, ge=1, le=9999)
    completion_flag: str | None = Field(default=None, max_length=120)


class DialogueChoiceDNA(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: _id("choice"), min_length=1, max_length=120)
    label: str = Field(min_length=1, max_length=180)
    set_flag: str | None = Field(default=None, max_length=120)
    grant_item_id: str | None = Field(default=None, max_length=120)
    grant_quantity: int = Field(default=1, ge=1, le=9999)
    complete_dialogue: bool = True


class DialogueDNA(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: _id("dialogue"), min_length=1, max_length=120)
    trigger_entity_id: str = Field(min_length=1, max_length=160)
    speaker: str = Field(default="Character", min_length=1, max_length=120)
    lines: list[str] = Field(min_length=1, max_length=20)
    choices: list[DialogueChoiceDNA] = Field(default_factory=list, max_length=6)
    once: bool = True
    completion_flag: str | None = Field(default=None, max_length=120)


class GateDNA(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: _id("gate"), min_length=1, max_length=120)
    trigger_entity_id: str = Field(min_length=1, max_length=160)
    door_entity_id: str | None = Field(default=None, max_length=160)
    label: str = Field(default="Gate", min_length=1, max_length=120)
    requires_flag: str | None = Field(default=None, max_length=120)
    requires_item_id: str | None = Field(default=None, max_length=120)
    consume_item: bool = False
    open_flag: str | None = Field(default=None, max_length=120)


class AdventureStateDNA(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    game_id: str
    revision: int = Field(default=1, ge=1)
    items: list[AdventureItemDNA] = Field(default_factory=list, max_length=200)
    objectives: list[ObjectiveDNA] = Field(default_factory=list, max_length=200)
    dialogues: list[DialogueDNA] = Field(default_factory=list, max_length=100)
    gates: list[GateDNA] = Field(default_factory=list, max_length=100)
    initial_flags: dict[str, bool] = Field(default_factory=dict, max_length=200)
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)

    def touch(self) -> None:
        self.revision += 1
        self.updated_at = _now()


class CreateItemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=600)
    max_stack: int = Field(default=99, ge=1, le=9999)
    consumable: bool = False


class CreateObjectiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=800)
    kind: ObjectiveKind
    target_entity_id: str | None = Field(default=None, max_length=160)
    target_count: int = Field(default=1, ge=1, le=1_000_000)
    flag: str | None = Field(default=None, max_length=120)
    seconds: float | None = Field(default=None, ge=0.1, le=86_400.0)
    reward_item_id: str | None = Field(default=None, max_length=120)
    reward_quantity: int = Field(default=1, ge=1, le=9999)
    completion_flag: str | None = Field(default=None, max_length=120)


class CreateDialogueRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trigger_entity_id: str = Field(min_length=1, max_length=160)
    speaker: str = Field(default="Character", min_length=1, max_length=120)
    lines: list[str] = Field(min_length=1, max_length=20)
    choices: list[DialogueChoiceDNA] = Field(default_factory=list, max_length=6)
    once: bool = True
    completion_flag: str | None = Field(default=None, max_length=120)


class CreateGateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trigger_entity_id: str = Field(min_length=1, max_length=160)
    door_entity_id: str | None = Field(default=None, max_length=160)
    label: str = Field(default="Gate", min_length=1, max_length=120)
    requires_flag: str | None = Field(default=None, max_length=120)
    requires_item_id: str | None = Field(default=None, max_length=120)
    consume_item: bool = False
    open_flag: str | None = Field(default=None, max_length=120)


def adventure_path(game_id: str):
    return game_dir(game_id) / "adventure_dna.json"


def validate_adventure(state: AdventureStateDNA) -> None:
    collections = {
        "item": [row.id for row in state.items],
        "objective": [row.id for row in state.objectives],
        "dialogue": [row.id for row in state.dialogues],
        "gate": [row.id for row in state.gates],
    }
    for label, ids in collections.items():
        if len(ids) != len(set(ids)):
            raise ValueError(f"Adventure {label} IDs must be unique")
    item_ids = set(collections["item"])
    for objective in state.objectives:
        if objective.kind in {"collect", "reach", "talk"} and not objective.target_entity_id:
            raise ValueError(f"Objective '{objective.title}' requires a target entity")
        if objective.kind == "flag" and not objective.flag:
            raise ValueError(f"Objective '{objective.title}' requires a flag")
        if objective.kind == "timer" and objective.seconds is None:
            raise ValueError(f"Objective '{objective.title}' requires seconds")
        if objective.reward_item_id and objective.reward_item_id not in item_ids:
            raise ValueError(f"Objective '{objective.title}' references an unknown reward item")
    for dialogue in state.dialogues:
        for line in dialogue.lines:
            if not str(line).strip() or len(str(line)) > 400:
                raise ValueError(f"Dialogue '{dialogue.speaker}' lines must be 1-400 characters")
        for choice in dialogue.choices:
            if choice.grant_item_id and choice.grant_item_id not in item_ids:
                raise ValueError(f"Dialogue '{dialogue.speaker}' choice references an unknown item")
    for gate in state.gates:
        if gate.requires_item_id and gate.requires_item_id not in item_ids:
            raise ValueError(f"Gate '{gate.label}' references an unknown item")
    for flag in state.initial_flags:
        if not flag.strip() or len(flag) > 120:
            raise ValueError("Adventure flag names must be 1-120 characters")


def save_adventure(state: AdventureStateDNA) -> AdventureStateDNA:
    validate_adventure(state)
    path = adventure_path(state.game_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return state


def load_adventure_optional(game_id: str) -> AdventureStateDNA | None:
    path = adventure_path(game_id)
    if not path.is_file():
        return None
    state = AdventureStateDNA.model_validate_json(path.read_text(encoding="utf-8"))
    validate_adventure(state)
    return state


def ensure_adventure(game: GameDNA) -> AdventureStateDNA:
    state = load_adventure_optional(game.id)
    if state is not None:
        return state
    return save_adventure(AdventureStateDNA(game_id=game.id))


def adventure_integrity_payload(game_id: str) -> dict | None:
    state = load_adventure_optional(game_id)
    if state is None:
        return None
    return state.model_dump(mode="json", exclude={"created_at", "updated_at"})


def adventure_reference_blockers(game_id: str) -> list[str]:
    state = load_adventure_optional(game_id)
    if state is None:
        return []
    world = load_world_optional(game_id)
    world_ids = {row.id for row in world.entities} if world else set()
    blockers: list[str] = []
    for objective in state.objectives:
        if objective.target_entity_id and objective.target_entity_id not in world_ids:
            blockers.append(f"Adventure objective '{objective.title}' references a missing World DNA entity.")
    for dialogue in state.dialogues:
        if dialogue.trigger_entity_id not in world_ids:
            blockers.append(f"Adventure dialogue '{dialogue.speaker}' references a missing trigger entity.")
    for gate in state.gates:
        if gate.trigger_entity_id not in world_ids:
            blockers.append(f"Adventure gate '{gate.label}' references a missing trigger entity.")
        if gate.door_entity_id and gate.door_entity_id not in world_ids:
            blockers.append(f"Adventure gate '{gate.label}' references a missing door entity.")
    return blockers


def adventure_runtime_payload(game: GameDNA) -> dict:
    state = ensure_adventure(game)
    validate_adventure(state)
    return {
        "version": 1,
        "revision": state.revision,
        "items": [row.model_dump(mode="json") for row in state.items],
        "objectives": [row.model_dump(mode="json") for row in state.objectives],
        "dialogues": [row.model_dump(mode="json") for row in state.dialogues],
        "gates": [row.model_dump(mode="json") for row in state.gates],
        "initial_flags": dict(state.initial_flags),
        "save_policy": {
            "storage": "browser_local_only",
            "server_sync": False,
            "personal_data": False,
            "content_hash_versioned": True,
        },
        "arbitrary_script_source_allowed": False,
    }


def _invalidate(game: GameDNA) -> None:
    remove_public_snapshot(game)
    game.public_id = None
    game.rating_assessment = None
    game.latest_build = None
    game.status = "draft"
    game.touch()
    save_game(game)


def _persist_edit(game: GameDNA, state: AdventureStateDNA) -> AdventureStateDNA:
    state.touch()
    save_adventure(state)
    _invalidate(game)
    return state


def add_item(game: GameDNA, body: CreateItemRequest) -> AdventureItemDNA:
    state = ensure_adventure(game)
    row = AdventureItemDNA(**body.model_dump())
    state.items.append(row)
    _persist_edit(game, state)
    return row


def add_objective(game: GameDNA, body: CreateObjectiveRequest) -> ObjectiveDNA:
    state = ensure_adventure(game)
    row = ObjectiveDNA(**body.model_dump())
    state.objectives.append(row)
    _persist_edit(game, state)
    return row


def add_dialogue(game: GameDNA, body: CreateDialogueRequest) -> DialogueDNA:
    state = ensure_adventure(game)
    row = DialogueDNA(**body.model_dump())
    state.dialogues.append(row)
    _persist_edit(game, state)
    return row


def add_gate(game: GameDNA, body: CreateGateRequest) -> GateDNA:
    state = ensure_adventure(game)
    row = GateDNA(**body.model_dump())
    state.gates.append(row)
    _persist_edit(game, state)
    return row


def delete_adventure_row(game: GameDNA, kind: str, row_id: str) -> None:
    state = ensure_adventure(game)
    attr = {"items": "items", "objectives": "objectives", "dialogues": "dialogues", "gates": "gates"}.get(kind)
    if attr is None:
        raise ValueError("Unknown Adventure State collection")
    rows = list(getattr(state, attr))
    filtered = [row for row in rows if row.id != row_id]
    if len(filtered) == len(rows):
        raise ValueError("Adventure State entry not found")
    setattr(state, attr, filtered)
    _persist_edit(game, state)


def _creator(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    if not member.plan.has(GAME_CREATE):
        raise HTTPException(403, "Adventure State authoring unlocks on the Basic £4.99 tier")
    return member


def _game(game_id: str) -> GameDNA:
    try:
        game = load_game(game_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Game not found") from exc
    if not game.actively_editable:
        raise HTTPException(409, "Reopen this game before editing Adventure State DNA")
    return game


def _state_response(game: GameDNA) -> dict:
    state = ensure_adventure(game)
    return {
        "game_id": game.id,
        "adventure": state.model_dump(mode="json"),
        "runtime": adventure_runtime_payload(game),
        "integrity_bound": True,
        "server_save_sync": False,
        "arbitrary_script_source_allowed": False,
    }


@router.get("/api/game-forge/games/{game_id}/adventure")
def get_adventure(game_id: str, request: Request):
    _creator(request)
    return _state_response(_game(game_id))


@router.put("/api/game-forge/games/{game_id}/adventure")
def replace_adventure(game_id: str, body: AdventureStateDNA, request: Request):
    _creator(request)
    game = _game(game_id)
    if body.game_id != game.id:
        raise HTTPException(400, "Adventure State game_id does not match this game")
    current = ensure_adventure(game)
    if body.revision < current.revision:
        raise HTTPException(409, "Stale Adventure State revision")
    try:
        _persist_edit(game, body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _state_response(game)


@router.post("/api/game-forge/games/{game_id}/adventure/items")
def create_item(game_id: str, body: CreateItemRequest, request: Request):
    _creator(request)
    game = _game(game_id)
    try:
        row = add_item(game, body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"item": row.model_dump(mode="json"), **_state_response(game)}


@router.post("/api/game-forge/games/{game_id}/adventure/objectives")
def create_objective(game_id: str, body: CreateObjectiveRequest, request: Request):
    _creator(request)
    game = _game(game_id)
    try:
        row = add_objective(game, body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"objective": row.model_dump(mode="json"), **_state_response(game)}


@router.post("/api/game-forge/games/{game_id}/adventure/dialogues")
def create_dialogue(game_id: str, body: CreateDialogueRequest, request: Request):
    _creator(request)
    game = _game(game_id)
    try:
        row = add_dialogue(game, body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"dialogue": row.model_dump(mode="json"), **_state_response(game)}


@router.post("/api/game-forge/games/{game_id}/adventure/gates")
def create_gate(game_id: str, body: CreateGateRequest, request: Request):
    _creator(request)
    game = _game(game_id)
    try:
        row = add_gate(game, body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"gate": row.model_dump(mode="json"), **_state_response(game)}


@router.delete("/api/game-forge/games/{game_id}/adventure/{collection}/{row_id}")
def delete_adventure_entry(game_id: str, collection: str, row_id: str, request: Request):
    _creator(request)
    game = _game(game_id)
    try:
        delete_adventure_row(game, collection, row_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"deleted": True, "collection": collection, "row_id": row_id, **_state_response(game)}


__all__ = [
    "router",
    "AdventureStateDNA",
    "AdventureItemDNA",
    "ObjectiveDNA",
    "DialogueDNA",
    "DialogueChoiceDNA",
    "GateDNA",
    "CreateItemRequest",
    "CreateObjectiveRequest",
    "CreateDialogueRequest",
    "CreateGateRequest",
    "ensure_adventure",
    "load_adventure_optional",
    "save_adventure",
    "adventure_integrity_payload",
    "adventure_reference_blockers",
    "adventure_runtime_payload",
    "add_item",
    "add_objective",
    "add_dialogue",
    "add_gate",
    "delete_adventure_row",
]
