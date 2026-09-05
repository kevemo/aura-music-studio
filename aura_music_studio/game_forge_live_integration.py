from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from secrets import token_urlsafe
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .game_forge_api import _creator
from .game_forge_models import GameDNA
from .game_forge_store import game_dir, load_game


router = APIRouter(tags=["Game Forge Shared Sky Live"])

LIVE_SCHEMA_VERSION = "game_forge_live_source.v1"
LIVE_STATE_SCHEMA_VERSION = 1
_OPAQUE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")

GameForgeLiveSourceType = Literal[
    "clean_game_output",
    "playtest_runtime",
    "approved_build_output",
    "selected_editor_viewport",
    "selected_scene_viewport",
    "coding_tutorial",
    "visual_logic",
    "profiler_tutorial",
    "creator_camera",
    "microphone",
    "game_audio",
]
GameForgePresentationMode = Literal[
    "development",
    "tutorial",
    "build_review",
    "playtest",
    "multiplayer_playtest",
    "gameplay",
    "launch_showcase",
    "brb",
]
AudienceVisibility = Literal["private", "unlisted", "public"]
LiveSourceStatus = Literal["active", "hidden", "detached", "revoked"]
LiveHealth = Literal["ready", "degraded", "not_ready", "revoked"]

# These categories are policy, not user-supplied configuration. The descriptor can therefore be
# passed to a compositor/transport consumer without serialising the editor/repository itself.
LIVE_PRIVACY_EXCLUSIONS: tuple[str, ...] = (
    "api_keys_tokens_and_environment_variables",
    "signing_and_destination_credentials",
    "git_and_private_repository_credentials",
    "unselected_private_source_files",
    "cloud_backend_database_and_admin_consoles",
    "hidden_debug_and_security_logic",
    "unpublished_monetisation_configuration",
    "private_player_or_user_data",
    "collaborator_contact_details",
    "unreleased_roadmap_comments_and_tasks",
    "private_test_accounts",
    "private_reference_or_training_assets",
    "other_tenant_projects",
    "raw_crash_dumps_or_logs_with_secrets_or_personal_data",
)

