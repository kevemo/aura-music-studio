from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field

from .aura_effect_system_creator import EffectNodeSpec, EffectSystemSpec, compile_effect_system, make_effect_system
from .aura_effect_system_portal import effect_system_creator_page
from .aura_effect_system_preview_tokens import (
    consume_effect_system_preview_token,
    issue_effect_system_preview_token,
)
from .aura_effect_system_project import (
    apply_effect_system,
    list_saved_effect_systems,
    load_effect_system,
    preview_project_effect_system,
    restore_effect_system_revision,
    save_effect_system,
)
from .aura_effect_system_prompt import compose_effect_system_from_prompt
from .creative_catalogue import public_catalogue
from .creative_effect_entitlements import PUBLIC_COIN_UNIT, store as effect_entitlement_store
from .tenant_storage import project_path


class EffectSystemNodeRequest(BaseModel):
    id: str = Field(min_length=1, max_length=120)
    catalogue_item_id: str = Field(min_length=1, max_length=120)
    parameters: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    mix: float = Field(default=1.0, ge=0.0, le=1.0)


class EffectSystemDefinitionRequest(BaseModel):
    id: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=160)
    nodes: list[EffectSystemNodeRequest] = Field(min_length=1, max_length=32)
    description: str = Field(default="", max_length=500)
    version: int = Field(default=1, ge=1, le=1_000_000)


class EffectSystemPromptRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=1200)
    system_id: str | None = Field(default=None, min_length=1, max_length=120)
    name: str | None = Field(default=None, min_length=1, max_length=160)


class EffectSystemGraphRequest(BaseModel):
    system: EffectSystemDefinitionRequest
    source_prompt_fingerprint: str = Field(default="", max_length=64)


class EffectSystemApplyRequest(EffectSystemGraphRequest):
    # Compatibility name retained for the existing creator client. This field now carries an
    # opaque one-time server-issued preview proof rather than a client-computable graph digest.
    expected_fingerprint: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-fA-F]{64}$")


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


def _project(project_name: str) -> Path:
    try:
        return project_path(project_name, must_exist=True)
    except ValueError as exc:
        raise HTTPException(400, "Invalid project path") from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, "Project not found") from exc


def _spec(body: EffectSystemDefinitionRequest) -> EffectSystemSpec:
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


def _http_project_error(exc: Exception) -> HTTPException:
    if isinstance(exc, FileNotFoundError):
        return HTTPException(404, "Saved effect system, track or revision not found")
    if isinstance(exc, KeyError):
        return HTTPException(404, "Track or revision not found")
    if isinstance(exc, PermissionError):
        return HTTPException(403, str(exc))
    if isinstance(exc, RuntimeError):
        return HTTPException(409, str(exc))
    return HTTPException(400, str(exc))


def list_member_effect_catalogue(
    request: Request,
    query: str = "",
    studio: str = "music",
    limit: int = 50,
):
    member, _user_id = _require_member(request)
    query = str(query or "").strip()
    studio = str(studio or "").strip().casefold()
    if len(query) > 160:
        raise HTTPException(400, "Catalogue search query is too long")
    if not studio or len(studio) > 40 or not studio.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(400, "Invalid catalogue studio filter")
    if limit < 1 or limit > 100:
        raise HTTPException(400, "Catalogue result limit must be between 1 and 100")
    rows = public_catalogue(query, studio=studio)
    visible = rows[:limit]
    return {
        "items": visible,
        "count": len(visible),
        "total_matches": len(rows),
        "query": query,
        "studio": studio,
        "limit": limit,
        "plan": member.plan.id,
        "public_metadata_only": True,
        "project_mutated": False,
        "entitlement_granted": False,
        "execution_authorized": False,
    }


def compose_member_effect_system(body: EffectSystemPromptRequest, request: Request):
    member, user_id = _require_member(request)
    try:
        result = compose_effect_system_from_prompt(
            body.prompt,
            system_id=body.system_id,
            name=body.name,
        )
        entitlements: list[dict[str, Any]] = []
        seen: set[str] = set()
        for node in result["system"]["nodes"]:
            effect_id = str(node["catalogue_item_id"])
            if effect_id in seen:
                continue
            seen.add(effect_id)
            entitlements.append(effect_entitlement_store.has_entitlement(user_id, effect_id))
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    missing = [row for row in entitlements if not bool(row.get("owned"))]
    return {
        **result,
        "entitlements": entitlements,
        "missing_entitlement_effect_ids": [str(row.get("effect_id") or "") for row in missing],
        "can_apply": not missing,
        "editable_graph": True,
        "plan": member.plan.id,
        "coin_unit": PUBLIC_COIN_UNIT,
        "project_mutated": False,
    }


def list_member_effect_systems(project_name: str, request: Request):
    member, _user_id = _require_member(request)
    project = _project(project_name)
    try:
        rows = list_saved_effect_systems(project)
    except (OSError, TypeError, ValueError) as exc:
        raise _http_project_error(exc) from exc
    return {
        "project": project.name,
        "items": rows,
        "count": len(rows),
        "plan": member.plan.id,
        "editable_graph": True,
        "source_media_mutated": False,
    }


