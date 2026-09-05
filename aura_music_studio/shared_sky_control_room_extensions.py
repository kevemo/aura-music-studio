from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Protocol

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .esp_niche import require_esp_hub_member
from .shared_sky_control_room import (
    AudioPatch,
    StudioConflict,
    StudioInvariantError,
    StudioTransportError,
    studio,
    studio_repo,
    utc_now,
    validate_no_secrets,
)
from .shared_sky_streaming_studios import ScheduleCreate, SceneCreate, SourceCreate, SourceUpdate, shared_sky

router = APIRouter(tags=["Shared Sky Professional Control Room Extensions"])


SCENE_TEMPLATES: dict[str, dict[str, Any]] = {
    "camera-full-screen": {
        "name": "Camera Full Screen", "layout_key": "solo",
        "sources": [{"source_type": "camera", "name": "Camera", "visible": False, "z_index": 10}],
    },
    "camera-chat": {
        "name": "Camera + Chat", "layout_key": "split",
        "sources": [
            {"source_type": "camera", "name": "Camera", "visible": False, "z_index": 10},
            {"source_type": "chat_overlay", "name": "Chat Overlay", "visible": False, "z_index": 30, "binding": {"kind": "chat"}},
        ],
    },
    "creator-canvas": {
        "name": "Creator + Canvas", "layout_key": "presentation",
        "sources": [
            {"source_type": "camera", "name": "Creator Camera", "visible": False, "z_index": 20},
            {"source_type": "presentation", "name": "Creative Canvas", "visible": False, "z_index": 10},
        ],
    },
    "canvas-only": {
        "name": "Canvas Only", "layout_key": "presentation",
        "sources": [{"source_type": "presentation", "name": "Creative Canvas", "visible": False, "z_index": 10}],
    },
    "interview-2-up": {
        "name": "Interview 2-Up", "layout_key": "interview",
        "sources": [
            {"source_type": "remote_guest", "name": "Host", "visible": False, "z_index": 10},
            {"source_type": "remote_guest", "name": "Guest", "visible": False, "z_index": 20},
        ],
    },
    "panel-grid": {
        "name": "Panel / Grid", "layout_key": "grid-4",
        "sources": [{"source_type": "remote_guest", "name": f"Participant {index}", "visible": False, "z_index": index * 10} for index in range(1, 5)],
    },
    "screen-presenter": {
        "name": "Screen Share + Presenter", "layout_key": "presentation",
        "sources": [
            {"source_type": "screen", "name": "Screen Share", "visible": False, "z_index": 10},
            {"source_type": "camera", "name": "Presenter", "visible": False, "z_index": 20},
        ],
    },
    "tutorial": {
        "name": "Tutorial", "layout_key": "presentation",
        "sources": [
            {"source_type": "screen", "name": "Tutorial Surface", "visible": False, "z_index": 10},
            {"source_type": "camera", "name": "Instructor", "visible": False, "z_index": 20},
            {"source_type": "text", "name": "Tutorial Title", "visible": False, "z_index": 30},
        ],
    },
    "music-performance": {
        "name": "Music Performance", "layout_key": "solo",
        "sources": [
            {"source_type": "camera", "name": "Performance Camera", "visible": False, "z_index": 10},
            {"source_type": "microphone", "name": "Performance Audio", "visible": False, "z_index": 20},
        ],
    },
    "gameplay": {
        "name": "Gameplay", "layout_key": "gaming",
        "sources": [
            {"source_type": "game_capture", "name": "Gameplay", "visible": False, "z_index": 10},
            {"source_type": "camera", "name": "Creator Camera", "visible": False, "z_index": 20},
        ],
    },
    "premiere": {
        "name": "Premiere", "layout_key": "solo",
        "sources": [{"source_type": "video", "name": "Premiere Media", "visible": False, "z_index": 10}],
    },
    "brb": {
        "name": "BRB", "layout_key": "solo",
        "sources": [{"source_type": "text", "name": "Be Right Back", "visible": True, "z_index": 10, "text": "Be Right Back"}],
    },
    "starting-soon": {
        "name": "Starting Soon", "layout_key": "solo",
        "sources": [
            {"source_type": "gradient", "name": "Starting Soon Background", "visible": True, "z_index": 0},
            {"source_type": "text", "name": "Starting Soon", "visible": True, "z_index": 10, "text": "Starting Soon"},
            {"source_type": "countdown", "name": "Countdown", "visible": True, "z_index": 20},
        ],
    },
    "ending": {
        "name": "Ending", "layout_key": "solo",
        "sources": [
            {"source_type": "gradient", "name": "Ending Background", "visible": True, "z_index": 0},
            {"source_type": "text", "name": "Thanks For Watching", "visible": True, "z_index": 10, "text": "Thanks For Watching"},
        ],
    },
    "custom": {"name": "Custom", "layout_key": "solo", "sources": []},
}