_EXPLICIT_PRESENTATION_SOURCES = {
    "selected_editor_viewport",
    "selected_scene_viewport",
    "coding_tutorial",
    "visual_logic",
    "profiler_tutorial",
}
_BUILD_SOURCES = {"clean_game_output", "playtest_runtime", "approved_build_output"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _error(status: int, code: str, message: str, correlation_id: str | None = None) -> HTTPException:
    return HTTPException(
        status,
        {
            "code": code,
            "message": message,
            "correlation_id": correlation_id or f"corr_{uuid4().hex}",
        },
    )


def _correlation_id(request: Request) -> str:
    supplied = str(request.headers.get("X-Correlation-ID") or request.headers.get("X-Request-ID") or "").strip()
    if supplied and len(supplied) <= 160 and _OPAQUE_REF.fullmatch(supplied):
        return supplied
    return f"corr_{uuid4().hex}"


def _opaque(value: str | None, *, field_name: str, required: bool = False) -> str | None:
    clean = str(value or "").strip()
    if not clean:
        if required:
            raise _error(400, "live_source_privacy_blocked", f"{field_name} is required")
        return None
    if not _OPAQUE_REF.fullmatch(clean):
        raise _error(400, "live_source_privacy_blocked", f"{field_name} must be an opaque identifier, not a path, URL or source payload")
    return clean


def _game(game_id: str) -> GameDNA:
    try:
        return load_game(game_id)
    except (ValueError, FileNotFoundError) as exc:
        raise _error(404, "project_unauthorised", "Game project not found in the current workspace") from exc


def _member_identity(member) -> str:
    value = str(getattr(member, "user_id", "") or "").strip()
    if not value:
        raise _error(401, "unauthenticated", "Authenticated creator identity is unavailable")
    return value


def _workspace_id(game: GameDNA) -> str | None:
    name = str((game.metadata or {}).get("creative_project_name") or "").strip()
    if not name:
        return None
    # Never expose a tenant filesystem path. This is a logical project reference only.
    return f"creative:{name}"


def _state_path(game_id: str) -> Path:
    folder = game_dir(game_id) / "live"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "shared_sky.json"


class LiveInclusionManifest(BaseModel):
    capture_scope: Literal["game_runtime", "approved_presentation_surface", "creator_device", "approved_game_audio"]
    approved_surfaces: list[str] = Field(default_factory=list, max_length=12)
    private_editor_panels_included: bool = False
    source_code_payload_included: bool = False
    credentials_included: bool = False
    whole_window_capture: bool = False


class GameForgeSafeLiveSource(BaseModel):
    source_adapter_id: str
    schema_version: str = LIVE_SCHEMA_VERSION
    studio_type: Literal["game_forge"] = "game_forge"
    project_id: str
    workspace_id: str | None = None
    creator_identity_ref: str
    live_session_id: str
    participant_ref: str | None = None
    source_type: GameForgeLiveSourceType
    safe_display_label: str = Field(min_length=1, max_length=120)
    media_kind: Literal["video", "audio"]
    aspect_profile: Literal["source_native", "landscape_16_9", "portrait_9_16", "square_1_1"] = "source_native"
    project_version: int = Field(ge=1)
    build_id: str | None = None
    project_visibility: Literal["private", "public_test"] = "private"
    audience_visibility: AudienceVisibility = "private"
    privacy_classification: Literal["private_project", "public_test_project"] = "private_project"
    inclusion_manifest: LiveInclusionManifest
    exclusion_policy: list[str] = Field(default_factory=lambda: list(LIVE_PRIVACY_EXCLUSIONS))
    rights_readiness: Literal["verified", "unverified"] = "unverified"
    shared_sky_source_ref: str | None = None
    health: LiveHealth = "ready"
    status: LiveSourceStatus = "active"
    revoked: bool = False
    presentation_mode: GameForgePresentationMode = "playtest"
    presentation_surface_ref: str | None = None
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)
    correlation_id: str


class GameForgeLiveFeedback(BaseModel):
    feedback_id: str = Field(default_factory=lambda: f"gf_feedback_{uuid4().hex}")
    project_id: str
    build_id: str | None = None
    project_version: int
    live_session_id: str
    source_adapter_id: str
    author_ref: str | None = None
    live_time_seconds: float | None = Field(default=None, ge=0)
    category: Literal["bug", "ux", "balance", "idea", "question", "other"] = "other"
    text: str = Field(min_length=1, max_length=4000)
    clip_ref: str | None = None
    moderation_state: Literal["unreviewed", "approved", "hidden"] = "unreviewed"
    triage_state: Literal["new", "accepted", "rejected", "resolved"] = "new"
    created_at: str = Field(default_factory=_now)
    correlation_id: str


class GameForgeLiveReturnRecord(BaseModel):
    return_id: str = Field(default_factory=lambda: f"gf_return_{uuid4().hex}")
    idempotency_key: str
    project_id: str
    build_id: str | None = None
    project_version: int
    live_session_id: str
    source_adapter_id: str
    asset_ref: str
    asset_type: Literal["recording", "replay", "clip", "highlight", "tutorial_segment", "bug_reproduction", "promotional_candidate"]
    start_seconds: float | None = Field(default=None, ge=0)
    end_seconds: float | None = Field(default=None, ge=0)
    provenance: Literal["shared_sky"] = "shared_sky"
    processing_state: Literal["processing", "ready", "failed"] = "processing"
    created_at: str = Field(default_factory=_now)
    correlation_id: str


class GameForgeLiveState(BaseModel):
    schema_version: int = LIVE_STATE_SCHEMA_VERSION
    project_id: str
    sources: dict[str, GameForgeSafeLiveSource] = Field(default_factory=dict)
    feedback: dict[str, GameForgeLiveFeedback] = Field(default_factory=dict)
    returns: dict[str, GameForgeLiveReturnRecord] = Field(default_factory=dict)
    updated_at: str = Field(default_factory=_now)


