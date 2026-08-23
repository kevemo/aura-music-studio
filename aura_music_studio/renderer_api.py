from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .admin_portal import _authorized
from .renderer_runtime import AceStepRuntime, RendererUnavailable, real_renderer_status

router = APIRouter(tags=["Aura Live Neural Renderers"])


class AceInitRequest(BaseModel):
    model: str | None = None
    slot: int = Field(default=1, ge=1, le=3)
    init_llm: bool = False
    lm_model: str | None = None


def _safe_member_status(status: dict) -> dict:
    ace = status.get("ace_step") or {}
    stats = ace.get("stats") or {}
    return {
        "primary": status.get("primary"),
        "primary_ready": bool(status.get("primary_ready")),
        "any_real_renderer_ready": bool(status.get("any_real_renderer_ready")),
        "final_audio_policy": "real_waveform_only",
        "ace_step": {
            "ready": bool(ace.get("ready")),
            "reachable": bool(ace.get("reachable")),
            "full_song_model": ace.get("full_song_model"),
            "build_around_ready": bool(ace.get("supports_build_around")),
            "track_model": ace.get("track_model"),
            "llm_initialized": bool(ace.get("llm_initialized")),
            "loaded_lm_model": ace.get("loaded_lm_model"),
            "queue_size": stats.get("queue_size"),
            "queue_maxsize": stats.get("queue_maxsize"),
        },
        "yue": {
            "configured": bool((status.get("yue") or {}).get("configured")),
            "ready": bool((status.get("yue") or {}).get("ready")),
            "role": (status.get("yue") or {}).get("role"),
        },
    }


@router.get("/renderers/status")
def member_renderer_status(request: Request):
    if not getattr(request.state, "member", None):
        raise HTTPException(401, "Sign in required")
    return _safe_member_status(real_renderer_status(include_stats=True))


@router.get("/owner/renderers/status")
def owner_renderer_status(request: Request):
    if not _authorized(request):
        raise HTTPException(401, "Owner access required")
    return real_renderer_status(include_stats=True)


@router.post("/owner/renderers/ace/init")
def owner_initialize_ace(payload: AceInitRequest, request: Request):
    if not _authorized(request):
        raise HTTPException(401, "Owner access required")
    try:
        result = AceStepRuntime().initialize(
            model=payload.model,
            slot=payload.slot,
            init_llm=payload.init_llm,
            lm_model=payload.lm_model,
        )
    except Exception as exc:
        raise HTTPException(503, f"ACE-Step initialization failed: {type(exc).__name__}: {exc}") from exc
    return {"initialized": True, "result": result, "status": real_renderer_status(include_stats=True)}


@router.get("/renderers/ready")
def renderer_ready(request: Request, task_type: str = "text2music"):
    if not getattr(request.state, "member", None):
        raise HTTPException(401, "Sign in required")
    try:
        model = AceStepRuntime().require_ready(task_type=task_type)
        return {"ready": True, "engine": "ace-step-1.5", "model": model, "task_type": task_type}
    except RendererUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
