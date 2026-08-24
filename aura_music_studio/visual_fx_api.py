from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .plans import VISUAL_FX_STUDIO
from .visual_fx import VisualEffect, VisualFxError, VisualFxStore, VisualKeyframe, VisualLayer
from .visual_fx_render_ownership import VisualFxRenderOwnership, VisualFxRenderOwnershipError

router = APIRouter(prefix="/api/visual-fx", tags=["visual-fx"])
store = VisualFxStore()
render_ownership = VisualFxRenderOwnership(store.db_path, store.output_root)


class CreateVisualProjectBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    width: int = Field(default=1080, ge=256, le=7680)
    height: int = Field(default=1920, ge=256, le=7680)
    fps: float = Field(default=30, ge=1, le=240)
    duration_seconds: float = Field(default=15, gt=0, le=21600)
    background: str = Field(default="#000000", max_length=100)


class KeyframeBody(BaseModel):
    time_seconds: float = Field(ge=0)
    property: str = Field(min_length=1, max_length=100)
    value: float | str | list[float]
    easing: str = Field(default="linear", max_length=100)


class EffectBody(BaseModel):
    kind: str = Field(min_length=1, max_length=100)
    enabled: bool = True
    parameters: dict = Field(default_factory=dict)


class AddLayerBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    layer_type: str
    source: str | None = Field(default=None, max_length=4000)
    start_seconds: float = Field(default=0, ge=0)
    end_seconds: float = Field(default=5, gt=0)
    z_index: int = 0
    opacity: float = Field(default=1.0, ge=0, le=1)
    blend_mode: str = "normal"
    transform: dict = Field(default_factory=dict)
    mask: dict | None = None
    effects: list[EffectBody] = Field(default_factory=list)
    keyframes: list[KeyframeBody] = Field(default_factory=list)
    text: str | None = Field(default=None, max_length=20000)