class AttachLiveSourceRequest(BaseModel):
    live_session_id: str = Field(min_length=1, max_length=160)
    source_type: GameForgeLiveSourceType = "clean_game_output"
    safe_display_label: str | None = Field(default=None, max_length=120)
    participant_ref: str | None = Field(default=None, max_length=160)
    presentation_surface_ref: str | None = Field(default=None, max_length=160)
    audience_visibility: AudienceVisibility = "private"
    aspect_profile: Literal["source_native", "landscape_16_9", "portrait_9_16", "square_1_1"] = "source_native"
    presentation_mode: GameForgePresentationMode = "playtest"
    idempotency_key: str | None = Field(default=None, max_length=160)


class TransitionLiveSourceRequest(BaseModel):
    presentation_mode: GameForgePresentationMode


class PromoteLiveVersionRequest(BaseModel):
    expected_project_version: int = Field(ge=1)
    expected_build_id: str | None = Field(default=None, max_length=160)


class EmergencyHideRequest(BaseModel):
    revoke: bool = False


class CreateLiveFeedbackRequest(BaseModel):
    live_session_id: str = Field(min_length=1, max_length=160)
    source_adapter_id: str = Field(min_length=1, max_length=160)
    author_ref: str | None = Field(default=None, max_length=160)
    live_time_seconds: float | None = Field(default=None, ge=0)
    category: Literal["bug", "ux", "balance", "idea", "question", "other"] = "other"
    text: str = Field(min_length=1, max_length=4000)
    clip_ref: str | None = Field(default=None, max_length=160)
    creator_promoted: bool = False
    structured_playtest: bool = False


class CreateLiveReturnRequest(BaseModel):
    live_session_id: str = Field(min_length=1, max_length=160)
    source_adapter_id: str = Field(min_length=1, max_length=160)
    asset_ref: str = Field(min_length=1, max_length=160)
    asset_type: Literal["recording", "replay", "clip", "highlight", "tutorial_segment", "bug_reproduction", "promotional_candidate"]
    start_seconds: float | None = Field(default=None, ge=0)
    end_seconds: float | None = Field(default=None, ge=0)
    processing_state: Literal["processing", "ready", "failed"] = "processing"
    idempotency_key: str | None = Field(default=None, max_length=160)


def _load_state(game_id: str) -> GameForgeLiveState:
    path = _state_path(game_id)
    if not path.is_file():
        return GameForgeLiveState(project_id=game_id)
    try:
        state = GameForgeLiveState.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise _error(409, "internal", "Game Forge live state could not be validated") from exc
    if state.project_id != game_id:
        raise _error(409, "internal", "Game Forge live state project identity mismatch")
    return state


def _save_state(state: GameForgeLiveState) -> None:
    path = _state_path(state.project_id)
    state.updated_at = _now()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(state.model_dump_json(indent=2), encoding="utf-8")
    tmp.replace(path)


def _source_id(*, creator_id: str, game_id: str, body: AttachLiveSourceRequest) -> str:
    participant = _opaque(body.participant_ref, field_name="participant_ref") or "solo"
    live_session = _opaque(body.live_session_id, field_name="live_session_id", required=True)
    supplied_key = _opaque(body.idempotency_key, field_name="idempotency_key")
    material = supplied_key or f"{creator_id}|{game_id}|{live_session}|{participant}|{body.source_type}"
    return f"gfs_{hashlib.sha256(material.encode('utf-8')).hexdigest()[:24]}"


