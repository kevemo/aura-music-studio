from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .creative_project import (
    CreativeDirective,
    CreativeElement,
    CreativeKind,
    CreativeProjectStore,
    CreativeReference,
    DirectiveOperation,
    ElementStatus,
    InputMode,
    public_capabilities,
)
from .plans import DEEP_REVISION_HISTORY, REVISION_HISTORY
from .revisions import create_revision
from .tenant_storage import list_project_dirs, project_path

router = APIRouter(prefix="/creative", tags=["creative-projects"])


class InitializeCreativeProjectRequest(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    project_intent: str = Field(default="", max_length=4000)
    metadata: dict = Field(default_factory=dict)


class CreateElementRequest(BaseModel):
    kind: CreativeKind
    label: str = Field(min_length=1, max_length=200)
    role: str = Field(default="", max_length=120)
    status: ElementStatus = "draft"
    source_type: Literal["generated", "uploaded", "recorded", "reference", "derived", "legacy"] = "generated"
    source_ref: str | None = Field(default=None, max_length=1000)
    parent_ids: list[str] = Field(default_factory=list, max_length=100)
    prompt: str = Field(default="", max_length=6000)
    metadata: dict = Field(default_factory=dict)


class UpdateElementRequest(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=200)
    role: str | None = Field(default=None, max_length=120)
    status: ElementStatus | None = None
    source_ref: str | None = Field(default=None, max_length=1000)
    prompt: str | None = Field(default=None, max_length=6000)
    metadata: dict | None = None


class CreateReferenceRequest(BaseModel):
    kind: CreativeKind
    label: str = Field(min_length=1, max_length=200)
    source_ref: str = Field(min_length=1, max_length=1000)
    usage: str = Field(default="creative reference", max_length=500)
    rights_confirmed: bool = False
    metadata: dict = Field(default_factory=dict)


class CreateDirectiveRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=6000)
    input_mode: InputMode = "text"
    operation: DirectiveOperation = "revise"
    target_kind: CreativeKind | None = None
    target_element_ids: list[str] = Field(default_factory=list, max_length=100)
    reference_ids: list[str] = Field(default_factory=list, max_length=100)
    preserve_element_ids: list[str] = Field(default_factory=list, max_length=100)
    metadata: dict = Field(default_factory=dict)


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Membership context unavailable")
    return member


def _project(project_name: str, *, create: bool = False) -> Path:
    try:
        target = project_path(project_name, must_exist=not create)
    except ValueError as exc:
        raise HTTPException(400, "Invalid project path") from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, "Project not found") from exc
    if create:
        target.mkdir(parents=True, exist_ok=True)
    return target


def _store(project_name: str, *, create: bool = False) -> CreativeProjectStore:
    return CreativeProjectStore(_project(project_name, create=create))


def _manifest(store: CreativeProjectStore):
    try:
        return store.load()
    except FileNotFoundError as exc:
        raise HTTPException(
            404,
            "Creative manifest not initialized for this project. Initialize it before adding cross-media elements.",
        ) from exc


def _snapshot(member, store: CreativeProjectStore, *, label: str, reason: str) -> dict | None:
    """Create a cheap metadata-only undo point when the member owns revision history."""
    if not member.plan.has(REVISION_HISTORY) or not store.exists():
        return None
    keep = 200 if member.plan.has(DEEP_REVISION_HISTORY) else 20
    try:
        return create_revision(
            store.project_dir,
            label=label,
            reason=reason,
            actor="Aura Creative House",
            keep=keep,
        )
    except Exception:
        # A failed optional snapshot must not corrupt or block the primary creative edit.
        return None


@router.get("/capabilities")
def capabilities(request: Request):
    member = _member(request)
    return {"plan": member.plan.id, "capabilities": public_capabilities()}


@router.get("/projects")
def projects(request: Request):
    _member(request)
    rows: list[dict] = []
    for directory in list_project_dirs():
        store = CreativeProjectStore(directory)
        if store.exists():
            manifest = store.load()
            rows.append({
                "project_name": manifest.project_name,
                "title": manifest.title,
                "creative_manifest": True,
                "elements": len(manifest.elements),
                "references": len(manifest.references),
                "directives": len(manifest.directives),
                "updated_at": manifest.updated_at,
            })
        else:
            rows.append({
                "project_name": directory.name,
                "title": directory.name,
                "creative_manifest": False,
                "elements": 0,
                "references": 0,
                "directives": 0,
                "updated_at": None,
            })
    return {"projects": rows}


