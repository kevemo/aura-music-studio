from __future__ import annotations

import re
import secrets
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
from .creative_renderers import renderer_for, renderer_states
from .plans import DEEP_REVISION_HISTORY, REVISION_HISTORY
from .revisions import create_revision
from .tenant_storage import list_project_dirs, project_path

router = APIRouter(prefix="/creative", tags=["creative-projects"])

_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".avif", ".gif"}
_VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"}
_AUDIO_EXTENSIONS = {".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg"}


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


class QueueRendererRequest(BaseModel):
    negative_prompt: str = Field(default="", max_length=4000)
    seed: int | None = Field(default=None, ge=0, le=2**63 - 1)
    width: int = Field(default=1024, ge=64, le=4096)
    height: int = Field(default=1024, ge=64, le=4096)
    frames: int = Field(default=121, ge=1, le=10000)
    fps: float = Field(default=24.0, ge=1.0, le=120.0)
    variables: dict = Field(default_factory=dict)


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


def _directive(manifest, directive_id: str):
    directive = next((item for item in manifest.directives if item.id == directive_id), None)
    if directive is None:
        raise HTTPException(404, "Aura directive not found")
    return directive


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


def _public_renderer_states(*, probe: bool) -> dict:
    states = renderer_states(probe=probe)
    for value in states.values():
        # Never expose operator/internal network addresses to ordinary members.
        value.pop("base_url", None)
    return states


def _safe_output_name(filename: str, index: int) -> str:
    name = Path(filename).name
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")[:180]
    if not stem:
        stem = f"creative_output_{index:02d}"
    return f"{index:02d}_{stem}"


def _media_kind(filename: str, fallback: CreativeKind) -> CreativeKind:
    suffix = Path(filename).suffix.lower()
    if suffix in _IMAGE_EXTENSIONS:
        return "image"
    if suffix in _VIDEO_EXTENSIONS:
        return "video"
    if suffix in _AUDIO_EXTENSIONS:
        return "audio"
    return fallback


def _execution_state(history: dict, prompt_id: str, outputs: list) -> tuple[str, str | None]:
    entry = history.get(prompt_id)
    if not isinstance(entry, dict):
        return "queued", None
    status = entry.get("status")
    status_text = ""
    if isinstance(status, dict):
        status_text = str(status.get("status_str") or status.get("status") or "").lower()
    if "error" in status_text or "fail" in status_text:
        return "failed", status_text
    if outputs:
        return "completed", None
    return "running", None


@router.get("/capabilities")
def capabilities(request: Request):
    member = _member(request)
    return {
        "plan": member.plan.id,
        "capabilities": public_capabilities(),
        "creative_renderers": _public_renderer_states(probe=False),
    }


@router.get("/renderers")
def creative_renderers(request: Request, probe: bool = False):
    member = _member(request)
    return {"plan": member.plan.id, "renderers": _public_renderer_states(probe=probe)}


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
    return {
        "manifest": manifest.model_dump(mode="json"),
        "capabilities": public_capabilities(),
        "creative_renderers": _public_renderer_states(probe=False),
    }


@router.get("/projects/{project_name}/manifest")
def get_manifest(project_name: str, request: Request):
    _member(request)
    manifest = _manifest(_store(project_name))
    return {
        "manifest": manifest.model_dump(mode="json"),
        "capabilities": public_capabilities(),
        "creative_renderers": _public_renderer_states(probe=False),
    }


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