def _required_build(game: GameDNA, source_type: str):
    if source_type not in _BUILD_SOURCES:
        return None
    build = game.latest_build
    if build is None or not build.private_playtest_ready:
        raise _error(409, "live_source_not_ready", "Build the current game before using a gameplay/playtest LIVE source")
    if source_type == "approved_build_output":
        assessment = game.rating_assessment
        if game.status not in {"approved_test", "public_test"} or not assessment or not assessment.public_test_allowed:
            raise _error(409, "live_source_not_ready", "Approved Build Output requires a current approved Game Forge test build")
        if assessment.content_hash != build.content_hash:
            raise _error(409, "stale_project_version", "The approved assessment does not match the current build")
    return build


def _inclusion_manifest(source_type: str, presentation_surface_ref: str | None) -> LiveInclusionManifest:
    if source_type in _BUILD_SOURCES:
        return LiveInclusionManifest(capture_scope="game_runtime", approved_surfaces=["game_canvas"])
    if source_type in _EXPLICIT_PRESENTATION_SOURCES:
        ref = _opaque(presentation_surface_ref, field_name="presentation_surface_ref", required=True)
        return LiveInclusionManifest(capture_scope="approved_presentation_surface", approved_surfaces=[ref])
    if source_type in {"creator_camera", "microphone"}:
        return LiveInclusionManifest(capture_scope="creator_device", approved_surfaces=[source_type])
    return LiveInclusionManifest(capture_scope="approved_game_audio", approved_surfaces=["game_audio_bus"])


def shared_sky_compatibility_payload(source: GameForgeSafeLiveSource) -> dict:
    """Return the bounded Chat 2/3 handoff; no project document or arbitrary config is accepted."""
    return {
        "source_type": "game_forge",
        "name": source.safe_display_label,
        "visible": source.status == "active" and not source.revoked,
        "locked": False,
        "config": {
            "schema_version": source.schema_version,
            "source_adapter_id": source.source_adapter_id,
            "studio_type": source.studio_type,
            "project_id": source.project_id,
            "workspace_id": source.workspace_id,
            "creator_identity_ref": source.creator_identity_ref,
            "live_session_id": source.live_session_id,
            "participant_ref": source.participant_ref,
            "source_type": source.source_type,
            "media_kind": source.media_kind,
            "aspect_profile": source.aspect_profile,
            "project_version": source.project_version,
            "build_id": source.build_id,
            "audience_visibility": source.audience_visibility,
            "privacy_classification": source.privacy_classification,
            "inclusion_manifest": source.inclusion_manifest.model_dump(mode="json"),
            "exclusion_policy": list(source.exclusion_policy),
            "rights_readiness": source.rights_readiness,
            "presentation_mode": source.presentation_mode,
            "presentation_surface_ref": source.presentation_surface_ref,
            "health": source.health,
            "revoked": source.revoked,
            "correlation_id": source.correlation_id,
        },
    }


def _source_payload(source: GameForgeSafeLiveSource) -> dict:
    return {
        "source": source.model_dump(mode="json"),
        "shared_sky_compatibility": shared_sky_compatibility_payload(source),
        "transport_owned_by_chat_2": True,
        "composition_owned_by_chat_3": True,
        "battle_truth_owned_by_chat_6": True,
        "project_privacy_changed": False,
    }


def _owned_source(state: GameForgeLiveState, source_adapter_id: str) -> GameForgeSafeLiveSource:
    clean = _opaque(source_adapter_id, field_name="source_adapter_id", required=True)
    source = state.sources.get(clean)
    if source is None:
        raise _error(404, "live_source_not_ready", "Game Forge LIVE source was not found")
    return source


@router.get("/api/game-forge/games/{game_id}/live")
def game_live_state(game_id: str, request: Request):
    member = _creator(request)
    _member_identity(member)
    game = _game(game_id)
    state = _load_state(game.id)
    return {
        "project_id": game.id,
        "project_version": game.version,
        "project_visibility": "public_test" if game.status == "public_test" and game.public_id else "private",
        "sources": [_source_payload(row) for row in state.sources.values()],
        "feedback": [row.model_dump(mode="json") for row in state.feedback.values()],
        "returns": [row.model_dump(mode="json") for row in state.returns.values()],
        "community_events_are_read_only": True,
        "viewer_events_auto_mutate_game": False,
        "destination_credentials_stored_here": False,
    }


