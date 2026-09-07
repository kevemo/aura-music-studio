from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .aura_effect_system_automation import (
    clear_effect_system_mix_automation,
    effect_system_mix_automation,
    restore_effect_system_automation_revision,
    set_effect_system_mix_automation,
)
from .aura_effect_system_member_library import (
    import_reusable_effect_system,
    list_reusable_effect_systems,
    load_reusable_effect_system,
    publish_project_effect_system,
    remove_reusable_effect_system,
)
from .aura_effect_system_creator import EffectNodeSpec, EffectSystemSpec, compile_effect_system, make_effect_system
from .aura_effect_system_recovery import (
    discard_effect_system_autosave,
    load_effect_system_autosave,
    save_effect_system_autosave,
)
from .tenant_storage import project_path, projects_root


class EffectAutomationPointRequest(BaseModel):
    time: float
    value: float


class EffectAutomationRequest(BaseModel):
    points: list[EffectAutomationPointRequest] = Field(default_factory=list, max_length=2000)
    interpolation: str = Field(default="linear", pattern=r"^(hold|linear|smooth)$")


class ReusableEffectSystemPublishRequest(BaseModel):
    item_id: str | None = Field(default=None, min_length=1, max_length=160)
    tags: list[str] = Field(default_factory=list, max_length=20)


class ReusableEffectSystemImportRequest(BaseModel):
    target_project: str = Field(min_length=1, max_length=120)


class EffectSystemRecoveryNodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=120)
    catalogue_item_id: str = Field(min_length=1, max_length=120)
    parameters: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    mix: float = Field(default=1.0, ge=0.0, le=1.0)


class EffectSystemRecoveryDefinitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=160)
    nodes: list[EffectSystemRecoveryNodeRequest] = Field(min_length=1, max_length=32)
    description: str = Field(default="", max_length=500)
    version: int = Field(default=1, ge=1, le=1_000_000)


class EffectSystemAutosaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    system: EffectSystemRecoveryDefinitionRequest
    source_prompt_fingerprint: str = Field(default="", max_length=64)


def _require_member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Membership context unavailable")
    plan = getattr(member, "plan", None)
    plan_id = getattr(plan, "id", None)
    if not isinstance(plan_id, str) or not plan_id.strip():
        raise HTTPException(401, "Membership plan context unavailable")
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


def _translate(exc: Exception) -> HTTPException:
    if isinstance(exc, FileNotFoundError):
        return HTTPException(404, "Effect system, recovery draft, reusable library item, track or revision not found")
    if isinstance(exc, PermissionError):
        return HTTPException(403, str(exc))
    if isinstance(exc, RuntimeError):
        return HTTPException(409, str(exc))
    return HTTPException(400, str(exc))


def _recovery_spec(body: EffectSystemRecoveryDefinitionRequest) -> EffectSystemSpec:
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


def autosave_member_effect_system(
    project_name: str,
    body: EffectSystemAutosaveRequest,
    request: Request,
):
    member, _user_id = _require_member(request)
    project = _project(project_name)
    spec = _recovery_spec(body.system)
    try:
        result = save_effect_system_autosave(
            project,
            spec,
            source_prompt_fingerprint=body.source_prompt_fingerprint,
        )
    except (OSError, TypeError, ValueError) as exc:
        raise _translate(exc) from exc
    return {
        **result,
        "project": project.name,
        "plan": member.plan.id,
        "recovery_draft": True,
        "canonical_save_requires_explicit_action": True,
        "entitlement_granted": False,
        "execution_authorized": False,
    }


def get_member_effect_system_autosave(project_name: str, system_id: str, request: Request):
    member, _user_id = _require_member(request)
    project = _project(project_name)
    try:
        result = load_effect_system_autosave(project, system_id)
    except (FileNotFoundError, KeyError, OSError, TypeError, ValueError) as exc:
        raise _translate(exc) from exc
    return {
        **result,
        "project": project.name,
        "plan": member.plan.id,
        "recovery_draft": True,
        "entitlement_granted": False,
        "execution_authorized": False,
    }


def discard_member_effect_system_autosave(project_name: str, system_id: str, request: Request):
    member, _user_id = _require_member(request)
    project = _project(project_name)
    try:
        result = discard_effect_system_autosave(project, system_id)
    except (FileNotFoundError, OSError, TypeError, ValueError) as exc:
        raise _translate(exc) from exc
    return {
        **result,
        "project": project.name,
        "plan": member.plan.id,
        "recovery_draft": False,
        "entitlement_granted": False,
        "execution_authorized": False,
    }


def get_member_effect_system_mix_automation(
    project_name: str,
    track_id: str,
    system_id: str,
    node_id: str,
    request: Request,
):
    member, _user_id = _require_member(request)
    project = _project(project_name)
    try:
        result = effect_system_mix_automation(project, track_id, system_id, node_id)
    except (FileNotFoundError, KeyError, OSError, TypeError, ValueError) as exc:
        raise _translate(exc) from exc
    return {**result, "plan": member.plan.id, "editable_keyframes": True}