@router.post("/projects/{project_name}/initialize")
def initialize_project(project_name: str, body: InitializeCreativeProjectRequest, request: Request):
    _member(request)
    store = _store(project_name, create=True)
    manifest = store.initialize(
        project_name=project_name,
        title=body.title,
        project_intent=body.project_intent,
        metadata=body.metadata,
    )
    return {"manifest": manifest.model_dump(mode="json"), "capabilities": public_capabilities()}


@router.get("/projects/{project_name}/manifest")
def get_manifest(project_name: str, request: Request):
    _member(request)
    manifest = _manifest(_store(project_name))
    return {"manifest": manifest.model_dump(mode="json"), "capabilities": public_capabilities()}


@router.post("/projects/{project_name}/elements")
def add_element(project_name: str, body: CreateElementRequest, request: Request):
    member = _member(request)
    store = _store(project_name)
    _manifest(store)
    try:
        element = CreativeElement(
            kind=body.kind,
            label=body.label,
            role=body.role,
            status=body.status,
            source_type=body.source_type,
            source_ref=body.source_ref,
            parent_ids=body.parent_ids,
            prompt=body.prompt,
            metadata=body.metadata,
        )
        revision = _snapshot(member, store, label=f"Before adding {body.label}", reason="creative_element_add")
        manifest = store.add_element(element)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "element": element.model_dump(mode="json"),
        "manifest": manifest.model_dump(mode="json"),
        "revision_snapshot": revision,
    }


@router.patch("/projects/{project_name}/elements/{element_id}")
def update_element(
    project_name: str,
    element_id: str,
    body: UpdateElementRequest,
    request: Request,
):
    member = _member(request)
    store = _store(project_name)
    _manifest(store)
    revision = _snapshot(member, store, label="Before creative element edit", reason="creative_element_edit")
    try:
        manifest = store.update_element(
            element_id,
            label=body.label,
            role=body.role,
            status=body.status,
            source_ref=body.source_ref,
            prompt=body.prompt,
            metadata=body.metadata,
        )
    except KeyError as exc:
        raise HTTPException(404, "Creative element not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    element = next(item for item in manifest.elements if item.id == element_id)
    return {
        "element": element.model_dump(mode="json"),
        "manifest": manifest.model_dump(mode="json"),
        "revision_snapshot": revision,
    }


@router.post("/projects/{project_name}/references")
def add_reference(project_name: str, body: CreateReferenceRequest, request: Request):
    member = _member(request)
    if not body.rights_confirmed:
        raise HTTPException(400, "Confirm that you have the right or authorization to use this reference")
    store = _store(project_name)
    _manifest(store)
    try:
        reference = CreativeReference(
            kind=body.kind,
            label=body.label,
            source_ref=body.source_ref,
            usage=body.usage,
            rights_confirmed=body.rights_confirmed,
            metadata=body.metadata,
        )
        revision = _snapshot(member, store, label=f"Before attaching {body.label}", reason="creative_reference_add")
        manifest = store.add_reference(reference)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "reference": reference.model_dump(mode="json"),
        "manifest": manifest.model_dump(mode="json"),
        "revision_snapshot": revision,
    }


@router.post("/projects/{project_name}/directives")
def add_directive(project_name: str, body: CreateDirectiveRequest, request: Request):
    member = _member(request)
    store = _store(project_name)
    _manifest(store)
    directive = CreativeDirective(
        instruction=body.instruction,
        input_mode=body.input_mode,
        operation=body.operation,
        target_kind=body.target_kind,
        target_element_ids=body.target_element_ids,
        reference_ids=body.reference_ids,
        preserve_element_ids=body.preserve_element_ids,
        metadata=body.metadata,
    )
    revision = _snapshot(member, store, label="Before Aura directive", reason="creative_directive_add")
    try:
        manifest = store.add_directive(directive)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "directive": directive.model_dump(mode="json"),
        "manifest": manifest.model_dump(mode="json"),
        "revision_snapshot": revision,
        "truthful_execution_state": {
            "status": directive.status,
            "capability_state": directive.capability_state,
            "renderer_route": directive.renderer_route,
            "note": (
                "The directive is ready for a connected renderer."
                if directive.status == "ready_for_renderer"
                else "The directive is safely stored as editable project intent until the required renderer adapter is connected."
            ),
        },
    }