AUDIO_PRESETS: dict[str, dict[str, Any]] = {
    "speech": {"gain": 1.0, "pan": 0.0, "delay_ms": 0, "monitor": "off", "high_pass_hz": 90, "compressor": True, "limiter": True},
    "podcast": {"gain": 1.0, "pan": 0.0, "delay_ms": 0, "monitor": "off", "high_pass_hz": 75, "compressor": True, "limiter": True},
    "music": {"gain": 1.0, "pan": 0.0, "delay_ms": 0, "monitor": "off", "high_pass_hz": 35, "compressor": False, "limiter": True},
    "gaming": {"gain": 1.0, "pan": 0.0, "delay_ms": 0, "monitor": "off", "high_pass_hz": 85, "compressor": True, "limiter": True},
    "interview": {"gain": 1.0, "pan": 0.0, "delay_ms": 0, "monitor": "off", "high_pass_hz": 90, "compressor": True, "limiter": True},
    "quiet-room": {"gain": 1.0, "pan": 0.0, "delay_ms": 0, "monitor": "off", "high_pass_hz": 60, "compressor": True, "limiter": True},
    "noisy-room": {"gain": 0.95, "pan": 0.0, "delay_ms": 0, "monitor": "off", "high_pass_hz": 120, "compressor": True, "limiter": True},
}


class SceneTemplateInstantiate(BaseModel):
    template_key: str = Field(min_length=1, max_length=80)
    name: str | None = Field(default=None, max_length=120)


class AudioPresetApply(BaseModel):
    preset_key: str = Field(min_length=1, max_length=80)
    expected_session_version: int = Field(ge=1)


class MediaCuePatch(BaseModel):
    expected_session_version: int = Field(ge=1)
    cue_ms: int = Field(default=0, ge=0, le=86_400_000)
    trim_in_ms: int = Field(default=0, ge=0, le=86_400_000)
    trim_out_ms: int | None = Field(default=None, ge=1, le=86_400_000)
    loop: bool = False
    volume: float = Field(default=1.0, ge=0.0, le=2.0)
    autoplay_on_scene_enter: bool = False
    on_scene_exit: Literal["continue", "pause", "stop"] = "pause"


class StudioScheduleCreate(BaseModel):
    title: str = Field(default="Scheduled Shared Sky LIVE", min_length=1, max_length=200)
    start_at: datetime
    destination_ids: list[str] = Field(default_factory=list, max_length=50)
    mode: Literal["live", "pre_recorded"] = "live"


class ParticipantState(BaseModel):
    participant_id: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=160)
    stage: Literal["green_room", "preview", "programme", "disconnected"]
    connection_state: Literal["connecting", "connected", "reconnecting", "disconnected"]
    camera: Literal["on", "off", "unavailable"] = "unavailable"
    microphone: Literal["on", "off", "unavailable"] = "unavailable"
    connection_quality: Literal["good", "fair", "poor", "unknown"] = "unknown"
    role: str = Field(default="guest", max_length=80)


class ParticipantProvider(Protocol):
    def studio_participants(self, user_id: str, broadcast_id: str) -> list[dict[str, Any]]: ...


class ParticipantCompatibilityAdapter:
    """Read-only Chat 6 boundary. Never promotes a connected guest to Programme by assumption."""

    def __init__(self, provider: Any):
        self.provider = provider

    def list(self, user_id: str, broadcast_id: str | None) -> dict[str, Any]:
        if not broadcast_id:
            return {"supported": False, "participants": [], "reason": "Participant staging requires a broadcast session"}
        getter = getattr(self.provider, "studio_participants", None)
        if not callable(getter):
            return {"supported": False, "participants": [], "reason": "Chat 6 participant staging contract not merged"}
        rows = [ParticipantState.model_validate(row).model_dump() for row in getter(user_id, broadcast_id)]
        return {"supported": True, "participants": rows, "authoritative": True}


