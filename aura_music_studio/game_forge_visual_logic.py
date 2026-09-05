from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .game_forge_gameplay import _safe_behavior as _safe_gameplay_behavior
from .game_forge_models import GameDNA
from .game_forge_store import game_dir, load_game, remove_public_snapshot, save_game
from .game_forge_world import BehaviorNodeDNA, GameWorldDNA, WorldEntityDNA, ensure_world, save_world
from .game_forge_world_logic import _safe_logic_behavior
from .plans import GAME_CREATE

router = APIRouter(tags=["Game Forge Visual Logic"])

VISUAL_LOGIC_SCHEMA = "game_forge_visual_logic.v1"
VISUAL_LOGIC_COMPILER = "aura_world_logic.v2"
VisualLogicOp = Literal[
    "follow_target",
    "timer",
    "door",
    "collectible",
    "damage",
    "checkpoint",
    "patrol",
    "quest_trigger",
]
_WORLD_LOGIC_OPS = {"follow_target", "timer", "door"}
_GAMEPLAY_OPS = {"collectible", "damage", "checkpoint", "patrol", "quest_trigger"}
_RUNTIME_OPS = _WORLD_LOGIC_OPS | _GAMEPLAY_OPS
_NODE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
_PROTECTED_ENTITY_IDS = {"player", "camera"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class VisualLogicNode(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    op: VisualLogicOp
    params: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    canvas_x: float = Field(default=0.0, ge=-100_000.0, le=100_000.0)
    canvas_y: float = Field(default=0.0, ge=-100_000.0, le=100_000.0)


class VisualLogicEdge(BaseModel):
    source: str = Field(min_length=1, max_length=80)
    target: str = Field(min_length=1, max_length=80)


class PutVisualLogicGraphRequest(BaseModel):
    nodes: list[VisualLogicNode] = Field(default_factory=list, max_length=40)
    edges: list[VisualLogicEdge] = Field(default_factory=list, max_length=120)
    expected_revision: int | None = Field(default=None, ge=1)


class VisualLogicGraph(BaseModel):
    schema_version: str = VISUAL_LOGIC_SCHEMA
    graph_id: str
    game_id: str
    entity_id: str
    revision: int = Field(default=1, ge=1)
    nodes: list[VisualLogicNode] = Field(default_factory=list, max_length=40)
    edges: list[VisualLogicEdge] = Field(default_factory=list, max_length=120)
    compiled_behavior_ids: list[str] = Field(default_factory=list, max_length=40)
    compiled_world_revision: int | None = None
    compiler: str = VISUAL_LOGIC_COMPILER
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)


class VisualLogicConflict(ValueError):
    pass


def _creator(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    if not member.plan.has(GAME_CREATE):
        raise HTTPException(403, "Visual Logic unlocks on the Basic £4.99 tier")
    return member


def _game(game_id: str) -> GameDNA:
    try:
        return load_game(game_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Game not found") from exc


def _require_editable(game: GameDNA) -> None:
    if not game.actively_editable:
        raise VisualLogicConflict("Reopen this game before changing its Visual Logic graph")


def _entity(world: GameWorldDNA, entity_id: str) -> WorldEntityDNA:
    row = next((item for item in world.entities if item.id == entity_id), None)
    if row is None:
        raise ValueError("World entity not found")
    if row.id in _PROTECTED_ENTITY_IDS:
        raise VisualLogicConflict("Core player/camera entities cannot be authored through Visual Logic")
    return row


def _graph_id(game_id: str, entity_id: str) -> str:
    digest = hashlib.sha256(f"{game_id}:{entity_id}".encode("utf-8")).hexdigest()[:24]
    return f"vlg_{digest}"


def _behavior_id(game_id: str, entity_id: str, node_id: str) -> str:
    digest = hashlib.sha256(f"{game_id}:{entity_id}:{node_id}".encode("utf-8")).hexdigest()[:28]
    return f"visual_{digest}"


def _graph_path(game_id: str, entity_id: str):
    digest = hashlib.sha256(entity_id.encode("utf-8")).hexdigest()[:32]
    return game_dir(game_id) / "visual_logic" / f"{digest}.json"


def load_visual_logic_graph(game_id: str, entity_id: str) -> VisualLogicGraph | None:
    path = _graph_path(game_id, entity_id)
    if not path.is_file():
        return None
    try:
        graph = VisualLogicGraph.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise VisualLogicConflict("Stored Visual Logic graph is invalid and requires repair") from exc
    if graph.game_id != game_id or graph.entity_id != entity_id or graph.graph_id != _graph_id(game_id, entity_id):
        raise VisualLogicConflict("Stored Visual Logic graph identity does not match this project entity")
    return graph


def _save_graph(graph: VisualLogicGraph) -> VisualLogicGraph:
    path = _graph_path(graph.game_id, graph.entity_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(graph.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return graph


def _invalidate(game: GameDNA) -> None:
    remove_public_snapshot(game)
    game.public_id = None
    game.rating_assessment = None
    game.latest_build = None
    game.status = "draft"
    game.touch()
    save_game(game)


def _ordered_node_ids(nodes: list[VisualLogicNode], edges: list[VisualLogicEdge]) -> list[str]:
    ids = [node.id for node in nodes]
    if any(not _NODE_ID_RE.fullmatch(node_id) for node_id in ids):
        raise ValueError("Visual Logic node IDs may contain only letters, numbers, underscores and hyphens")
    if len(ids) != len(set(ids)):
        raise ValueError("Visual Logic node IDs must be unique")

    known = set(ids)
    order_index = {node_id: index for index, node_id in enumerate(ids)}
    outgoing: dict[str, list[str]] = {node_id: [] for node_id in ids}
    indegree = {node_id: 0 for node_id in ids}
    seen_edges: set[tuple[str, str]] = set()
    for edge in edges:
        if edge.source not in known or edge.target not in known:
            raise ValueError("Visual Logic edges must connect nodes in this graph")
        if edge.source == edge.target:
            raise ValueError("Visual Logic nodes cannot connect to themselves")
        pair = (edge.source, edge.target)
        if pair in seen_edges:
            raise ValueError("Duplicate Visual Logic edges are not allowed")
        seen_edges.add(pair)
        outgoing[edge.source].append(edge.target)
        indegree[edge.target] += 1

    ready = [node_id for node_id in ids if indegree[node_id] == 0]
    ready.sort(key=order_index.__getitem__)
    ordered: list[str] = []
    while ready:
        node_id = ready.pop(0)
        ordered.append(node_id)
        for target in sorted(outgoing[node_id], key=order_index.__getitem__):
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
                ready.sort(key=order_index.__getitem__)
    if len(ordered) != len(ids):
        raise ValueError("Visual Logic graph contains a cycle; runtime behavior order must be acyclic")
    return ordered


def _sanitize_node(game_id: str, entity_id: str, node: VisualLogicNode) -> tuple[VisualLogicNode, BehaviorNodeDNA]:
    behavior_id = _behavior_id(game_id, entity_id, node.id)
    # Probe as enabled so disabled authoring nodes are normalized without becoming executable.
    # Each operation reuses the exact sanitizer already trusted by its production runtime path.
    probe = BehaviorNodeDNA(id=behavior_id, op=node.op, params=dict(node.params), enabled=True)
    if node.op in _WORLD_LOGIC_OPS:
        safe = _safe_logic_behavior(probe)
    elif node.op in _GAMEPLAY_OPS:
        safe = _safe_gameplay_behavior(probe)
    else:
        safe = None
    if safe is None:
        raise ValueError(f"Visual Logic operation '{node.op}' is not executable in the current Aura runtime")
    sanitized = VisualLogicNode(
        id=node.id,
        op=node.op,
        params=dict(safe["params"]),
        enabled=node.enabled,
        canvas_x=node.canvas_x,
        canvas_y=node.canvas_y,
    )
    behavior = BehaviorNodeDNA(
        id=behavior_id,
        op=node.op,
        params=dict(safe["params"]),
        enabled=node.enabled,
    )
    return sanitized, behavior


def _validate_runtime_requirements(world: GameWorldDNA, entity: WorldEntityDNA, behaviors: list[BehaviorNodeDNA]) -> None:
    world_ids = {row.id for row in world.entities}
    for behavior in behaviors:
        if not behavior.enabled:
            continue
        if behavior.op == "follow_target":
            target = str(behavior.params.get("target") or "player")
            if target not in world_ids:
                raise ValueError(f"Visual Logic follow target '{target}' does not exist in this world")
            if entity.physics is None or entity.physics.mode not in {"kinematic", "dynamic"}:
                raise ValueError("Visual Logic follow_target requires kinematic or dynamic Physics DNA on the entity")
        elif behavior.op == "door":
            if entity.physics is None or entity.physics.mode != "kinematic":
                raise ValueError("Visual Logic door requires kinematic Physics DNA on the entity")
        elif behavior.op in {"collectible", "damage", "checkpoint", "quest_trigger"}:
            if entity.physics is None:
                raise ValueError(f"Visual Logic {behavior.op} requires Physics DNA for collision behavior")
        elif behavior.op == "patrol":
            if entity.physics is None or entity.physics.mode not in {"kinematic", "dynamic"}:
                raise ValueError("Visual Logic patrol requires kinematic or dynamic Physics DNA on the entity")


def compile_visual_logic_graph(
    game: GameDNA,
    entity_id: str,
    body: PutVisualLogicGraphRequest,
) -> VisualLogicGraph:
    _require_editable(game)
    world = ensure_world(game)
    entity = _entity(world, entity_id)
    current = load_visual_logic_graph(game.id, entity.id)
    if body.expected_revision is not None:
        actual = current.revision if current else None
        if actual != body.expected_revision:
            raise VisualLogicConflict(
                f"Stale Visual Logic revision: expected {body.expected_revision}, current is {actual or 'none'}"
            )

    ordered_ids = _ordered_node_ids(body.nodes, body.edges)
    by_id = {node.id: node for node in body.nodes}
    sanitized_nodes: list[VisualLogicNode] = []
    compiled: list[BehaviorNodeDNA] = []
    for node_id in ordered_ids:
        clean_node, behavior = _sanitize_node(game.id, entity.id, by_id[node_id])
        sanitized_nodes.append(clean_node)
        compiled.append(behavior)
    _validate_runtime_requirements(world, entity, compiled)

    previous_ids = set(current.compiled_behavior_ids if current else [])
    retained = [row for row in entity.behaviors if row.id not in previous_ids]
    new_ids = {row.id for row in compiled}
    if any(row.id in new_ids for row in retained):
        raise VisualLogicConflict("Visual Logic behavior identity collides with a non-graph behavior")
    if len(retained) + len(compiled) > 40:
        raise ValueError("Compiled Visual Logic would exceed the entity behavior safety limit")

    entity.behaviors = [*retained, *compiled]
    world.touch()
    save_world(world)
    _invalidate(game)

    timestamp = _now()
    graph = VisualLogicGraph(
        graph_id=_graph_id(game.id, entity.id),
        game_id=game.id,
        entity_id=entity.id,
        revision=(current.revision + 1) if current else 1,
        nodes=sanitized_nodes,
        edges=list(body.edges),
        compiled_behavior_ids=[row.id for row in compiled],
        compiled_world_revision=world.revision,
        created_at=current.created_at if current else timestamp,
        updated_at=timestamp,
    )
    return _save_graph(graph)


def delete_visual_logic_graph(game: GameDNA, entity_id: str) -> bool:
    _require_editable(game)
    world = ensure_world(game)
    entity = _entity(world, entity_id)
    current = load_visual_logic_graph(game.id, entity.id)
    if current is None:
        return False
    owned = set(current.compiled_behavior_ids)
    before = len(entity.behaviors)
    entity.behaviors = [row for row in entity.behaviors if row.id not in owned]
    if len(entity.behaviors) != before:
        world.touch()
        save_world(world)
        _invalidate(game)
    path = _graph_path(game.id, entity.id)
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    return True


def visual_logic_capabilities(game_id: str) -> dict[str, Any]:
    return {
        "game_id": game_id,
        "schema_version": VISUAL_LOGIC_SCHEMA,
        "compiler": VISUAL_LOGIC_COMPILER,
        "runtime_ops": sorted(_RUNTIME_OPS),
        "world_logic_ops": sorted(_WORLD_LOGIC_OPS),
        "aura3d_gameplay_ops": sorted(_GAMEPLAY_OPS),
        "edge_semantics": "compile_order_only",
        "compiled_target": "WorldEntityDNA.behaviors",
        "arbitrary_script_source_allowed": False,
        "eval_allowed": False,
        "network_access_from_graph": False,
        "unknown_node_execution": False,
        "optimistic_concurrency": True,
        "build_invalidated_after_compile": True,
    }


def _graph_payload(graph: VisualLogicGraph) -> dict[str, Any]:
    return {
        "graph": graph.model_dump(mode="json"),
        "capabilities": visual_logic_capabilities(graph.game_id),
        "compiled": True,
        "sanitized_params_only": True,
    }


@router.get("/api/game-forge/games/{game_id}/visual-logic")
def get_visual_logic_capabilities(game_id: str, request: Request):
    _creator(request)
    _game(game_id)
    return visual_logic_capabilities(game_id)


@router.get("/api/game-forge/games/{game_id}/visual-logic/{entity_id}")
def get_visual_logic_graph(game_id: str, entity_id: str, request: Request):
    _creator(request)
    game = _game(game_id)
    world = ensure_world(game)
    _entity(world, entity_id)
    graph = load_visual_logic_graph(game.id, entity_id)
    if graph is None:
        return {
            "graph": None,
            "capabilities": visual_logic_capabilities(game.id),
            "compiled": False,
            "sanitized_params_only": True,
        }
    return _graph_payload(graph)


@router.put("/api/game-forge/games/{game_id}/visual-logic/{entity_id}")
def put_visual_logic_graph(game_id: str, entity_id: str, body: PutVisualLogicGraphRequest, request: Request):
    _creator(request)
    game = _game(game_id)
    try:
        graph = compile_visual_logic_graph(game, entity_id, body)
    except VisualLogicConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        **_graph_payload(graph),
        "invalidated_previous_build_and_rating": True,
        "manual_behaviors_preserved": True,
    }


@router.delete("/api/game-forge/games/{game_id}/visual-logic/{entity_id}")
def delete_visual_logic_graph_route(game_id: str, entity_id: str, request: Request):
    _creator(request)
    game = _game(game_id)
    try:
        deleted = delete_visual_logic_graph(game, entity_id)
    except VisualLogicConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "deleted": deleted,
        "game_id": game.id,
        "entity_id": entity_id,
        "compiled_behaviors_removed": deleted,
        "manual_behaviors_preserved": True,
    }


__all__ = [
    "VISUAL_LOGIC_SCHEMA",
    "VISUAL_LOGIC_COMPILER",
    "VisualLogicNode",
    "VisualLogicEdge",
    "PutVisualLogicGraphRequest",
    "VisualLogicGraph",
    "VisualLogicConflict",
    "compile_visual_logic_graph",
    "delete_visual_logic_graph",
    "load_visual_logic_graph",
    "visual_logic_capabilities",
    "router",
]