@router.post("/projects/{project_name}/directives/{directive_id}/render")
def queue_creative_render(
    project_name: str,
    directive_id: str,
    body: QueueRendererRequest,
    request: Request,
):
    member = _member(request)
    store = _store(project_name)
    manifest = _manifest(store)
    directive = _directive(manifest, directive_id)
    if directive.target_kind not in {"image", "video"}:
        raise HTTPException(400, "This renderer bridge currently accepts image or video Aura directives")

    renderer = renderer_for(directive.target_kind)
    if not renderer.configured:
        raise HTTPException(
            503,
            f"The {directive.target_kind} renderer adapter is installed but not configured on this deployment",
        )

    seed = body.seed if body.seed is not None else secrets.randbelow(2**31 - 1)
    variables = dict(body.variables)
    variables.update({
        "prompt": directive.instruction,
        "negative_prompt": body.negative_prompt,
        "seed": seed,
        "width": body.width,
        "height": body.height,
        "frames": body.frames,
        "fps": body.fps,
        "project_name": manifest.project_name,
        "project_title": manifest.title,
        "directive_id": directive.id,
        "operation": directive.operation,
    })

    revision = _snapshot(member, store, label="Before creative render queue", reason="creative_render_queue")
    try:
        submission = renderer.submit(variables)
    except Exception as exc:
        raise HTTPException(502, f"Creative renderer submission failed: {type(exc).__name__}: {exc}") from exc

    manifest = store.update_directive(
        directive.id,
        status="queued",
        capability_state="connected",
        renderer_route=f"comfyui:{submission.workflow_name}",
        metadata={
            "creative_renderer": {
                "provider": submission.provider,
                "kind": submission.kind,
                "prompt_id": submission.prompt_id,
                "client_id": submission.client_id,
                "workflow_name": submission.workflow_name,
                "seed": seed,
                "width": body.width,
                "height": body.height,
                "frames": body.frames,
                "fps": body.fps,
            }
        },
    )
    directive = _directive(manifest, directive.id)
    return {
        "directive": directive.model_dump(mode="json"),
        "submission": submission.model_dump(mode="json"),
        "revision_snapshot": revision,
        "note": "Renderer accepted the Aura directive. Poll render-status or sync outputs when complete.",
    }


@router.get("/projects/{project_name}/directives/{directive_id}/render-status")
def creative_render_status(project_name: str, directive_id: str, request: Request):
    _member(request)
    store = _store(project_name)
    manifest = _manifest(store)
    directive = _directive(manifest, directive_id)
    if directive.target_kind not in {"image", "video"}:
        raise HTTPException(400, "This directive is not assigned to an image/video renderer")
    render_meta = directive.metadata.get("creative_renderer")
    if not isinstance(render_meta, dict) or not render_meta.get("prompt_id"):
        raise HTTPException(409, "This directive has not been submitted to a creative renderer")

    renderer = renderer_for(directive.target_kind)
    prompt_id = str(render_meta["prompt_id"])
    try:
        history = renderer.history(prompt_id)
        outputs = renderer.collect_outputs(history, prompt_id)
        status, error = _execution_state(history, prompt_id, outputs)
    except Exception as exc:
        raise HTTPException(502, f"Unable to read creative renderer status: {type(exc).__name__}: {exc}") from exc

    metadata = {"creative_renderer": {**render_meta, "output_count": len(outputs)}}
    if error:
        metadata["creative_renderer"]["error"] = error
    if directive.status != status or metadata != {"creative_renderer": render_meta}:
        manifest = store.update_directive(
            directive.id,
            status=status,
            metadata=metadata,
        )
        directive = _directive(manifest, directive.id)
    return {
        "directive": directive.model_dump(mode="json"),
        "renderer_status": status,
        "outputs": [item.model_dump(mode="json") for item in outputs],
    }


@router.post("/projects/{project_name}/directives/{directive_id}/cancel-render")
def cancel_creative_render(project_name: str, directive_id: str, request: Request):
    member = _member(request)
    store = _store(project_name)
    manifest = _manifest(store)
    directive = _directive(manifest, directive_id)
    if directive.target_kind not in {"image", "video"}:
        raise HTTPException(400, "This directive is not assigned to an image/video renderer")
    render_meta = directive.metadata.get("creative_renderer")
    if not isinstance(render_meta, dict) or not render_meta.get("prompt_id"):
        raise HTTPException(409, "This directive has not been submitted to a creative renderer")
    if directive.status not in {"queued", "running"}:
        raise HTTPException(409, f"Creative render cannot be cancelled from status: {directive.status}")

    renderer = renderer_for(directive.target_kind)
    prompt_id = str(render_meta["prompt_id"])
    revision = _snapshot(member, store, label="Before cancelling creative render", reason="creative_render_cancel")
    try:
        cancellation = renderer.cancel(prompt_id)
    except Exception as exc:
        raise HTTPException(502, f"Creative renderer cancellation failed: {type(exc).__name__}: {exc}") from exc

    if cancellation.state == "not_queued":
        try:
            history = renderer.history(prompt_id)
            outputs = renderer.collect_outputs(history, prompt_id)
            status, error = _execution_state(history, prompt_id, outputs)
        except Exception as exc:
            raise HTTPException(502, f"Unable to reconcile creative renderer state: {type(exc).__name__}: {exc}") from exc
        if status in {"completed", "failed"}:
            metadata = {"creative_renderer": {**render_meta, "output_count": len(outputs)}}
            if error:
                metadata["creative_renderer"]["error"] = error
            manifest = store.update_directive(directive.id, status=status, metadata=metadata)
            directive = _directive(manifest, directive.id)
            return {
                "directive": directive.model_dump(mode="json"),
                "cancellation": cancellation.model_dump(mode="json"),
                "revision_snapshot": revision,
                "note": (
                    "The render had already completed before cancellation; its outputs remain available."
                    if status == "completed"
                    else "The renderer had already failed before cancellation."
                ),
            }

    manifest = store.update_directive(
        directive.id,
        status="ready_for_renderer",
        metadata={
            "creative_renderer": {
                **render_meta,
                "cancelled": True,
                "cancellation_state": cancellation.state,
            }
        },
    )
    directive = _directive(manifest, directive.id)
    return {
        "directive": directive.model_dump(mode="json"),
        "cancellation": cancellation.model_dump(mode="json"),
        "revision_snapshot": revision,
        "note": "Creative render cancelled safely. The Aura directive is ready to revise or render again.",
    }