class RecordingCompatibilityAdapter:
    """Chat 2 recording action seam. It refuses to invent start/stop success."""

    def __init__(self, provider: Any):
        self.provider = provider

    def action(self, user_id: str, broadcast_id: str | None, action: Literal["start", "stop"]) -> dict[str, Any]:
        if not broadcast_id:
            raise StudioTransportError("Recording requires a broadcast session")
        method = getattr(self.provider, f"{action}_recording", None)
        if not callable(method):
            raise StudioTransportError(f"Chat 2 recording {action} contract not merged")
        result = method(user_id, broadcast_id)
        if not isinstance(result, dict) or not result.get("authoritative", True):
            raise StudioTransportError("Recording provider did not return authoritative state")
        return result


participants = ParticipantCompatibilityAdapter(shared_sky)
recording_actions = RecordingCompatibilityAdapter(shared_sky)


def _member(request: Request):
    return require_esp_hub_member(request)


def _raise(exc: Exception) -> None:
    if isinstance(exc, KeyError):
        raise HTTPException(404, "Shared Sky studio resource not found") from exc
    if isinstance(exc, StudioConflict):
        raise HTTPException(409, str(exc)) from exc
    if isinstance(exc, StudioTransportError):
        raise HTTPException(503, str(exc)) from exc
    if isinstance(exc, (StudioInvariantError, ValueError)):
        raise HTTPException(400, str(exc)) from exc
    raise HTTPException(500, "Shared Sky studio extension operation failed") from exc


def _template_config(template_key: str) -> dict[str, Any]:
    template = SCENE_TEMPLATES.get(template_key)
    if not template:
        raise StudioInvariantError("Unknown Shared Sky scene template")
    return template


def instantiate_template(user_id: str, project_id: str, template_key: str, name: str | None = None) -> dict[str, Any]:
    shared_sky.project(user_id, project_id)
    template = _template_config(template_key)
    scene = shared_sky.create_scene(
        user_id,
        project_id,
        SceneCreate(
            name=(name or template["name"])[:120],
            layout_key=str(template.get("layout_key") or "solo"),
            transition_key="fade",
            transition_ms=350,
        ),
    )
    try:
        for spec in template.get("sources", []):
            config = {
                "privacy": "programme_safe",
                "template_slot": True,
                "template_key": template_key,
                "transform": {"x": 0, "y": 0, "width": 1, "height": 1, "rotation": 0, "opacity": 1},
            }
            if spec.get("binding"):
                config["binding"] = spec["binding"]
            if spec.get("text"):
                config["text"] = spec["text"]
            validate_no_secrets(config)
            shared_sky.create_source(
                user_id,
                scene["id"],
                SourceCreate(
                    source_type=spec["source_type"],
                    name=spec["name"],
                    config=config,
                    visible=bool(spec.get("visible", False)),
                    locked=False,
                    z_index=int(spec.get("z_index", 0)),
                ),
            )
    except Exception:
        shared_sky.delete_scene(user_id, scene["id"])
        raise
    return shared_sky.scene(user_id, scene["id"])


def apply_media_cue(user_id: str, session_id: str, source_id: str, body: MediaCuePatch) -> dict[str, Any]:
    current = studio_repo.get_session(user_id, session_id)
    if current["version"] != body.expected_session_version:
        raise StudioConflict("Studio state changed in another tab/operator")
    source = shared_sky.source(user_id, source_id)
    if source.get("project_id") != current["project_id"]:
        raise StudioInvariantError("Media source does not belong to this studio project")
    if source.get("source_type") not in {"video", "audio", "slideshow"}:
        raise StudioInvariantError("Cue controls apply only to media/playlist sources")
    if body.trim_out_ms is not None and body.trim_out_ms <= body.trim_in_ms:
        raise StudioInvariantError("Media trim-out must be later than trim-in")
    config = dict(source.get("config") or {})
    config["playback"] = {
        "cue_ms": body.cue_ms,
        "trim_in_ms": body.trim_in_ms,
        "trim_out_ms": body.trim_out_ms,
        "loop": body.loop,
        "volume": body.volume,
        "autoplay_on_scene_enter": body.autoplay_on_scene_enter,
        "on_scene_exit": body.on_scene_exit,
        "updated_at": utc_now(),
    }
    validate_no_secrets(config)
    updated = shared_sky.update_source(user_id, source_id, SourceUpdate(config=config))
    bumped = studio_repo.set_autosave_state(
        user_id,
        session_id,
        current["version"],
        {"reason": "media_cue", "source_id": source_id, "saved_at": utc_now()},
    )
    return {"source": updated, "session": bumped, "programme_unchanged": True}


