from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from .aura_image_effect_system import (
    compose_image_effect_system,
    load_reusable_image_effect_system,
    preview_image_effect_system,
    save_reusable_image_effect_system,
)
from .executable_image_effects import ImageEffectGraph
from .route_integrity import register_route_composition_hook
from .tenant_storage import project_path, projects_root

router = APIRouter(tags=["image-effects"])

_PREVIEW_ID = re.compile(r"^[a-f0-9]{32}$")
_ALLOWED_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"})


class ComposeImageEffectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=1200)
    name: str = Field(default="Aura Image FX", min_length=1, max_length=120)


class PreviewImageEffectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1, max_length=300)
    graph: ImageEffectGraph


class SaveImageEffectPresetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    graph: ImageEffectGraph
    preview_token: str = Field(min_length=64, max_length=64)


def _project_or_404(project_name: str) -> Path:
    root = project_path(project_name)
    if not root.exists() or not root.is_dir():
        raise HTTPException(status_code=404, detail="Project not found")
    return root.resolve()


def _relative_source(project: Path, raw_source: str) -> Path:
    normalized = str(raw_source or "").strip().replace("\\", "/")
    pure = PurePosixPath(normalized)
    if not normalized or pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise HTTPException(status_code=400, detail="Source must be a project-relative image path")
    if pure.suffix.casefold() not in _ALLOWED_IMAGE_SUFFIXES:
        raise HTTPException(status_code=400, detail="Unsupported image type")

    candidate = (project / Path(*pure.parts)).resolve()
    try:
        candidate.relative_to(project)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Source must stay inside the project") from exc
    if not candidate.exists() or not candidate.is_file():
        raise HTTPException(status_code=404, detail="Source image not found")
    return candidate


def _preview_directory(project: Path) -> Path:
    target = project / "output" / "image-effects"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _preset_directory() -> Path:
    # projects_root() is already tenant/user scoped. Store presets beside the projects
    # directory so the private system files never appear as creator projects.
    target = projects_root().parent / "image_effect_presets"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _safe_kernel_call(callable_obj: Any, *args: Any, **kwargs: Any) -> Any:
    try:
        return callable_obj(*args, **kwargs)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Image effect resource not found") from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/image-effects/compose")
def compose_image_effect(request: ComposeImageEffectRequest) -> dict[str, Any]:
    return _safe_kernel_call(compose_image_effect_system, request.prompt, name=request.name)


@router.post("/projects/{project_name}/image-effects/preview")
def preview_project_image_effect(project_name: str, request: PreviewImageEffectRequest) -> dict[str, Any]:
    project = _project_or_404(project_name)
    source = _relative_source(project, request.source)
    preview_id = uuid4().hex
    destination = _preview_directory(project) / f"{preview_id}.png"
    evidence = _safe_kernel_call(preview_image_effect_system, source, destination, request.graph)
    return {
        **evidence,
        "preview_id": preview_id,
        "preview_url": f"/projects/{project_name}/image-effects/previews/{preview_id}.png",
        "project_scoped": True,
        "path_exposed": False,
    }


@router.get("/projects/{project_name}/image-effects/previews/{preview_id}.png")
def get_project_image_effect_preview(project_name: str, preview_id: str) -> FileResponse:
    project = _project_or_404(project_name)
    if not _PREVIEW_ID.fullmatch(str(preview_id or "")):
        raise HTTPException(status_code=404, detail="Preview not found")
    target = _preview_directory(project) / f"{preview_id}.png"
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Preview not found")
    return FileResponse(target, media_type="image/png")


@router.post("/image-effects/presets/{preset_name}")
def save_image_effect_preset(preset_name: str, request: SaveImageEffectPresetRequest) -> dict[str, Any]:
    return _safe_kernel_call(
        save_reusable_image_effect_system,
        _preset_directory(),
        preset_name,
        request.graph,
        expected_fingerprint=request.preview_token,
    )


@router.get("/image-effects/presets/{preset_name}")
def get_image_effect_preset(preset_name: str) -> dict[str, Any]:
    return _safe_kernel_call(load_reusable_image_effect_system, _preset_directory(), preset_name)


def _install_image_effect_routes(app: Any) -> None:
    """Install the bounded image-effect surface at final canonical route composition.

    FastAPI snapshots APIRouter contents when a parent router is mounted. This repository also
    performs late route composition for large production domains, so relying only on nested
    engineering-router inclusion can leave newly-added routes absent from the canonical app.
    Reusing the original route objects here preserves request models, endpoint functions and
    middleware while the final integrity pass removes any exact duplicates.
    """

    existing = {
        (str(getattr(route, "path", "")), tuple(sorted(str(method).upper() for method in (getattr(route, "methods", set()) or set()))))
        for route in app.router.routes
    }
    for route in router.routes:
        signature = (
            str(getattr(route, "path", "")),
            tuple(sorted(str(method).upper() for method in (getattr(route, "methods", set()) or set()))),
        )
        if signature not in existing:
            app.router.routes.append(route)
            existing.add(signature)

    app.state.image_effect_routes_installed = True


register_route_composition_hook("image_effect_routes", _install_image_effect_routes)

# Import the browser editor only after the core API/router is defined. The editor owns a separate
# final-composition hook and therefore cannot create a second core Image Effect dispatch authority.
from . import image_effect_editor as _image_effect_editor  # noqa: E402,F401


__all__ = ["router"]