class UpdateLayerBody(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    source: str | None = Field(default=None, max_length=4000)
    start_seconds: float | None = Field(default=None, ge=0)
    end_seconds: float | None = Field(default=None, gt=0)
    z_index: int | None = None
    opacity: float | None = Field(default=None, ge=0, le=1)
    blend_mode: str | None = None
    transform: dict | None = None
    mask: dict | None = None
    effects: list[EffectBody] | None = None
    keyframes: list[KeyframeBody] | None = None
    text: str | None = Field(default=None, max_length=20000)


class RenderBody(BaseModel):
    output_kind: str = "mp4"


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if not member:
        raise HTTPException(401, "Sign in required")
    if not member.plan.has(VISUAL_FX_STUDIO):
        raise HTTPException(403, "The layered Visual FX Studio is a Pro feature")
    return member


@router.get("/capabilities")
def visual_fx_capabilities():
    return {
        "minimum_plan": "pro",
        "features": [
            "multi-track visual timeline",
            "video/image/text/audio/shape/adjustment/effect layers",
            "keyframes and easing",
            "masks",
            "blend modes",
            "motion tracking metadata",
            "chroma-key effect metadata",
            "AI background/object edit workflow hooks",
            "color grading",
            "speed-curve metadata",
            "advanced captions",
            "non-destructive project schema",
            "real compositor export contract",
            "tenant-bound render ownership",
        ],
        "blend_modes": sorted(store.VALID_BLEND_MODES),
        "layer_types": sorted(store.VALID_LAYER_TYPES),
        "note": "Unsupported renderer effects must fail explicitly; the Studio does not silently flatten or ignore requested Pro controls.",
    }


@router.post("/projects")
def create_project(body: CreateVisualProjectBody, request: Request):
    member = _member(request)
    try:
        return store.create_project(user_id=member.user_id, **body.model_dump())
    except VisualFxError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/projects")
def list_projects(request: Request, limit: int = 50):
    member = _member(request)
    return {"projects": store.list_projects(member.user_id, limit=limit)}


@router.get("/projects/{project_id}")
def get_project(project_id: str, request: Request):
    member = _member(request)
    try:
        return store.get_project(member.user_id, project_id)
    except VisualFxError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/projects/{project_id}/layers")
def add_layer(project_id: str, body: AddLayerBody, request: Request):
    member = _member(request)
    payload = body.model_dump()
    layer = VisualLayer(
        id=uuid4().hex,
        name=payload["name"],
        layer_type=payload["layer_type"],
        source=payload.get("source"),
        start_seconds=payload["start_seconds"],
        end_seconds=payload["end_seconds"],
        z_index=payload["z_index"],
        opacity=payload["opacity"],
        blend_mode=payload["blend_mode"],
        transform=payload.get("transform") or {},
        mask=payload.get("mask"),
        effects=[VisualEffect(**effect) for effect in payload.get("effects") or []],
        keyframes=[VisualKeyframe(**keyframe) for keyframe in payload.get("keyframes") or []],
        text=payload.get("text"),
    )
    try:
        return store.add_layer(user_id=member.user_id, project_id=project_id, layer=layer)
    except VisualFxError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.patch("/projects/{project_id}/layers/{layer_id}")
def update_layer(project_id: str, layer_id: str, body: UpdateLayerBody, request: Request):
    member = _member(request)
    changes = body.model_dump(exclude_unset=True)
    if "effects" in changes:
        changes["effects"] = [effect.model_dump() if hasattr(effect, "model_dump") else effect for effect in changes["effects"]]
    if "keyframes" in changes:
        changes["keyframes"] = [key.model_dump() if hasattr(key, "model_dump") else key for key in changes["keyframes"]]
    try:
        return store.update_layer(user_id=member.user_id, project_id=project_id, layer_id=layer_id, changes=changes)
    except VisualFxError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.delete("/projects/{project_id}/layers/{layer_id}")
def delete_layer(project_id: str, layer_id: str, request: Request):
    member = _member(request)
    try:
        return store.delete_layer(user_id=member.user_id, project_id=project_id, layer_id=layer_id)
    except VisualFxError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/projects/{project_id}/render")
def render_project(project_id: str, body: RenderBody, request: Request):
    member = _member(request)
    try:
        result = store.render_project(user_id=member.user_id, project_id=project_id, output_kind=body.output_kind)
        render_id = str(result["id"])
        output_kind = str(result["output_kind"])
        output_path = Path(str(result["output_path"]))
        render_ownership.register(
            user_id=member.user_id,
            project_id=project_id,
            render_id=render_id,
            output_kind=output_kind,
            output_path=output_path,
        )
        # Never expose a deployment filesystem path to the browser. The opaque, tenant-bound
        # download route is the only client capability for the completed export.
        return {
            "id": render_id,
            "status": result.get("status", "completed"),
            "output_kind": output_kind,
            "download_url": f"/api/visual-fx/renders/{render_id}/{output_kind}",
        }
    except VisualFxError as exc:
        raise HTTPException(422, str(exc)) from exc
    except VisualFxRenderOwnershipError as exc:
        # The file remains inaccessible because unregistered output ids fail closed.
        raise HTTPException(500, "Visual FX export could not be registered securely") from exc


@router.get("/renders")
def list_renders(request: Request, project_id: str | None = None, limit: int = 100):
    member = _member(request)
    items = render_ownership.list_for_user(member.user_id, project_id=project_id, limit=limit)
    return {
        "renders": [
            {
                **item,
                "download_url": f"/api/visual-fx/renders/{item['render_id']}/{item['output_kind']}",
            }
            for item in items
        ]
    }


@router.get("/renders/{render_id}/{output_kind}")
def download_render(render_id: str, output_kind: str, request: Request):
    member = _member(request)
    if output_kind not in {"mp4", "png"}:
        raise HTTPException(404, "Rendered output is unavailable")
    try:
        output = render_ownership.resolve(
            user_id=member.user_id,
            render_id=render_id,
            output_kind=output_kind,
        )
    except VisualFxRenderOwnershipError as exc:
        raise HTTPException(404, "Rendered output is unavailable") from exc
    media_type = "video/mp4" if output_kind == "mp4" else "image/png"
    return FileResponse(
        output,
        media_type=media_type,
        filename=f"live-sound-studio-{render_id}.{output_kind}",
        headers={"Cache-Control": "private, no-store"},
    )