def aura_production_diagnostics(user_id: str, session_id: str) -> dict[str, Any]:
    hydrated = studio.session(user_id, session_id)
    session = hydrated["session"]
    project = hydrated["project"]
    transport = hydrated["transport"]
    preview = next((scene for scene in project.get("scenes", []) if scene["id"] == session.get("preview_scene_id")), None)
    sources = list((preview or {}).get("sources", []))
    recommendations: list[dict[str, Any]] = []
    if not sources:
        recommendations.append({"kind": "scene", "severity": "info", "message": "Preview has no sources. Add or attach a production source before taking it to Programme.", "evidence": {"source_count": 0}})
    if not session.get("programme_scene_id"):
        recommendations.append({"kind": "programme", "severity": "info", "message": "Programme has no committed scene yet. Preview remains isolated until an explicit CUT or TRANSITION.", "evidence": {"programme_scene_id": None}})
    if transport.get("state") in {"live", "starting"} and not transport.get("programme_commit_supported"):
        recommendations.append({"kind": "transport", "severity": "warning", "message": "Live Programme switching is locked because the authoritative Chat 2 commit contract is unavailable.", "evidence": {"transport_state": transport.get("state"), "programme_commit_supported": False}})
    audio_sources = [row for row in sources if row.get("source_type") in {"microphone", "audio", "desktop_audio", "application_audio", "video"}]
    if sources and not audio_sources:
        recommendations.append({"kind": "audio", "severity": "info", "message": "No audio-bearing source is present in Preview. Confirm that silence is intentional before going live.", "evidence": {"audio_source_count": 0}})
    hidden_slots = [row for row in sources if not row.get("visible") and (row.get("config") or {}).get("template_slot")]
    if hidden_slots:
        recommendations.append({"kind": "template", "severity": "info", "message": "This scene still has unattached template slots. Attach devices/media before enabling those sources.", "evidence": {"unattached_template_slots": len(hidden_slots)}})
    return {
        "assistant": "Aura production assistance",
        "mode": "advisory_only",
        "authoritative_actions_performed": False,
        "generated_at": utc_now(),
        "recommendations": recommendations,
        "evidence_source": "current studio/transport state",
    }


@router.get("/shared-sky/studio/api/templates")
def list_scene_templates(request: Request):
    _member(request)
    return {"templates": [{"key": key, "name": row["name"], "layout_key": row["layout_key"], "source_slots": len(row.get("sources", []))} for key, row in SCENE_TEMPLATES.items()]}


@router.post("/shared-sky/studio/api/projects/{project_id}/templates/instantiate")
def instantiate_scene_template(project_id: str, body: SceneTemplateInstantiate, request: Request):
    member, _ = _member(request)
    try:
        return {"scene": instantiate_template(member.user_id, project_id, body.template_key, body.name)}
    except Exception as exc:
        _raise(exc)


@router.get("/shared-sky/studio/api/audio/presets")
def list_audio_presets(request: Request):
    _member(request)
    return {"presets": AUDIO_PRESETS}


@router.post("/shared-sky/studio/api/sessions/{session_id}/sources/{source_id}/audio/preset")
def apply_audio_preset(session_id: str, source_id: str, body: AudioPresetApply, request: Request):
    member, _ = _member(request)
    try:
        preset = AUDIO_PRESETS.get(body.preset_key)
        if not preset:
            raise StudioInvariantError("Unknown Shared Sky audio preset")
        return studio.update_audio(
            member.user_id,
            session_id,
            source_id,
            AudioPatch(audio=dict(preset), expected_session_version=body.expected_session_version),
        )
    except Exception as exc:
        _raise(exc)


@router.patch("/shared-sky/studio/api/sessions/{session_id}/sources/{source_id}/cue")
def patch_media_cue(session_id: str, source_id: str, body: MediaCuePatch, request: Request):
    member, _ = _member(request)
    try:
        return apply_media_cue(member.user_id, session_id, source_id, body)
    except Exception as exc:
        _raise(exc)


@router.post("/shared-sky/studio/api/sessions/{session_id}/schedules")
def create_studio_schedule(session_id: str, body: StudioScheduleCreate, request: Request):
    member, _ = _member(request)
    try:
        session = studio_repo.get_session(member.user_id, session_id)
        if body.start_at.tzinfo is None or body.start_at.utcoffset() is None:
            raise StudioInvariantError("Scheduled LIVE time must include an explicit timezone")
        schedule = shared_sky.create_schedule(
            member.user_id,
            ScheduleCreate(
                project_id=session["project_id"],
                title=body.title,
                start_at=body.start_at.isoformat(),
                destination_ids=body.destination_ids,
                mode=body.mode,
            ),
        )
        shared_sky.event(member.user_id, session.get("broadcast_id"), "studio_schedule_created", {"session_id": session_id, "schedule_id": schedule["id"], "mode": body.mode})
        return {"schedule": schedule, "execution_owner": "Chat 2 Shared Sky scheduler"}
    except Exception as exc:
        _raise(exc)