def get_member_effect_system(project_name: str, system_id: str, request: Request):
    member, _user_id = _require_member(request)
    project = _project(project_name)
    try:
        spec = load_effect_system(project, system_id)
        compiled = compile_effect_system(spec)
        rows = list_saved_effect_systems(project)
        saved = next((row for row in rows if row.get("id") == spec.id), None)
    except (FileNotFoundError, KeyError, OSError, TypeError, ValueError) as exc:
        raise _http_project_error(exc) from exc
    return {
        "project": project.name,
        "system": spec.public(),
        "fingerprint": compiled.fingerprint,
        "source_prompt_fingerprint": (saved or {}).get("source_prompt_fingerprint"),
        "ffmpeg_filter_chain": compiled.ffmpeg_filter_chain,
        "effects": [effect.model_dump(mode="json") for effect in compiled.effects],
        "plan": member.plan.id,
        "editable_graph": True,
        "backend_executable": True,
    }


def save_member_effect_system(project_name: str, body: EffectSystemGraphRequest, request: Request):
    member, _user_id = _require_member(request)
    project = _project(project_name)
    spec = _spec(body.system)
    try:
        result = save_effect_system(
            project,
            spec,
            source_prompt_fingerprint=body.source_prompt_fingerprint,
        )
    except (OSError, TypeError, ValueError) as exc:
        raise _http_project_error(exc) from exc
    return {
        **result,
        "plan": member.plan.id,
        "editable_graph": True,
        "reuse_available": True,
    }


def preview_member_effect_system(
    project_name: str,
    track_id: str,
    body: EffectSystemGraphRequest,
    request: Request,
):
    member, user_id = _require_member(request)
    project = _project(project_name)
    spec = _spec(body.system)
    try:
        result = preview_project_effect_system(
            project,
            track_id,
            spec,
            user_id=user_id,
            source_prompt_fingerprint=body.source_prompt_fingerprint,
            entitlement_store=effect_entitlement_store,
        )
        proof = issue_effect_system_preview_token(
            project,
            user_id=user_id,
            track_id=str(result["track_id"]),
            fingerprint=str(result["fingerprint"]),
        )
    except (FileNotFoundError, KeyError, OSError, PermissionError, RuntimeError, TypeError, ValueError) as exc:
        raise _http_project_error(exc) from exc
    return {
        **result,
        "plan": member.plan.id,
        "editable_graph": True,
        "coin_unit": PUBLIC_COIN_UNIT,
        "preview_token": proof["token"],
        "preview_token_expires_in_seconds": proof["expires_in_seconds"],
        "preview_token_one_time": True,
        "preview_token_server_authoritative": True,
        "preview_evidence_persisted": True,
        "apply_requires_matching_preview_token": True,
    }


def apply_member_effect_system(
    project_name: str,
    track_id: str,
    body: EffectSystemApplyRequest,
    request: Request,
):
    member, user_id = _require_member(request)
    project = _project(project_name)
    spec = _spec(body.system)
    try:
        compiled = compile_effect_system(spec)
        consume_effect_system_preview_token(
            project,
            body.expected_fingerprint,
            user_id=user_id,
            track_id=track_id,
            fingerprint=compiled.fingerprint,
        )
    except (OSError, PermissionError, RuntimeError, TypeError, ValueError) as exc:
        raise _http_project_error(exc) from exc
    try:
        result = apply_effect_system(
            project,
            track_id,
            spec,
            user_id=user_id,
            actor="Aura Effect/System Creator",
            source_prompt_fingerprint=body.source_prompt_fingerprint,
            entitlement_store=effect_entitlement_store,
        )
    except (FileNotFoundError, KeyError, OSError, PermissionError, RuntimeError, TypeError, ValueError) as exc:
        raise _http_project_error(exc) from exc
    return {
        **result,
        "plan": member.plan.id,
        "coin_unit": PUBLIC_COIN_UNIT,
        "preview_token_verified": True,
        "preview_token_consumed": True,
        "preview_token_server_authoritative": True,
        "entitlements_rechecked_at_apply": True,
    }


def restore_member_effect_system_revision(project_name: str, revision_id: str, request: Request):
    member, _user_id = _require_member(request)
    project = _project(project_name)
    try:
        result = restore_effect_system_revision(project, revision_id)
    except (FileNotFoundError, KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
        raise _http_project_error(exc) from exc
    return {
        **result,
        "plan": member.plan.id,
        "restored_by": "Aura Effect/System Creator",
    }


def effect_system_route_registrations(prefix: str) -> tuple[tuple[str, Any, str], ...]:
    base = f"{prefix}/effect-systems"
    return (
        ("/creative/effect-system-creator", effect_system_creator_page, "GET"),
        (f"{base}/catalogue", list_member_effect_catalogue, "GET"),
        (f"{base}/compose", compose_member_effect_system, "POST"),
        (f"{base}/projects/{{project_name}}", list_member_effect_systems, "GET"),
        (f"{base}/projects/{{project_name}}/save", save_member_effect_system, "POST"),
        (f"{base}/projects/{{project_name}}/tracks/{{track_id}}/preview", preview_member_effect_system, "POST"),
        (f"{base}/projects/{{project_name}}/tracks/{{track_id}}/apply", apply_member_effect_system, "POST"),
        (f"{base}/projects/{{project_name}}/restore/{{revision_id}}", restore_member_effect_system_revision, "POST"),
        (f"{base}/projects/{{project_name}}/{{system_id}}", get_member_effect_system, "GET"),
    )


__all__ = [
    "EffectSystemApplyRequest",
    "EffectSystemDefinitionRequest",
    "EffectSystemGraphRequest",
    "EffectSystemNodeRequest",
    "EffectSystemPromptRequest",
    "apply_member_effect_system",
    "compose_member_effect_system",
    "effect_system_route_registrations",
    "get_member_effect_system",
    "list_member_effect_catalogue",
    "list_member_effect_systems",
    "preview_member_effect_system",
    "restore_member_effect_system_revision",
    "save_member_effect_system",
]