@router.post("/projects/{project_name}/directives/{directive_id}/sync-outputs")
def sync_creative_outputs(project_name: str, directive_id: str, request: Request):
    member = _member(request)
    project = _project(project_name)
    store = CreativeProjectStore(project)
    manifest = _manifest(store)
    directive = _directive(manifest, directive_id)
    if directive.target_kind not in {"image", "video"}:
        raise HTTPException(400, "This directive is not assigned to an image/video renderer")
    render_meta = directive.metadata.get("creative_renderer")
    if not isinstance(render_meta, dict) or not render_meta.get("prompt_id"):
        raise HTTPException(409, "This directive has not been submitted to a creative renderer")

    renderer = renderer_for(directive.target_kind)
    prompt_id = str(render_meta["prompt_id"])
    try:
        history = renderer.history(prompt_id)
        outputs = renderer.collect_outputs(history, prompt_id)
        status, error = _execution_state(history, prompt_id, outputs)
    except Exception as exc:
        raise HTTPException(502, f"Unable to read creative renderer output: {type(exc).__name__}: {exc}") from exc
    if status == "failed":
        store.update_directive(directive.id, status="failed", metadata={"creative_renderer": {**render_meta, "error": error or "renderer failed"}})
        raise HTTPException(502, "Creative renderer reported a failed execution")
    if not outputs:
        if directive.status != status:
            store.update_directive(directive.id, status=status)
        raise HTTPException(409, "Creative render is not complete yet")

    revision = _snapshot(member, store, label="Before importing creative outputs", reason="creative_output_import")
    output_dir = project / "output" / "creative" / directive.target_kind / directive.id
    output_dir.mkdir(parents=True, exist_ok=True)
    existing = {item.source_ref: item for item in manifest.elements if item.source_ref}
    imported: list[dict] = []

    for index, output in enumerate(outputs, start=1):
        filename = _safe_output_name(output.filename, index)
        destination = output_dir / filename
        relative = destination.relative_to(project).as_posix()
        element = existing.get(relative)
        if element is None:
            try:
                if not destination.is_file():
                    renderer.download_output(output, destination)
            except Exception as exc:
                raise HTTPException(502, f"Failed importing creative output: {type(exc).__name__}: {exc}") from exc
            kind = _media_kind(filename, directive.target_kind)
            element = CreativeElement(
                kind=kind,
                label=f"{manifest.title} — {kind.title()} output {index}",
                role=f"Generated from Aura directive {directive.id}",
                status="ready",
                source_type="generated",
                source_ref=relative,
                parent_ids=list(directive.target_element_ids),
                prompt=directive.instruction,
                metadata={
                    "directive_id": directive.id,
                    "renderer": "comfyui",
                    "renderer_output": output.model_dump(mode="json"),
                },
            )
            manifest = store.add_element(element)
            existing[relative] = element
        imported.append(element.model_dump(mode="json"))

    local_outputs = [item["source_ref"] for item in imported if item.get("source_ref")]
    manifest = store.update_directive(
        directive.id,
        status="completed",
        capability_state="connected",
        metadata={
            "creative_renderer": {
                **render_meta,
                "output_count": len(outputs),
                "local_outputs": local_outputs,
                "synced": True,
            }
        },
    )
    directive = _directive(manifest, directive.id)
    return {
        "directive": directive.model_dump(mode="json"),
        "imported_elements": imported,
        "revision_snapshot": revision,
        "project_relative_outputs": local_outputs,
    }