@router.post("/api/game-forge/games/{game_id}/live/sources")
def attach_game_live_source(game_id: str, body: AttachLiveSourceRequest, request: Request):
    member = _creator(request)
    creator_id = _member_identity(member)
    correlation_id = _correlation_id(request)
    game = _game(game_id)
    live_session = _opaque(body.live_session_id, field_name="live_session_id", required=True)
    participant = _opaque(body.participant_ref, field_name="participant_ref")
    presentation_ref = _opaque(body.presentation_surface_ref, field_name="presentation_surface_ref")
    if body.source_type in _EXPLICIT_PRESENTATION_SOURCES and not presentation_ref:
        raise _error(409, "live_source_privacy_blocked", "Editor, code, node-graph and profiler sources require an explicit approved presentation surface", correlation_id)
    if body.audience_visibility == "public" and not game.rights_confirmed:
        raise _error(409, "rights_not_verified", "Confirm project rights before attaching a public Game Forge LIVE source", correlation_id)
    build = _required_build(game, body.source_type)
    source_adapter_id = _source_id(creator_id=creator_id, game_id=game.id, body=body)
    state = _load_state(game.id)
    existing = state.sources.get(source_adapter_id)
    if existing is not None:
        if existing.revoked:
            raise _error(409, "live_source_privacy_blocked", "This source handle was revoked; use a new LIVE session before attaching again", correlation_id)
        return {**_source_payload(existing), "idempotent_replay": True}

    project_public = game.status == "public_test" and bool(game.public_id)
    label = str(body.safe_display_label or "").strip() or (
        "Game Forge — Clean Game Output" if body.source_type in _BUILD_SOURCES else f"Game Forge — {body.source_type.replace('_', ' ').title()}"
    )
    media_kind: Literal["video", "audio"] = "audio" if body.source_type in {"microphone", "game_audio"} else "video"
    source = GameForgeSafeLiveSource(
        source_adapter_id=source_adapter_id,
        project_id=game.id,
        workspace_id=_workspace_id(game),
        creator_identity_ref=creator_id,
        live_session_id=live_session,
        participant_ref=participant,
        source_type=body.source_type,
        safe_display_label=label,
        media_kind=media_kind,
        aspect_profile=body.aspect_profile,
        project_version=game.version,
        build_id=build.build_id if build else None,
        project_visibility="public_test" if project_public else "private",
        audience_visibility=body.audience_visibility,
        privacy_classification="public_test_project" if project_public else "private_project",
        inclusion_manifest=_inclusion_manifest(body.source_type, presentation_ref),
        rights_readiness="verified" if game.rights_confirmed else "unverified",
        presentation_mode=body.presentation_mode,
        presentation_surface_ref=presentation_ref,
        correlation_id=correlation_id,
    )
    state.sources[source.source_adapter_id] = source
    _save_state(state)
    return {**_source_payload(source), "idempotent_replay": False}


@router.patch("/api/game-forge/games/{game_id}/live/sources/{source_adapter_id}/presentation")
def transition_game_live_source(game_id: str, source_adapter_id: str, body: TransitionLiveSourceRequest, request: Request):
    _creator(request)
    _game(game_id)
    state = _load_state(game_id)
    source = _owned_source(state, source_adapter_id)
    if source.revoked:
        raise _error(409, "live_source_privacy_blocked", "Revoked LIVE sources cannot transition")
    source.presentation_mode = body.presentation_mode
    source.status = "hidden" if body.presentation_mode == "brb" else "active"
    source.health = "ready"
    source.updated_at = _now()
    source.correlation_id = _correlation_id(request)
    _save_state(state)
    return {
        **_source_payload(source),
        "same_live_session": True,
        "live_session_id": source.live_session_id,
        "new_live_session_created": False,
    }