@router.delete("/shared-sky/studio/api/sessions/{session_id}/schedules/{schedule_id}")
def cancel_studio_schedule(session_id: str, schedule_id: str, request: Request):
    member, _ = _member(request)
    try:
        session = studio_repo.get_session(member.user_id, session_id)
        schedule = shared_sky.schedule(member.user_id, schedule_id)
        if schedule.get("project_id") != session["project_id"]:
            raise StudioInvariantError("Schedule does not belong to this studio project")
        shared_sky.delete_schedule(member.user_id, schedule_id)
        shared_sky.event(member.user_id, session.get("broadcast_id"), "studio_schedule_cancelled", {"session_id": session_id, "schedule_id": schedule_id})
        return {"cancelled": True, "schedule_id": schedule_id}
    except Exception as exc:
        _raise(exc)


@router.get("/shared-sky/studio/api/sessions/{session_id}/participants")
def studio_participants(session_id: str, request: Request):
    member, _ = _member(request)
    try:
        session = studio_repo.get_session(member.user_id, session_id)
        return participants.list(member.user_id, session.get("broadcast_id"))
    except Exception as exc:
        _raise(exc)


@router.post("/shared-sky/studio/api/sessions/{session_id}/recording/start")
def start_recording(session_id: str, request: Request):
    member, _ = _member(request)
    try:
        session = studio_repo.get_session(member.user_id, session_id)
        result = recording_actions.action(member.user_id, session.get("broadcast_id"), "start")
        shared_sky.event(member.user_id, session.get("broadcast_id"), "studio_recording_start", {"session_id": session_id, "authoritative_state": result.get("state")})
        return result
    except Exception as exc:
        _raise(exc)


@router.post("/shared-sky/studio/api/sessions/{session_id}/recording/stop")
def stop_recording(session_id: str, request: Request):
    member, _ = _member(request)
    try:
        session = studio_repo.get_session(member.user_id, session_id)
        result = recording_actions.action(member.user_id, session.get("broadcast_id"), "stop")
        shared_sky.event(member.user_id, session.get("broadcast_id"), "studio_recording_stop", {"session_id": session_id, "authoritative_state": result.get("state")})
        return result
    except Exception as exc:
        _raise(exc)


@router.get("/shared-sky/studio/api/sessions/{session_id}/preflight")
def studio_preflight(session_id: str, request: Request):
    member, _ = _member(request)
    try:
        session = studio_repo.get_session(member.user_id, session_id)
        transport = studio.transport.status(member.user_id, session.get("broadcast_id"))
        participants_state = participants.list(member.user_id, session.get("broadcast_id"))
        return {
            "session_id": session_id,
            "generated_at": utc_now(),
            "transport": transport,
            "participants": participants_state,
            "recording": studio.transport.recording_capabilities(member.user_id, session.get("broadcast_id")),
            "programme": {"preview_scene_id": session.get("preview_scene_id"), "programme_scene_id": session.get("programme_scene_id"), "transition_state": session.get("transition_state")},
            "ready_for_programme_switch": session.get("transition_state") == "idle" and bool(session.get("preview_scene_id")) and bool(transport.get("programme_commit_supported")),
        }
    except Exception as exc:
        _raise(exc)


@router.get("/shared-sky/studio/api/sessions/{session_id}/aura/diagnostics")
def aura_diagnostics(session_id: str, request: Request):
    member, _ = _member(request)
    try:
        return aura_production_diagnostics(member.user_id, session_id)
    except Exception as exc:
        _raise(exc)


def install_shared_sky_control_room_extensions(app: Any) -> None:
    existing = {getattr(route, "path", "") for route in app.router.routes}
    if "/shared-sky/studio/api/templates" not in existing:
        app.include_router(router)


__all__ = [
    "AUDIO_PRESETS",
    "ParticipantCompatibilityAdapter",
    "ParticipantState",
    "RecordingCompatibilityAdapter",
    "SCENE_TEMPLATES",
    "apply_media_cue",
    "aura_production_diagnostics",
    "install_shared_sky_control_room_extensions",
    "instantiate_template",
    "router",
]
