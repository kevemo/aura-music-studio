from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .aura_effect_system_creator import EffectNodeSpec, EffectSystemSpec, compile_effect_system, make_effect_system
from .aura_effect_system_project import (
    apply_effect_system,
    list_saved_effect_systems,
    load_effect_system,
    preview_project_effect_system,
    restore_effect_system_revision,
    save_effect_system,
)
from .aura_effect_system_prompt import MAX_EFFECT_PROMPT_CHARS, compose_effect_system_from_prompt
from .creative_effect_entitlements import store as effect_entitlement_store
from .tenant_storage import project_path

router = APIRouter(
    prefix="/command-center/api/effect-systems",
    tags=["Aura Effect/System Creator"],
)


class EffectNodeInput(BaseModel):
    id: str = Field(min_length=1, max_length=120)
    catalogue_item_id: str = Field(min_length=1, max_length=120)
    parameters: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    mix: float = Field(default=1.0, ge=0.0, le=1.0)


class EffectSystemInput(BaseModel):
    id: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=500)
    version: int = Field(default=1, ge=1, le=1_000_000)
    nodes: list[EffectNodeInput] = Field(min_length=1, max_length=32)


class PromptComposeRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=MAX_EFFECT_PROMPT_CHARS)
    system_id: str | None = Field(default=None, max_length=120)
    name: str | None = Field(default=None, max_length=160)


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Membership context unavailable")
    user_id = str(getattr(member, "user_id", "") or "").strip()
    if not user_id:
        raise HTTPException(401, "Active member account required")
    return member, user_id


def _project(project_name: str):
    try:
        return project_path(project_name, must_exist=True)
    except ValueError as exc:
        raise HTTPException(400, "Invalid project path") from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, "Project not found") from exc


def _spec(body: EffectSystemInput) -> EffectSystemSpec:
    try:
        return make_effect_system(
            body.id,
            body.name,
            [
                EffectNodeSpec(
                    id=node.id,
                    catalogue_item_id=node.catalogue_item_id,
                    parameters=dict(node.parameters),
                    enabled=node.enabled,
                    mix=node.mix,
                )
                for node in body.nodes
            ],
            description=body.description,
            version=body.version,
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


def _entitlements(user_id: str, spec: EffectSystemSpec) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for node in spec.nodes:
        if node.catalogue_item_id in seen:
            continue
        seen.add(node.catalogue_item_id)
        try:
            rows.append(effect_entitlement_store.has_entitlement(user_id, node.catalogue_item_id))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    return rows


def _compiled_response(user_id: str, spec: EffectSystemSpec) -> dict[str, Any]:
    try:
        compiled = compile_effect_system(spec).public()
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    entitlements = _entitlements(user_id, spec)
    missing = [str(row.get("effect_id") or "") for row in entitlements if not bool(row.get("owned"))]
    return {
        **compiled,
        "editable_system": spec.public(),
        "entitlements": entitlements,
        "missing_entitlement_effect_ids": missing,
        "can_apply": not missing,
        "compile_is_non_mutating": True,
        "project_mutated": False,
        "arbitrary_command_execution": False,
    }


@router.get("/capabilities")
def effect_system_capabilities(request: Request):
    member, _ = _member(request)
    return {
        "plan": member.plan.id,
        "domain": "music",
        "runtime": "ffmpeg_audio",
        "prompt_composer": "bounded_catalogue_intent_v1",
        "prompt_to_executable_graph": True,
        "editable_typed_nodes": True,
        "compile_preview": True,
        "project_save": True,
        "versioned_project_save": True,
        "project_track_preview": True,
        "entitlement_checked_apply": True,
        "revision_backed_undo": True,
        "visual_node_editor": False,
        "keyframe_automation_editor": False,
        "marketplace_publish": False,
        "source_media_mutated_by_preview": False,
        "arbitrary_command_execution": False,
    }


@router.post("/compose")
def compose_effect_system(body: PromptComposeRequest, request: Request):
    _, user_id = _member(request)
    try:
        composed = compose_effect_system_from_prompt(
            body.prompt,
            system_id=body.system_id,
            name=body.name,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    system = EffectSystemInput.model_validate(composed["system"])
    spec = _spec(system)
    response = _compiled_response(user_id, spec)
    response.update(
        {
            "prompt_fingerprint": composed["prompt_fingerprint"],
            "prompt_length": composed["prompt_length"],
            "composer": composed["composer"],
            "preview_required_before_apply": True,
        }
    )
    return response


@router.post("/compile")
def compile_editable_effect_system(body: EffectSystemInput, request: Request):
    _, user_id = _member(request)
    return _compiled_response(user_id, _spec(body))


@router.get("/projects/{project_name}")
def saved_effect_systems(project_name: str, request: Request):
    _member(request)
    project = _project(project_name)
    return {
        "project": project.name,
        "items": list_saved_effect_systems(project),
        "project_scoped": True,
        "source_media_mutated": False,
    }


@router.get("/projects/{project_name}/{system_id}")
def saved_effect_system(project_name: str, system_id: str, request: Request):
    _, user_id = _member(request)
    project = _project(project_name)
    try:
        spec = load_effect_system(project, system_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, "Saved effect system not found") from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"project": project.name, **_compiled_response(user_id, spec), "saved": True}


@router.put("/projects/{project_name}/{system_id}")
def save_editable_effect_system(project_name: str, system_id: str, body: EffectSystemInput, request: Request):
    _member(request)
    if system_id != body.id:
        raise HTTPException(409, "Path system id must match the editable system id")
    project = _project(project_name)
    spec = _spec(body)
    try:
        saved = save_effect_system(project, spec)
    except (TypeError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc
    return {
        **saved,
        "editable_system": spec.public(),
        "save_does_not_grant_entitlement": True,
        "source_media_mutated": False,
    }


@router.post("/projects/{project_name}/preview/{track_id}")
def preview_editable_effect_system(
    project_name: str,
    track_id: str,
    body: EffectSystemInput,
    request: Request,
):
    _, user_id = _member(request)
    project = _project(project_name)
    spec = _spec(body)
    try:
        return preview_project_effect_system(project, track_id, spec, user_id=user_id)
    except KeyError as exc:
        raise HTTPException(404, "Track not found") from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/projects/{project_name}/{system_id}/apply/{track_id}")
def apply_saved_effect_system(
    project_name: str,
    system_id: str,
    track_id: str,
    request: Request,
):
    _, user_id = _member(request)
    project = _project(project_name)
    try:
        spec = load_effect_system(project, system_id)
        return apply_effect_system(project, track_id, spec, user_id=user_id, actor=f"member:{user_id}")
    except FileNotFoundError as exc:
        raise HTTPException(404, "Saved effect system not found") from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, "Track not found") from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/projects/{project_name}/restore/{revision_id}")
def restore_effect_system_apply(project_name: str, revision_id: str, request: Request):
    _member(request)
    project = _project(project_name)
    try:
        return restore_effect_system_revision(project, revision_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, "Revision not found") from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


__all__ = [
    "EffectNodeInput",
    "EffectSystemInput",
    "PromptComposeRequest",
    "apply_saved_effect_system",
    "compile_editable_effect_system",
    "compose_effect_system",
    "effect_system_capabilities",
    "preview_editable_effect_system",
    "restore_effect_system_apply",
    "router",
    "save_editable_effect_system",
    "saved_effect_system",
    "saved_effect_systems",
]