@router.post("/api/game-forge/games/{game_id}/live/sources/{source_adapter_id}/promote-version")
def promote_game_live_version(game_id: str, source_adapter_id: str, body: PromoteLiveVersionRequest, request: Request):
    _creator(request)
    correlation_id = _correlation_id(request)
    game = _game(game_id)
    state = _load_state(game.id)
    source = _owned_source(state, source_adapter_id)
    if source.revoked:
        raise _error(409, "live_source_privacy_blocked", "Revoked LIVE sources cannot be promoted", correlation_id)
    if body.expected_project_version != game.version:
        raise _error(409, "stale_project_version", "The working Game Forge project changed before LIVE version promotion", correlation_id)
    build = _required_build(game, source.source_type)
    if body.expected_build_id is not None:
        expected = _opaque(body.expected_build_id, field_name="expected_build_id", required=True)
        actual = build.build_id if build else None
        if expected != actual:
            raise _error(409, "stale_project_version", "The requested LIVE build is no longer current", correlation_id)
    source.project_version = game.version
    source.build_id = build.build_id if build else None
    source.rights_readiness = "verified" if game.rights_confirmed else "unverified"
    source.updated_at = _now()
    source.correlation_id = correlation_id
    _save_state(state)
    return {
        **_source_payload(source),
        "explicit_promotion": True,
        "working_project_auto_switched_viewers": False,
    }


@router.post("/api/game-forge/games/{game_id}/live/sources/{source_adapter_id}/emergency-hide")
def emergency_hide_game_live_source(game_id: str, source_adapter_id: str, body: EmergencyHideRequest, request: Request):
    _creator(request)
    _game(game_id)
    state = _load_state(game_id)
    source = _owned_source(state, source_adapter_id)
    source.presentation_mode = "brb"
    source.status = "revoked" if body.revoke else "hidden"
    source.revoked = bool(body.revoke)
    source.health = "revoked" if body.revoke else "ready"
    source.updated_at = _now()
    source.correlation_id = _correlation_id(request)
    _save_state(state)
    return {
        **_source_payload(source),
        "brb_requested": True,
        "project_deleted": False,
        "autosave_terminated": False,
        "playtest_build_deleted": False,
    }


@router.delete("/api/game-forge/games/{game_id}/live/sources/{source_adapter_id}")
def detach_game_live_source(game_id: str, source_adapter_id: str, request: Request):
    _creator(request)
    _game(game_id)
    state = _load_state(game_id)
    source = _owned_source(state, source_adapter_id)
    if not source.revoked:
        source.status = "detached"
        source.health = "not_ready"
    source.updated_at = _now()
    source.correlation_id = _correlation_id(request)
    _save_state(state)
    return {**_source_payload(source), "detached": True, "idempotent": True}


@router.post("/api/game-forge/games/{game_id}/live/feedback")
def create_game_live_feedback(game_id: str, body: CreateLiveFeedbackRequest, request: Request):
    _creator(request)
    correlation_id = _correlation_id(request)
    _game(game_id)
    state = _load_state(game_id)
    source = _owned_source(state, body.source_adapter_id)
    live_session = _opaque(body.live_session_id, field_name="live_session_id", required=True)
    if source.live_session_id != live_session:
        raise _error(409, "live_source_not_ready", "Feedback LIVE session does not match the Game Forge source", correlation_id)
    if not body.creator_promoted and not body.structured_playtest:
        raise _error(409, "live_source_privacy_blocked", "Casual viewer chat is not a Game Forge issue. Promote it explicitly or use structured playtest mode", correlation_id)
    author_ref = _opaque(body.author_ref, field_name="author_ref")
    clip_ref = _opaque(body.clip_ref, field_name="clip_ref")
    item = GameForgeLiveFeedback(
        project_id=game_id,
        build_id=source.build_id,
        project_version=source.project_version,
        live_session_id=live_session,
        source_adapter_id=source.source_adapter_id,
        author_ref=author_ref,
        live_time_seconds=body.live_time_seconds,
        category=body.category,
        text=body.text,
        clip_ref=clip_ref,
        correlation_id=correlation_id,
    )
    state.feedback[item.feedback_id] = item
    _save_state(state)
    return {"feedback": item.model_dump(mode="json"), "game_mutated": False, "chat_auto_promoted": False}