def set_member_effect_system_mix_automation(
    project_name: str,
    track_id: str,
    system_id: str,
    node_id: str,
    body: EffectAutomationRequest,
    request: Request,
):
    member, _user_id = _require_member(request)
    project = _project(project_name)
    try:
        result = set_effect_system_mix_automation(
            project,
            track_id,
            system_id,
            node_id,
            [point.model_dump(mode="json") for point in body.points],
            interpolation=body.interpolation,
            actor="Aura Effect/System Creator",
        )
    except (FileNotFoundError, KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
        raise _translate(exc) from exc
    return {
        **result,
        "plan": member.plan.id,
        "editable_keyframes": True,
        "revision_backed": True,
    }


def clear_member_effect_system_mix_automation(
    project_name: str,
    track_id: str,
    system_id: str,
    node_id: str,
    request: Request,
):
    member, _user_id = _require_member(request)
    project = _project(project_name)
    try:
        result = clear_effect_system_mix_automation(
            project,
            track_id,
            system_id,
            node_id,
            actor="Aura Effect/System Creator",
        )
    except (FileNotFoundError, KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
        raise _translate(exc) from exc
    return {**result, "plan": member.plan.id, "editable_keyframes": True}


def restore_member_effect_system_automation_revision(
    project_name: str,
    revision_id: str,
    request: Request,
):
    member, _user_id = _require_member(request)
    project = _project(project_name)
    try:
        result = restore_effect_system_automation_revision(project, revision_id)
    except (FileNotFoundError, KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
        raise _translate(exc) from exc
    return {**result, "plan": member.plan.id, "restored_by": "Aura Effect/System Creator"}


def list_member_reusable_effect_systems(request: Request):
    member, _user_id = _require_member(request)
    try:
        rows = list_reusable_effect_systems(library_root=projects_root())
    except (OSError, TypeError, ValueError) as exc:
        raise _translate(exc) from exc
    return {
        "items": rows,
        "count": len(rows),
        "plan": member.plan.id,
        "visibility": "private",
        "marketplace_published": False,
        "sale_enabled": False,
    }


def publish_member_reusable_effect_system(
    project_name: str,
    system_id: str,
    body: ReusableEffectSystemPublishRequest,
    request: Request,
):
    member, _user_id = _require_member(request)
    project = _project(project_name)
    try:
        result = publish_project_effect_system(
            project,
            system_id,
            item_id=body.item_id,
            tags=body.tags,
            library_root=projects_root(),
        )
    except (FileNotFoundError, KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
        raise _translate(exc) from exc
    return {**result, "plan": member.plan.id, "reusable_library": True}


def get_member_reusable_effect_system(item_id: str, request: Request):
    member, _user_id = _require_member(request)
    try:
        spec = load_reusable_effect_system(item_id, library_root=projects_root())
        compiled = compile_effect_system(spec)
    except (FileNotFoundError, KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
        raise _translate(exc) from exc
    return {
        "item_id": item_id,
        "system": spec.public(),
        "fingerprint": compiled.fingerprint,
        "ffmpeg_filter_chain": compiled.ffmpeg_filter_chain,
        "backend_executable": True,
        "visibility": "private",
        "marketplace_published": False,
        "sale_enabled": False,
        "plan": member.plan.id,
    }


def import_member_reusable_effect_system(
    item_id: str,
    body: ReusableEffectSystemImportRequest,
    request: Request,
):
    member, _user_id = _require_member(request)
    target_project = _project(body.target_project)
    try:
        result = import_reusable_effect_system(target_project, item_id, library_root=projects_root())
    except (FileNotFoundError, KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
        raise _translate(exc) from exc
    return {**result, "plan": member.plan.id, "reusable_library": True}


def remove_member_reusable_effect_system(item_id: str, request: Request):
    member, _user_id = _require_member(request)
    try:
        result = remove_reusable_effect_system(item_id, library_root=projects_root())
    except (FileNotFoundError, KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
        raise _translate(exc) from exc
    return {**result, "plan": member.plan.id, "reusable_library": True}


def effect_system_extended_route_registrations(prefix: str) -> tuple[tuple[str, Any, str], ...]:
    base = f"{prefix}/effect-systems"
    automation = (
        f"{base}/projects/{{project_name}}/tracks/{{track_id}}/systems/{{system_id}}/nodes/{{node_id}}/mix-automation"
    )
    return (
        (f"{base}/projects/{{project_name}}/autosave", autosave_member_effect_system, "POST"),
        (f"{base}/projects/{{project_name}}/autosave/{{system_id}}", get_member_effect_system_autosave, "GET"),
        (f"{base}/projects/{{project_name}}/autosave/{{system_id}}", discard_member_effect_system_autosave, "DELETE"),
        (automation, get_member_effect_system_mix_automation, "GET"),
        (automation, set_member_effect_system_mix_automation, "PUT"),
        (automation, clear_member_effect_system_mix_automation, "DELETE"),
        (
            f"{base}/projects/{{project_name}}/automation/restore/{{revision_id}}",
            restore_member_effect_system_automation_revision,
            "POST",
        ),
        (f"{base}/library", list_member_reusable_effect_systems, "GET"),
        (
            f"{base}/projects/{{project_name}}/{{system_id}}/publish-private",
            publish_member_reusable_effect_system,
            "POST",
        ),
        (f"{base}/library/{{item_id}}", get_member_reusable_effect_system, "GET"),
        (f"{base}/library/{{item_id}}/import", import_member_reusable_effect_system, "POST"),
        (f"{base}/library/{{item_id}}", remove_member_reusable_effect_system, "DELETE"),
    )


__all__ = [
    "EffectAutomationPointRequest",
    "EffectAutomationRequest",
    "EffectSystemAutosaveRequest",
    "EffectSystemRecoveryDefinitionRequest",
    "EffectSystemRecoveryNodeRequest",
    "ReusableEffectSystemImportRequest",
    "ReusableEffectSystemPublishRequest",
    "autosave_member_effect_system",
    "discard_member_effect_system_autosave",
    "effect_system_extended_route_registrations",
    "get_member_effect_system_autosave",
]