@router.post("/api/game-forge/games/{game_id}/live/returns")
def register_game_live_return(game_id: str, body: CreateLiveReturnRequest, request: Request):
    _creator(request)
    correlation_id = _correlation_id(request)
    _game(game_id)
    state = _load_state(game_id)
    source = _owned_source(state, body.source_adapter_id)
    live_session = _opaque(body.live_session_id, field_name="live_session_id", required=True)
    if source.live_session_id != live_session:
        raise _error(409, "live_source_not_ready", "Returned LIVE asset session does not match the Game Forge source", correlation_id)
    asset_ref = _opaque(body.asset_ref, field_name="asset_ref", required=True)
    if body.end_seconds is not None and body.start_seconds is not None and body.end_seconds < body.start_seconds:
        raise _error(400, "live_source_not_ready", "Returned LIVE asset end time cannot be before its start time", correlation_id)
    supplied_key = _opaque(body.idempotency_key, field_name="idempotency_key")
    key_material = supplied_key or f"{game_id}|{live_session}|{source.source_adapter_id}|{asset_ref}|{body.asset_type}|{body.start_seconds}|{body.end_seconds}"
    key = f"gfr_{hashlib.sha256(key_material.encode('utf-8')).hexdigest()[:28]}"
    existing = state.returns.get(key)
    if existing is not None:
        return {"return": existing.model_dump(mode="json"), "idempotent_replay": True}
    item = GameForgeLiveReturnRecord(
        return_id=f"gf_return_{uuid4().hex}",
        idempotency_key=key,
        project_id=game_id,
        build_id=source.build_id,
        project_version=source.project_version,
        live_session_id=live_session,
        source_adapter_id=source.source_adapter_id,
        asset_ref=asset_ref,
        asset_type=body.asset_type,
        start_seconds=body.start_seconds,
        end_seconds=body.end_seconds,
        processing_state=body.processing_state,
        correlation_id=correlation_id,
    )
    state.returns[key] = item
    _save_state(state)
    return {"return": item.model_dump(mode="json"), "idempotent_replay": False}


def revoke_project_live_sources(game_id: str, *, reason: str = "project_permission_revoked") -> int:
    """Compatibility hook for Chat 1/auth lifecycle; caller must already have authoritative permission context."""
    state = _load_state(game_id)
    changed = 0
    for source in state.sources.values():
        if source.revoked:
            continue
        source.revoked = True
        source.status = "revoked"
        source.health = "revoked"
        source.presentation_mode = "brb"
        source.updated_at = _now()
        source.correlation_id = f"{reason[:80]}_{uuid4().hex[:12]}"
        changed += 1
    if changed:
        _save_state(state)
    return changed


@router.get("/game-creation/live/{game_id}", response_class=HTMLResponse, include_in_schema=False)
def game_live_portal(game_id: str, request: Request):
    _creator(request)
    game = _game(game_id)
    state = _load_state(game.id)
    current = next((row for row in reversed(list(state.sources.values())) if not row.revoked), None)
    nonce = token_urlsafe(18)
    safe_title = escape(game.title)
    game_id_json = json.dumps(game.id)
    source_id_json = json.dumps(current.source_adapter_id if current else "")
    script = f"""
const gameId={game_id_json}; let sourceId={source_id_json};
const out=document.getElementById('status');
async function call(path, method='POST', body={{}}){{
  const r=await fetch(path,{{method,headers:{{'Content-Type':'application/json'}},body:method==='GET'?undefined:JSON.stringify(body)}});
  const data=await r.json().catch(()=>({{}})); if(!r.ok) throw new Error(data?.detail?.message||data?.detail||'Request failed'); return data;
}}
function show(data){{out.textContent=JSON.stringify(data,null,2); if(data?.source?.source_adapter_id) sourceId=data.source.source_adapter_id;}}
document.getElementById('attach').onclick=async()=>{{try{{show(await call(`/api/game-forge/games/${{gameId}}/live/sources`,'POST',{{live_session_id:'ui_'+Date.now(),source_type:'clean_game_output',audience_visibility:'private',presentation_mode:'playtest'}}));}}catch(e){{out.textContent=e.message;}}}};
for(const [id,mode] of [['playtest','playtest'],['showcase','launch_showcase'],['brb','brb']]) document.getElementById(id).onclick=async()=>{{if(!sourceId) return out.textContent='Attach a safe source first.'; try{{show(await call(`/api/game-forge/games/${{gameId}}/live/sources/${{sourceId}}/presentation`,'PATCH',{{presentation_mode:mode}}));}}catch(e){{out.textContent=e.message;}}}};
document.getElementById('hide').onclick=async()=>{{if(!sourceId) return out.textContent='Attach a safe source first.'; try{{show(await call(`/api/game-forge/games/${{gameId}}/live/sources/${{sourceId}}/emergency-hide`,'POST',{{revoke:false}}));}}catch(e){{out.textContent=e.message;}}}};
"""
    html = f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>{safe_title} — Go Live &amp; Create</title><style>body{{margin:0;background:#070a12;color:#f5f7ff;font-family:system-ui;padding:24px}}main{{max-width:960px;margin:auto}}.card{{border:1px solid #ffffff24;border-radius:16px;padding:18px;background:#111726;margin:14px 0}}button,a{{border:1px solid #ffffff35;border-radius:10px;background:#18243a;color:white;padding:10px 13px;margin:4px;text-decoration:none;font-weight:700}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#05070d;padding:12px;border-radius:10px}}small{{color:#bbc4d8}}</style></head><body><main><h1>Game Forge — Go Live &amp; Create</h1><p><b>{safe_title}</b></p><div class='card'><p>Safe default: clean game/playtest output. Private code, credentials, admin panels, monetisation settings and unrelated windows are excluded by contract.</p><button id='attach'>Attach Clean Game Source</button><button id='playtest'>Playtest</button><button id='showcase'>Launch / Showcase</button><button id='brb'>BRB</button><button id='hide'>Emergency Hide</button><a href='/game-creation/play/{escape(game.id, quote=True)}'>Open Playtest</a></div><div class='card'><small>Shared Sky transport and programme composition remain owned by their canonical services. This panel creates and controls the project-safe Game Forge source adapter.</small><pre id='status'>No unsafe whole-window capture is used.</pre></div></main><script nonce='{nonce}'>{script}</script></body></html>"""
    return HTMLResponse(
        html,
        headers={
            "Cache-Control": "no-store",
            "X-Robots-Tag": "noindex, nofollow",
            "Content-Security-Policy": f"default-src 'self'; script-src 'nonce-{nonce}'; connect-src 'self'; style-src 'unsafe-inline'; frame-ancestors 'self'; object-src 'none'; base-uri 'none'",
        },
    )


__all__ = [
    "AttachLiveSourceRequest",
    "CreateLiveFeedbackRequest",
    "CreateLiveReturnRequest",
    "EmergencyHideRequest",
    "GameForgeLiveFeedback",
    "GameForgeLiveReturnRecord",
    "GameForgeLiveState",
    "GameForgeSafeLiveSource",
    "LIVE_PRIVACY_EXCLUSIONS",
    "LIVE_SCHEMA_VERSION",
    "PromoteLiveVersionRequest",
    "TransitionLiveSourceRequest",
    "attach_game_live_source",
    "game_live_state",
    "register_game_live_return",
    "revoke_project_live_sources",
    "router",
    "shared_sky_compatibility_payload",
]
