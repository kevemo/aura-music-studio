from __future__ import annotations

import hashlib
import json
import mimetypes
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field, model_validator
from starlette.middleware.base import BaseHTTPMiddleware

from .creative_project import CreativeProjectStore
from .models import ProjectManifest
from .project import ProjectWorkspace
from .rights import RightsLedger
from .shared_sky_streaming_studios import shared_sky
from .tenant_storage import project_path

router = APIRouter(prefix="/creation-live", tags=["Creation Studios Go Live & Create"])

StudioType = Literal["music", "video_cinema", "image_visual"]
MediaKind = Literal["audio", "video", "audiovisual", "still-or-slideshow", "data-overlay"]
PresentationMode = Literal[
    "creating", "tutorial", "review", "rehearsal", "performance", "premiere",
    "showcase", "gallery", "listening_party", "brb", "detached",
]
RightsState = Literal["ready", "warning", "blocked", "unknown"]
PrivacyClass = Literal["project_safe_output", "approved_snapshot", "advanced_workspace"]

_SOURCE_TYPES: dict[StudioType, set[str]] = {
    "music": {"clean_music_output", "selected_stem", "lyrics_view", "chords_view", "music_visualiser", "full_workspace"},
    "video_cinema": {"clean_video_output", "selected_viewer", "caption_presentation", "before_after", "full_workspace"},
    "image_visual": {"clean_artwork", "selected_canvas", "before_after", "gallery", "full_workspace"},
}
_PRESETS: dict[StudioType, list[dict[str, Any]]] = {
    "music": [
        {"id": "creator_studio", "label": "Creator + Studio", "mode": "creating"},
        {"id": "studio_only", "label": "Studio Only", "mode": "creating"},
        {"id": "performance", "label": "Performance", "mode": "performance"},
        {"id": "tutorial", "label": "Tutorial", "mode": "tutorial"},
        {"id": "listening_party", "label": "Listening Party / Premiere", "mode": "listening_party"},
        {"id": "custom", "label": "Custom", "mode": "creating"},
    ],
    "video_cinema": [
        {"id": "creator_canvas", "label": "Creator + Canvas", "mode": "creating"},
        {"id": "canvas_only", "label": "Canvas Only", "mode": "creating"},
        {"id": "editing_tutorial", "label": "Editing Tutorial", "mode": "tutorial"},
        {"id": "review_session", "label": "Review Session", "mode": "review"},
        {"id": "premiere", "label": "Premiere", "mode": "premiere"},
        {"id": "creator_chat", "label": "Creator + Chat", "mode": "creating"},
        {"id": "custom", "label": "Custom", "mode": "creating"},
    ],
    "image_visual": [
        {"id": "creator_canvas", "label": "Creator + Canvas", "mode": "creating"},
        {"id": "canvas_only", "label": "Canvas Only", "mode": "creating"},
        {"id": "design_tutorial", "label": "Design Tutorial", "mode": "tutorial"},
        {"id": "art_showcase", "label": "Art Showcase", "mode": "showcase"},
        {"id": "before_after", "label": "Before & After", "mode": "review"},
        {"id": "creator_chat", "label": "Creator + Chat", "mode": "creating"},
        {"id": "custom", "label": "Custom", "mode": "creating"},
    ],
}

_PRIVATE_KEYS = {
    "api_key", "apikey", "access_token", "refresh_token", "oauth_token", "stream_key", "password",
    "secret", "client_secret", "private_key", "credential", "credentials", "storage_url", "filesystem_path",
    "server_path", "provider_payload", "training_data", "training_files", "collaborator_email", "billing",
}
_PRIVATE_FLAGS = {"private", "hidden", "restricted", "collaborator_only", "internal_only"}
_MEDIA_EXTENSIONS = {
    ".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg",
    ".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v",
    ".png", ".jpg", ".jpeg", ".webp", ".avif", ".gif",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Membership context unavailable")
    return member


def _user_id(member: Any) -> str:
    value = str(getattr(member, "user_id", "") or "").strip()
    if not value:
        raise HTTPException(401, "Member identity unavailable")
    return value


def _project(project_name: str) -> Path:
    try:
        return project_path(project_name, must_exist=True)
    except ValueError as exc:
        raise HTTPException(400, "Invalid project path") from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, "Project not found") from exc


def _safe_label(value: str, fallback: str) -> str:
    clean = " ".join(str(value or "").replace("\x00", " ").split()).strip()
    return (clean or fallback)[:160]


def _contains_private_metadata(value: Any, *, path: str = "") -> list[str]:
    findings: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            name = str(key).strip().lower()
            here = f"{path}.{name}" if path else name
            if name in _PRIVATE_KEYS or any(token in name for token in ("token", "password", "secret", "credential")):
                findings.append(here)
                continue
            findings.extend(_contains_private_metadata(item, path=here))
    elif isinstance(value, list):
        for index, item in enumerate(value[:100]):
            findings.extend(_contains_private_metadata(item, path=f"{path}[{index}]"))
    return findings


def _adapter_id(user_id: str, project_name: str, studio_type: StudioType, source_key: str) -> str:
    digest = hashlib.sha256(f"{user_id}\x00{project_name}\x00{studio_type}\x00{source_key}".encode()).hexdigest()[:28]
    return f"cls_{digest}"


class SourceCapabilities(BaseModel):
    audio: bool = False
    video: bool = False
    still: bool = False
    camera_optional: bool = True
    microphone_optional: bool = True
    version_pin: bool = False
    full_workspace: bool = False


class RightsPreflight(BaseModel):
    state: RightsState
    codes: list[str] = Field(default_factory=list)
    messages: list[str] = Field(default_factory=list)
    requires_confirmation: bool = False


class CreationLiveSourceDescriptor(BaseModel):
    schema_version: int = 1
    source_adapter_id: str
    adapter_version: int = 1
    studio_type: StudioType
    project_id: str
    workspace_id: str
    creator_id: str
    source_type: str
    safe_display_name: str
    media_kind: MediaKind
    aspect_ratio: str | None = None
    frame_rate: float | None = None
    sample_rate: int | None = None
    channels: int | None = None
    capabilities: SourceCapabilities
    privacy_classification: PrivacyClass
    inclusion_manifest: list[str] = Field(default_factory=list)
    exclusion_policy: str = "creation-live-default-v1"
    rights: RightsPreflight
    live_source_registration_state: str = "not_registered"
    shared_sky_project_id: str | None = None
    shared_sky_broadcast_id: str | None = None
    shared_sky_source_id: str | None = None
    presentation_mode: PresentationMode = "creating"
    health: str = "available"
    version: int = 1
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)
    expires_at: str | None = None
    revoked_at: str | None = None
    correlation_id: str = Field(default_factory=lambda: f"corr_{uuid4().hex}")
    preview_kind: Literal["media", "browser_surface", "none"] = "none"
    element_id: str | None = None
    public_version_id: str | None = None

    @model_validator(mode="after")
    def reject_secret_bearing_descriptor(self):
        findings = _contains_private_metadata(self.model_dump(mode="python"))
        if findings:
            raise ValueError("Source descriptor contains prohibited private metadata")
        if self.source_type not in _SOURCE_TYPES[self.studio_type]:
            raise ValueError("Source type is not valid for this studio")
        return self


class AttachRequest(BaseModel):
    shared_sky_project_id: str = Field(min_length=1, max_length=160)
    broadcast_id: str | None = Field(default=None, max_length=160)
    editor_instance_id: str = Field(min_length=6, max_length=160)
    idempotency_key: str = Field(min_length=8, max_length=180)
    expected_version: int | None = Field(default=None, ge=1)
    rights_warning_confirmed: bool = False
    full_workspace_confirmed: bool = False


class TransitionRequest(BaseModel):
    mode: PresentationMode
    expected_version: int = Field(ge=1)
    editor_instance_id: str = Field(min_length=6, max_length=160)


class DetachRequest(BaseModel):
    expected_version: int | None = Field(default=None, ge=1)
    editor_instance_id: str = Field(min_length=6, max_length=160)
    idempotency_key: str = Field(min_length=8, max_length=180)
    reason: str = Field(default="creator_detach", max_length=120)


class ReturnAssetRequest(BaseModel):
    return_import_id: str = Field(min_length=1, max_length=180)
    studio_type: StudioType
    live_session_id: str = Field(min_length=1, max_length=180)
    recording_id: str = Field(min_length=1, max_length=180)
    highlight_id: str | None = Field(default=None, max_length=180)
    source_adapter_id: str = Field(min_length=1, max_length=180)
    asset_kind: Literal["recording", "highlight", "clip", "still"]
    processing_state: Literal["processing", "ready", "incomplete", "failed", "recovered"]
    asset_id: str | None = Field(default=None, max_length=180)
    source_element_id: str | None = Field(default=None, max_length=180)
    timestamp_start_ms: int | None = Field(default=None, ge=0)
    timestamp_end_ms: int | None = Field(default=None, ge=0)
    visibility: Literal["promo", "BTS", "tutorial", "showcase", "other"] = "other"
    correlation_id: str = Field(default_factory=lambda: f"corr_{uuid4().hex}")


class MarkerRequest(BaseModel):
    source_adapter_id: str = Field(min_length=1, max_length=180)
    live_session_id: str = Field(min_length=1, max_length=180)
    label: str = Field(min_length=1, max_length=120)
    kind: Literal["vocal_take", "chorus", "reveal", "grade", "poster", "reaction", "tutorial_tip", "other"] = "other"
    live_time_ms: int = Field(ge=0)


class CreationLiveStore:
    def __init__(self, db_path: str | Path | None = None):
        self.db_path = str(db_path or shared_sky.db_path)
        self._schema()

    def connect(self):
        con = sqlite3.connect(self.db_path, timeout=15)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _schema(self):
        with self.connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS creation_live_sources(
                    source_adapter_id TEXT PRIMARY KEY,user_id TEXT NOT NULL,project_name TEXT NOT NULL,studio_type TEXT NOT NULL,
                    source_key TEXT NOT NULL,server_ref TEXT NOT NULL DEFAULT '',descriptor_json TEXT NOT NULL,source_status TEXT NOT NULL,
                    shared_sky_project_id TEXT,broadcast_id TEXT,transport_source_id TEXT,active_editor_instance_id TEXT,
                    version INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,revoked_at TEXT,
                    UNIQUE(user_id,project_name,studio_type,source_key)
                );
                CREATE INDEX IF NOT EXISTS idx_creation_live_owner ON creation_live_sources(user_id,project_name,updated_at DESC);
                CREATE TABLE IF NOT EXISTS creation_live_idempotency(
                    user_id TEXT NOT NULL,operation TEXT NOT NULL,idempotency_key TEXT NOT NULL,source_adapter_id TEXT NOT NULL,
                    request_hash TEXT NOT NULL,response_json TEXT NOT NULL,created_at TEXT NOT NULL,
                    PRIMARY KEY(user_id,operation,idempotency_key)
                );
                CREATE TABLE IF NOT EXISTS creation_live_returns(
                    user_id TEXT NOT NULL,project_name TEXT NOT NULL,return_import_id TEXT NOT NULL,payload_json TEXT NOT NULL,
                    imported_element_id TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
                    PRIMARY KEY(user_id,project_name,return_import_id)
                );
                CREATE TABLE IF NOT EXISTS creation_live_markers(
                    id TEXT PRIMARY KEY,user_id TEXT NOT NULL,project_name TEXT NOT NULL,source_adapter_id TEXT NOT NULL,
                    live_session_id TEXT NOT NULL,label TEXT NOT NULL,kind TEXT NOT NULL,live_time_ms INTEGER NOT NULL,
                    correlation_id TEXT NOT NULL,created_at TEXT NOT NULL
                );
                """
            )

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        item = dict(row)
        item["descriptor"] = json.loads(item.pop("descriptor_json"))
        return item

    def get(self, user_id: str, source_adapter_id: str) -> dict:
        with self.connect() as con:
            row = con.execute(
                "SELECT * FROM creation_live_sources WHERE source_adapter_id=? AND user_id=?",
                (source_adapter_id, user_id),
            ).fetchone()
        item = self._row(row)
        if not item:
            raise KeyError(source_adapter_id)
        return item

    def upsert_discovered(self, user_id: str, descriptor: CreationLiveSourceDescriptor, source_key: str, server_ref: str = "") -> dict:
        payload = descriptor.model_dump(mode="json")
        with self.connect() as con:
            existing = con.execute(
                "SELECT version,created_at,source_status,shared_sky_project_id,broadcast_id,transport_source_id,active_editor_instance_id,revoked_at FROM creation_live_sources WHERE source_adapter_id=? AND user_id=?",
                (descriptor.source_adapter_id, user_id),
            ).fetchone()
            if existing:
                current = dict(existing)
                payload["version"] = int(current["version"])
                payload["created_at"] = current["created_at"]
                payload["live_source_registration_state"] = current["source_status"]
                payload["shared_sky_project_id"] = current["shared_sky_project_id"]
                payload["shared_sky_broadcast_id"] = current["broadcast_id"]
                payload["shared_sky_source_id"] = current["transport_source_id"]
                payload["revoked_at"] = current["revoked_at"]
                con.execute(
                    "UPDATE creation_live_sources SET server_ref=?,descriptor_json=?,updated_at=? WHERE source_adapter_id=? AND user_id=?",
                    (server_ref, json.dumps(payload, separators=(",", ":")), _now(), descriptor.source_adapter_id, user_id),
                )
            else:
                con.execute(
                    "INSERT INTO creation_live_sources(source_adapter_id,user_id,project_name,studio_type,source_key,server_ref,descriptor_json,source_status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (descriptor.source_adapter_id,user_id,descriptor.project_id,descriptor.studio_type,source_key,server_ref,json.dumps(payload,separators=(",", ":")),"discovered",descriptor.created_at,descriptor.updated_at),
                )
        return self.get(user_id, descriptor.source_adapter_id)

    def mutate(self, user_id: str, source_adapter_id: str, *, expected_version: int | None, editor_instance_id: str | None, **changes) -> dict:
        item = self.get(user_id, source_adapter_id)
        if expected_version is not None and item["version"] != expected_version:
            raise RuntimeError("stale_source_version")
        owner = item.get("active_editor_instance_id")
        if owner and editor_instance_id and owner != editor_instance_id and item["source_status"] not in {"detached", "revoked"}:
            raise RuntimeError("source_controlled_by_another_editor")
        descriptor = dict(item["descriptor"])
        status = str(changes.pop("source_status", item["source_status"]))
        version = item["version"] + 1
        stamp = _now()
        descriptor.update(changes.pop("descriptor_changes", {}))
        descriptor["version"] = version
        descriptor["updated_at"] = stamp
        descriptor["live_source_registration_state"] = status
        shared_project = changes.pop("shared_sky_project_id", item.get("shared_sky_project_id"))
        broadcast_id = changes.pop("broadcast_id", item.get("broadcast_id"))
        transport_source_id = changes.pop("transport_source_id", item.get("transport_source_id"))
        active_editor = changes.pop("active_editor_instance_id", editor_instance_id or item.get("active_editor_instance_id"))
        revoked_at = changes.pop("revoked_at", item.get("revoked_at"))
        descriptor["shared_sky_project_id"] = shared_project
        descriptor["shared_sky_broadcast_id"] = broadcast_id
        descriptor["shared_sky_source_id"] = transport_source_id
        descriptor["revoked_at"] = revoked_at
        with self.connect() as con:
            result = con.execute(
                "UPDATE creation_live_sources SET descriptor_json=?,source_status=?,shared_sky_project_id=?,broadcast_id=?,transport_source_id=?,active_editor_instance_id=?,version=?,updated_at=?,revoked_at=? WHERE source_adapter_id=? AND user_id=? AND version=?",
                (json.dumps(descriptor,separators=(",", ":")),status,shared_project,broadcast_id,transport_source_id,active_editor,version,stamp,revoked_at,source_adapter_id,user_id,item["version"]),
            )
            if result.rowcount != 1:
                raise RuntimeError("stale_source_version")
        return self.get(user_id, source_adapter_id)

    def idempotent(self, user_id: str, operation: str, key: str, source_adapter_id: str, request: dict, execute):
        request_hash = hashlib.sha256(json.dumps(request, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        with self.connect() as con:
            row = con.execute(
                "SELECT request_hash,response_json FROM creation_live_idempotency WHERE user_id=? AND operation=? AND idempotency_key=?",
                (user_id, operation, key),
            ).fetchone()
        if row:
            if row["request_hash"] != request_hash:
                raise RuntimeError("idempotency_key_reused_with_different_request")
            return json.loads(row["response_json"])
        result = execute()
        with self.connect() as con:
            try:
                con.execute(
                    "INSERT INTO creation_live_idempotency VALUES(?,?,?,?,?,?,?)",
                    (user_id,operation,key,source_adapter_id,request_hash,json.dumps(result,separators=(",", ":")),_now()),
                )
            except sqlite3.IntegrityError:
                row = con.execute(
                    "SELECT request_hash,response_json FROM creation_live_idempotency WHERE user_id=? AND operation=? AND idempotency_key=?",
                    (user_id, operation, key),
                ).fetchone()
                if not row or row["request_hash"] != request_hash:
                    raise RuntimeError("idempotency_conflict")
                return json.loads(row["response_json"])
        return result


creation_live_store = CreationLiveStore()


def _creative_manifest(project: Path):
    store = CreativeProjectStore(project)
    return store.load() if store.exists() else None


def _legacy_music_manifest(project: Path) -> ProjectManifest | None:
    try:
        return ProjectWorkspace(project).load_manifest()
    except (FileNotFoundError, ValueError):
        return None


def _voice_preflight(project: Path, metadata: dict[str, Any]) -> tuple[list[str], list[str]]:
    codes: list[str] = []
    messages: list[str] = []
    profile_id = str(metadata.get("voice_profile_id") or "").strip()
    if not profile_id:
        return codes, messages
    ledger = RightsLedger(project / ".aura_rights")
    try:
        profile = ledger.get_voice(profile_id)
        profile.assert_usable("live_streaming")
    except KeyError:
        codes.append("voice_profile_missing")
        messages.append("The referenced voice profile no longer exists.")
    except PermissionError:
        codes.append("likeness_or_voice_not_authorised_for_live")
        messages.append("This voice profile is not currently authorised for public LIVE output.")
    return codes, messages


def _rights_for(project: Path, studio_type: StudioType, *, metadata: dict[str, Any] | None = None, legacy_music: ProjectManifest | None = None, advanced_workspace: bool = False) -> RightsPreflight:
    metadata = metadata or {}
    blocked: list[str] = []
    warnings: list[str] = []
    messages: list[str] = []
    for flag in _PRIVATE_FLAGS:
        if metadata.get(flag) is True:
            blocked.append("private_asset_not_eligible")
            messages.append("This source is marked private, hidden or restricted.")
            break
    if metadata.get("broadcast_allowed") is False:
        blocked.append("private_asset_not_eligible")
        messages.append("Project metadata explicitly blocks broadcast use.")
    if metadata.get("likeness_live_allowed") is False or (metadata.get("real_person_likeness") and not metadata.get("likeness_consent")):
        blocked.append("likeness_or_voice_not_authorised_for_live")
        messages.append("Real-person likeness permission does not allow LIVE output.")
    voice_codes, voice_messages = _voice_preflight(project, metadata)
    blocked.extend(voice_codes)
    messages.extend(voice_messages)
    if _contains_private_metadata(metadata):
        blocked.append("private_metadata_detected")
        messages.append("Private provider/security metadata was detected and this source is blocked until removed from the presentation path.")
    if legacy_music and legacy_music.mode in {"cover", "remix", "backing_track"} and not legacy_music.rights_confirmed:
        blocked.append("project_rights_blocked")
        messages.append("This music project requires confirmed rights before public broadcast.")
    if blocked:
        return RightsPreflight(state="blocked", codes=sorted(set(blocked)), messages=messages, requires_confirmation=False)
    if advanced_workspace:
        warnings.append("advanced_workspace_privacy_warning")
        messages.append("Whole-workspace capture can expose unrelated notifications or private UI. Browser capture permissions cannot guarantee masking outside the selected application surface.")
    if not metadata.get("rights_record_id") and not (legacy_music and (legacy_music.mode == "original" or legacy_music.rights_confirmed)):
        warnings.append("rights_unverified")
        messages.append("No complete broadcast-rights record was found. Confirm you are authorised before attaching this source.")
    if warnings:
        return RightsPreflight(state="warning", codes=warnings, messages=messages, requires_confirmation=True)
    return RightsPreflight(state="ready", codes=["rights_metadata_ready"], messages=["No broadcast-rights blocker was found in the available project metadata."])


def _descriptor(*, user_id: str, project_name: str, studio_type: StudioType, source_type: str, source_key: str, label: str, media_kind: MediaKind, rights: RightsPreflight, privacy: PrivacyClass = "project_safe_output", inclusion: list[str] | None = None, capabilities: SourceCapabilities | None = None, element_id: str | None = None, public_version_id: str | None = None, aspect_ratio: str | None = None, frame_rate: float | None = None, sample_rate: int | None = None, channels: int | None = None, preview_kind: Literal["media", "browser_surface", "none"] = "media") -> CreationLiveSourceDescriptor:
    return CreationLiveSourceDescriptor(
        source_adapter_id=_adapter_id(user_id, project_name, studio_type, source_key), studio_type=studio_type,
        project_id=project_name, workspace_id=user_id, creator_id=user_id, source_type=source_type,
        safe_display_name=_safe_label(label, "Project source"), media_kind=media_kind, aspect_ratio=aspect_ratio,
        frame_rate=frame_rate, sample_rate=sample_rate, channels=channels, capabilities=capabilities or SourceCapabilities(),
        privacy_classification=privacy, inclusion_manifest=inclusion or [], rights=rights, element_id=element_id,
        public_version_id=public_version_id, preview_kind=preview_kind,
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=12)).isoformat(),
    )


def discover_sources(user_id: str, project_name: str, studio_type: StudioType) -> list[dict]:
    project = project_path(project_name, must_exist=True)
    rows: list[tuple[CreationLiveSourceDescriptor, str, str]] = []
    creative = _creative_manifest(project)
    legacy = _legacy_music_manifest(project) if studio_type == "music" else None
    if creative:
        active = set(creative.active_element_ids)
        wanted_kind = {"video"} if studio_type == "video_cinema" else ({"image"} if studio_type == "image_visual" else {"music", "audio"})
        for element in creative.elements:
            if element.kind not in wanted_kind or element.status != "ready" or not element.source_ref or element.id not in active:
                continue
            ref = Path(str(element.source_ref))
            if ref.is_absolute():
                continue
            target = (project / ref).resolve()
            if project.resolve() not in target.parents or not target.is_file() or target.suffix.lower() not in _MEDIA_EXTENSIONS:
                continue
            meta = dict(element.metadata or {})
            rights = _rights_for(project, studio_type, metadata=meta, legacy_music=legacy)
            source_type = "clean_video_output" if studio_type == "video_cinema" else ("clean_artwork" if studio_type == "image_visual" else "clean_music_output")
            kind: MediaKind = "audiovisual" if studio_type == "video_cinema" else ("still-or-slideshow" if studio_type == "image_visual" else "audio")
            cap = SourceCapabilities(audio=studio_type in {"music", "video_cinema"}, video=studio_type == "video_cinema", still=studio_type == "image_visual", version_pin=True)
            desc = _descriptor(user_id=user_id,project_name=project_name,studio_type=studio_type,source_type=source_type,source_key=f"element:{element.id}",label=element.label,media_kind=kind,rights=rights,inclusion=[f"creative_element:{element.id}"],capabilities=cap,element_id=element.id,public_version_id=str(meta.get("version_root_id") or element.id),aspect_ratio=("16:9" if studio_type=="video_cinema" else None),frame_rate=float(meta["fps"]) if isinstance(meta.get("fps"),(int,float)) else None,sample_rate=int(meta["sample_rate"]) if isinstance(meta.get("sample_rate"),int) else None,channels=int(meta["channels"]) if isinstance(meta.get("channels"),int) else None)
            rows.append((desc, f"element:{element.id}", str(ref)))
    if studio_type == "music":
        output = project / "output"
        if output.is_dir():
            for target in sorted(output.rglob("*")):
                if not target.is_file() or target.suffix.lower() not in {".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg"}:
                    continue
                relative = target.relative_to(project)
                if any(part.lower() in {"private", "reference", "training", "models"} for part in relative.parts):
                    continue
                source_type = "selected_stem" if "stem" in {part.lower() for part in relative.parts} else "clean_music_output"
                rights = _rights_for(project, studio_type, legacy_music=legacy)
                if source_type == "selected_stem" and rights.state == "ready":
                    rights = RightsPreflight(state="warning", codes=["selected_stem_confirmation"], messages=["A stem is only broadcast when explicitly selected; confirm this stem is eligible for public output."], requires_confirmation=True)
                key = f"file:{relative.as_posix()}"
                desc = _descriptor(user_id=user_id,project_name=project_name,studio_type=studio_type,source_type=source_type,source_key=key,label=("Selected stem · " if source_type=="selected_stem" else "Music output · ")+target.stem,media_kind="audio",rights=rights,inclusion=["authorised project audio output"],capabilities=SourceCapabilities(audio=True),preview_kind="media")
                rows.append((desc,key,relative.as_posix()))
    advanced = _descriptor(user_id=user_id,project_name=project_name,studio_type=studio_type,source_type="full_workspace",source_key="advanced:workspace",label="Full workspace (advanced/high risk)",media_kind="audiovisual",rights=_rights_for(project,studio_type,advanced_workspace=True),privacy="advanced_workspace",inclusion=["browser-selected workspace surface"],capabilities=SourceCapabilities(audio=True,video=True,full_workspace=True),preview_kind="browser_surface")
    rows.append((advanced,"advanced:workspace",""))
    out: list[dict] = []
    for desc, key, server_ref in rows:
        stored = creation_live_store.upsert_discovered(user_id, desc, key, server_ref)
        out.append(stored["descriptor"])
    return out


def _transport_register(user_id: str, shared_sky_project_id: str, descriptor: dict, existing_source_id: str | None) -> dict:
    try:
        from .shared_sky_transport_domain import transport
    except ImportError:
        return {"available": False, "state": "compatibility_pending", "reason": "Chat 2 transport source registry is not merged"}
    if existing_source_id:
        try:
            current = transport.source(user_id, existing_source_id)
            if current.get("project_id") == shared_sky_project_id:
                return {"available": True, "state": current.get("state", "ready"), "source": current, "reused": True}
        except (KeyError, ValueError):
            pass
    mapping = {"music": "music_project", "video_cinema": "video_project", "image_visual": "video_project"}
    result = transport.register_source(
        user_id, shared_sky_project_id, mapping[descriptor["studio_type"]],
        f"creation-live://{descriptor['source_adapter_id']}", state="ready",
        capabilities={"audio": descriptor["capabilities"]["audio"], "video": descriptor["capabilities"]["video"] or descriptor["capabilities"]["still"], "privacy": descriptor["privacy_classification"], "schema_version": descriptor["schema_version"]},
    )
    return {"available": True, "state": result.get("state", "ready"), "source": result, "reused": False}


def _programme_truth(user_id: str, item: dict) -> dict:
    broadcast_id = item.get("broadcast_id")
    if not broadcast_id:
        return {"session_state": "not_selected", "on_air": False, "programme_state": "unknown"}
    try:
        broadcast = shared_sky.broadcast(user_id, broadcast_id)
    except KeyError:
        return {"session_state": "ended_or_unavailable", "on_air": False, "programme_state": "unknown"}
    programme = "unknown"
    on_air = False
    try:
        from .shared_sky_control_room import StudioRepository
        repo = StudioRepository()
        checker = getattr(repo, "source_programme_state", None)
        if callable(checker):
            state = checker(user_id, broadcast_id, item["source_adapter_id"])
            programme = str(state.get("state") or "unknown")
            on_air = bool(state.get("on_air"))
    except (ImportError, AttributeError, TypeError, KeyError, ValueError):
        pass
    return {"session_state": broadcast.get("state", "unknown"), "on_air": on_air, "programme_state": programme}


def _api_error(exc: Exception):
    code = str(exc)
    if code == "stale_source_version":
        raise HTTPException(409, {"code": code, "message": "This source changed in another tab. Refresh live state before retrying."}) from exc
    if code == "source_controlled_by_another_editor":
        raise HTTPException(409, {"code": code, "message": "A newer editor instance currently controls this live source."}) from exc
    if code.startswith("idempotency"):
        raise HTTPException(409, {"code": code, "message": "The idempotency key cannot be reused for a different operation."}) from exc
    raise exc


@router.get("/capabilities")
def capabilities(request: Request):
    _member(request)
    try:
        import importlib.util
        chat2 = importlib.util.find_spec("aura_music_studio.shared_sky_transport_domain") is not None
        chat3 = importlib.util.find_spec("aura_music_studio.shared_sky_control_room") is not None
    except (ImportError, AttributeError):
        chat2 = chat3 = False
    return {
        "schema_version": 1,
        "studios": {key: {"source_types": sorted(value), "presets": _PRESETS[key]} for key, value in _SOURCE_TYPES.items()},
        "privacy_default": "allow_list_project_output",
        "full_workspace_default": False,
        "chat2_transport_contract": "available" if chat2 else "compatibility_pending",
        "chat3_control_room_contract": "available" if chat3 else "compatibility_pending",
        "community": {"chat4": "display_contract_only", "chat5_gifts": "display_only_no_financial_mutation", "chat6_battles": "read_only_no_score_engine"},
    }


@router.get("/projects/{project_name}/sources")
def sources(project_name: str, studio_type: StudioType, request: Request):
    member = _member(request)
    try:
        rows = discover_sources(_user_id(member), project_name, studio_type)
    except FileNotFoundError as exc:
        raise HTTPException(404, "Project not found") from exc
    return {"project_id": project_name, "studio_type": studio_type, "sources": rows, "presets": _PRESETS[studio_type]}


@router.get("/projects/{project_name}/sources/{source_adapter_id}")
def source_status(project_name: str, source_adapter_id: str, request: Request):
    user_id = _user_id(_member(request))
    _project(project_name)
    try:
        item = creation_live_store.get(user_id, source_adapter_id)
    except KeyError as exc:
        raise HTTPException(404, "Creation LIVE source not found") from exc
    if item["project_name"] != project_name:
        raise HTTPException(404, "Creation LIVE source not found")
    truth = _programme_truth(user_id, item)
    descriptor = dict(item["descriptor"])
    descriptor["health"] = "available" if item["source_status"] not in {"revoked", "detached"} else item["source_status"]
    return {"source": descriptor, "source_status": item["source_status"], "authoritative_live": truth, "truth_note": "ON AIR is true only when Chat 3 can authoritatively confirm this exact project source is on Programme."}


@router.get("/projects/{project_name}/sources/{source_adapter_id}/media")
def source_media(project_name: str, source_adapter_id: str, request: Request):
    user_id = _user_id(_member(request))
    project = _project(project_name).resolve()
    try:
        item = creation_live_store.get(user_id, source_adapter_id)
    except KeyError as exc:
        raise HTTPException(404, "Creation LIVE source not found") from exc
    if item["project_name"] != project_name or item["descriptor"].get("preview_kind") != "media":
        raise HTTPException(404, "Source preview unavailable")
    ref = str(item.get("server_ref") or "")
    relative = Path(ref)
    if not ref or relative.is_absolute():
        raise HTTPException(404, "Source media unavailable")
    target = (project / relative).resolve()
    if project not in target.parents or not target.is_file() or target.suffix.lower() not in _MEDIA_EXTENSIONS:
        raise HTTPException(404, "Source media unavailable")
    return FileResponse(target, media_type=mimetypes.guess_type(target.name)[0] or "application/octet-stream", headers={"Cache-Control":"private, no-store", "X-Content-Type-Options":"nosniff", "X-Creation-Live-Source":source_adapter_id})


@router.post("/projects/{project_name}/sources/{source_adapter_id}/attach")
def attach(project_name: str, source_adapter_id: str, body: AttachRequest, request: Request):
    user_id = _user_id(_member(request))
    _project(project_name)
    try:
        item = creation_live_store.get(user_id, source_adapter_id)
    except KeyError as exc:
        raise HTTPException(404, "Creation LIVE source not found") from exc
    if item["project_name"] != project_name:
        raise HTTPException(404, "Creation LIVE source not found")
    descriptor = item["descriptor"]
    rights = descriptor.get("rights") or {}
    if rights.get("state") == "blocked":
        raise HTTPException(403, {"code": "project_rights_blocked", "message": "This source is blocked by rights/privacy preflight.", "reasons": rights.get("codes", [])})
    if rights.get("requires_confirmation") and not body.rights_warning_confirmed:
        raise HTTPException(409, {"code": "rights_confirmation_required", "message": "Review and confirm the source rights/privacy warnings before attachment."})
    if descriptor.get("privacy_classification") == "advanced_workspace" and not body.full_workspace_confirmed:
        raise HTTPException(409, {"code": "full_workspace_confirmation_required", "message": "Full-workspace capture is a higher-risk advanced source and requires explicit confirmation."})
    try:
        shared_project = shared_sky.project(user_id, body.shared_sky_project_id)
    except KeyError as exc:
        raise HTTPException(403, {"code": "unauthorised_project", "message": "Shared Sky project is not owned by the current creator."}) from exc
    if body.broadcast_id:
        try:
            broadcast = shared_sky.broadcast(user_id, body.broadcast_id)
        except KeyError as exc:
            raise HTTPException(403, {"code": "live_session_ended", "message": "Broadcast is unavailable to this creator."}) from exc
        if broadcast.get("project_id") != shared_project.get("id"):
            raise HTTPException(409, {"code": "broadcast_project_mismatch", "message": "Broadcast belongs to a different Shared Sky project."})

    def execute():
        fresh = creation_live_store.get(user_id, source_adapter_id)
        registration = _transport_register(user_id, body.shared_sky_project_id, fresh["descriptor"], fresh.get("transport_source_id"))
        status = "ready" if registration["available"] else "registered"
        transport_id = registration.get("source", {}).get("id") if registration.get("source") else fresh.get("transport_source_id")
        try:
            updated = creation_live_store.mutate(
                user_id, source_adapter_id, expected_version=body.expected_version,
                editor_instance_id=body.editor_instance_id, source_status=status,
                shared_sky_project_id=body.shared_sky_project_id, broadcast_id=body.broadcast_id,
                transport_source_id=transport_id, active_editor_instance_id=body.editor_instance_id,
                descriptor_changes={"health": "available", "presentation_mode": fresh["descriptor"].get("presentation_mode", "creating")},
            )
        except RuntimeError as exc:
            _api_error(exc)
        truth = _programme_truth(user_id, updated)
        return {"source": updated["descriptor"], "source_status": updated["source_status"], "transport": registration, "authoritative_live": truth, "control_room_handoff": {"source_adapter_id": source_adapter_id, "safe_display_name": updated["descriptor"]["safe_display_name"], "privacy": updated["descriptor"]["privacy_classification"], "capabilities": updated["descriptor"]["capabilities"], "correlation_id": updated["descriptor"]["correlation_id"]}}

    try:
        return creation_live_store.idempotent(user_id, "attach", body.idempotency_key, source_adapter_id, body.model_dump(mode="json"), execute)
    except RuntimeError as exc:
        _api_error(exc)


@router.post("/projects/{project_name}/sources/{source_adapter_id}/transition")
def transition(project_name: str, source_adapter_id: str, body: TransitionRequest, request: Request):
    user_id = _user_id(_member(request)); _project(project_name)
    try:
        item = creation_live_store.get(user_id, source_adapter_id)
    except KeyError as exc:
        raise HTTPException(404, "Creation LIVE source not found") from exc
    if item["project_name"] != project_name or item["source_status"] in {"revoked", "detached"}:
        raise HTTPException(409, {"code": "source_not_ready", "message": "Source is not attached."})
    allowed_by_studio = {
        "music": {"creating","tutorial","rehearsal","performance","premiere","showcase","listening_party","brb","detached"},
        "video_cinema": {"creating","tutorial","review","premiere","showcase","brb","detached"},
        "image_visual": {"creating","tutorial","review","showcase","gallery","brb","detached"},
    }
    if body.mode not in allowed_by_studio[item["studio_type"]]:
        raise HTTPException(400, {"code": "presentation_mode_unsupported", "message": "Presentation mode is not valid for this studio."})
    try:
        updated = creation_live_store.mutate(user_id,source_adapter_id,expected_version=body.expected_version,editor_instance_id=body.editor_instance_id,descriptor_changes={"presentation_mode":body.mode})
    except RuntimeError as exc:
        _api_error(exc)
    return {"source":updated["descriptor"],"same_live_session":True,"authoritative_live":_programme_truth(user_id,updated),"note":"Presentation intent changed without creating a second live session; Chat 3 remains authoritative for Programme composition."}


@router.post("/projects/{project_name}/sources/{source_adapter_id}/emergency-hide")
def emergency_hide(project_name: str, source_adapter_id: str, body: TransitionRequest, request: Request):
    body = body.model_copy(update={"mode":"brb"})
    result = transition(project_name, source_adapter_id, body, request)
    result["emergency_hide"] = True
    result["programme_action"] = "brb_intent_requested"
    result["truth_note"] = "The project source is marked BRB locally. A real programme cut requires Chat 3 authoritative support; this endpoint never claims the cut occurred when that contract is unavailable."
    return result


@router.post("/projects/{project_name}/sources/{source_adapter_id}/detach")
def detach(project_name: str, source_adapter_id: str, body: DetachRequest, request: Request):
    user_id = _user_id(_member(request)); _project(project_name)
    try:
        item = creation_live_store.get(user_id,source_adapter_id)
    except KeyError as exc:
        raise HTTPException(404,"Creation LIVE source not found") from exc
    if item["project_name"] != project_name:
        raise HTTPException(404,"Creation LIVE source not found")
    def execute():
        current = creation_live_store.get(user_id, source_adapter_id)
        if current["source_status"] in {"detached","revoked"}:
            return {"source":current["descriptor"],"source_status":current["source_status"],"idempotent":True}
        try:
            updated=creation_live_store.mutate(user_id,source_adapter_id,expected_version=body.expected_version,editor_instance_id=body.editor_instance_id,source_status="detached",active_editor_instance_id=None,descriptor_changes={"presentation_mode":"detached","health":"detached"})
        except RuntimeError as exc:
            _api_error(exc)
        return {"source":updated["descriptor"],"source_status":"detached","idempotent":False,"transport_detach":"compatibility_event_only","note":"Project contribution is detached in Chat 7. Chat 2/3 source revocation is consumed when their canonical detach contract is available."}
    try:
        return creation_live_store.idempotent(user_id,"detach",body.idempotency_key,source_adapter_id,body.model_dump(mode="json"),execute)
    except RuntimeError as exc:
        _api_error(exc)


@router.get("/shared-sky/broadcasts")
def creator_broadcasts(request: Request):
    user_id = _user_id(_member(request))
    rows = shared_sky.broadcasts(user_id, limit=50)
    return {"broadcasts":[{"id":r["id"],"project_id":r["project_id"],"title":r["title"],"state":r["state"]} for r in rows]}


@router.post("/projects/{project_name}/markers")
def add_marker(project_name: str, body: MarkerRequest, request: Request):
    user_id=_user_id(_member(request)); _project(project_name)
    try:item=creation_live_store.get(user_id,body.source_adapter_id)
    except KeyError as exc:raise HTTPException(404,"Creation LIVE source not found") from exc
    if item["project_name"]!=project_name:raise HTTPException(404,"Creation LIVE source not found")
    marker_id=f"mark_{uuid4().hex}"
    correlation_id=f"corr_{uuid4().hex}"
    with creation_live_store.connect() as con:
        con.execute("INSERT INTO creation_live_markers VALUES(?,?,?,?,?,?,?,?,?,?)",(marker_id,user_id,project_name,body.source_adapter_id,body.live_session_id,_safe_label(body.label,"Marker"),body.kind,body.live_time_ms,correlation_id,_now()))
    return {"id":marker_id,"project_id":project_name,"live_session_id":body.live_session_id,"live_time_ms":body.live_time_ms,"kind":body.kind,"label":_safe_label(body.label,"Marker"),"correlation_id":correlation_id,"project_mutated":False}


@router.post("/projects/{project_name}/returns")
def import_return(project_name: str, body: ReturnAssetRequest, request: Request):
    user_id=_user_id(_member(request)); _project(project_name)
    try:source=creation_live_store.get(user_id,body.source_adapter_id)
    except KeyError as exc:raise HTTPException(404,"Creation LIVE source not found") from exc
    if source["project_name"]!=project_name or source["studio_type"]!=body.studio_type:
        raise HTTPException(409,{"code":"return_source_mismatch","message":"Returned asset does not match the originating project/studio source."})
    payload=body.model_dump(mode="json")
    with creation_live_store.connect() as con:
        row=con.execute("SELECT payload_json,imported_element_id FROM creation_live_returns WHERE user_id=? AND project_name=? AND return_import_id=?",(user_id,project_name,body.return_import_id)).fetchone()
        if row:
            return {"return":json.loads(row["payload_json"]),"imported_element_id":row["imported_element_id"],"deduplicated":True}
        con.execute("INSERT INTO creation_live_returns VALUES(?,?,?,?,?,?,?)",(user_id,project_name,body.return_import_id,json.dumps(payload,separators=(",", ":")),None,_now(),_now()))
    if body.processing_state not in {"ready","incomplete","recovered"}:
        return {"return":payload,"imported_element_id":None,"deduplicated":False,"imported":False,"note":"Authoritative recording asset is not ready for project import."}
    return {"return":payload,"imported_element_id":None,"deduplicated":False,"imported":False,"provenance_retained":True,"note":"Recording/highlight provenance is linked to this project. Binary import remains pending the canonical Chat 2 media-asset resolver; no fake local file is created."}


@router.get("/projects/{project_name}/community")
def community_panel(project_name: str, request: Request):
    _member(request); _project(project_name)
    return {
        "chat4":{"state":"compatibility_pending","mutates_project":False},
        "chat5_gifts":{"state":"display_only","financial_mutation":False},
        "chat6_battle":{"state":"read_only","local_score_engine":False},
        "truth_note":"No messages, viewer counts, Gifts or Battle scores are fabricated while owning realtime contracts are unavailable.",
    }


@router.get("/projects/{project_name}/aura-assistance")
def aura_assistance(project_name: str, studio_type: StudioType, request: Request):
    user_id=_user_id(_member(request)); rows=discover_sources(user_id,project_name,studio_type)
    ready=[r for r in rows if (r.get("rights") or {}).get("state")=="ready" and r.get("privacy_classification")!="advanced_workspace"]
    warning=[r for r in rows if (r.get("rights") or {}).get("state")=="warning"]
    best=ready[0] if ready else (warning[0] if warning else None)
    return {"recommended_source_adapter_id":best.get("source_adapter_id") if best else None,"recommended_preset":_PRESETS[studio_type][0],"warnings":((best or {}).get("rights") or {}).get("messages",[]),"consequential_actions_require_creator_confirmation":True,"can_start_or_stop_live":False,"can_reveal_hidden_content":False,"can_enable_full_workspace_capture":False}


LIVE_UI_SCRIPT = r"""
(()=>{
 const path=location.pathname, studio=path==='/studio'?'music':path==='/video-studio'?'video_cinema':path==='/image-designer'?'image_visual':null;
 if(!studio)return;
 const $=id=>document.getElementById(id); const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 let state={sources:[],selected:null,status:null,editor:sessionStorage.getItem('creationLiveEditor')||crypto.randomUUID(),previewStream:null}; sessionStorage.setItem('creationLiveEditor',state.editor);
 const projectName=()=>{try{if(path==='/studio'&&typeof selectedProject!=='undefined')return String(selectedProject||'').trim();if(typeof project==='function')return String(project()||'').trim()}catch(_){}return String($('project')?.value||new URLSearchParams(location.search).get('project')||'').trim()};
 async function req(url,opt={}){const r=await fetch(url,{credentials:'same-origin',headers:{'Content-Type':'application/json',...(opt.headers||{})},...opt});let b={};try{b=await r.json()}catch(_){}if(!r.ok)throw new Error(typeof b.detail==='string'?b.detail:(b.detail?.message||`Request failed (${r.status})`));return b}
 function ensure(){if($('creationLiveButton'))return;const b=document.createElement('button');b.id='creationLiveButton';b.type='button';b.textContent='🔴 Go Live & Create';b.setAttribute('aria-haspopup','dialog');b.style.cssText='position:fixed;right:18px;bottom:18px;z-index:79;border:1px solid #ff8da677;border-radius:999px;padding:12px 16px;background:#32101beF;color:#fff;font-weight:900;box-shadow:0 10px 35px #0008';b.onclick=open;document.body.append(b);const d=document.createElement('section');d.id='creationLiveDrawer';d.setAttribute('role','dialog');d.setAttribute('aria-modal','true');d.setAttribute('aria-label','Go Live and Create setup');d.hidden=true;d.style.cssText='position:fixed;right:0;top:0;bottom:0;width:min(540px,100%);z-index:80;background:#090d18f8;color:#fff;border-left:1px solid #ffffff24;padding:18px;overflow:auto;box-shadow:-20px 0 55px #0009;font-family:Inter,system-ui,sans-serif';d.innerHTML=`<div style="display:flex;gap:8px;align-items:center"><div style="flex:1"><small style="color:#f1c86f;font-weight:900">SHARED SKY · PROJECT-SAFE SOURCE</small><h2 style="margin:3px 0">Go Live & Create</h2></div><button id="clClose" aria-label="Close live setup">✕</button></div><p id="clTruth" style="color:#bdc7d8;font-size:.8rem">Nothing is ON AIR until Shared Sky confirms the exact project source is on Programme.</p><div id="clBody"></div><div id="clMessage" role="status" aria-live="polite" style="margin-top:10px;font-size:.78rem"></div>`;document.body.append(d);$('clClose').onclick=close;d.addEventListener('keydown',e=>{if(e.key==='Escape')close()});}
 function msg(v,bad=false){const m=$('clMessage');if(m){m.textContent=v;m.style.color=bad?'#ff9eae':'#8fe1b4'}}
 function close(){const d=$('creationLiveDrawer');if(d)d.hidden=true;if(state.previewStream){state.previewStream.getTracks().forEach(t=>t.stop());state.previewStream=null}}
 async function open(){ensure();$('creationLiveDrawer').hidden=false;$('clClose').focus();const pid=projectName();if(!pid){$('clBody').innerHTML='<p>Select/load a project first. Your creative editor stays open.</p>';return}await load(pid)}
 function sourceOption(s){const r=s.rights||{}, warn=r.state==='blocked'?'BLOCKED':r.state==='warning'?'CONFIRM':'READY';return `<label style="display:block;border:1px solid #ffffff24;padding:10px;border-radius:12px;margin:7px 0"><input type="radio" name="clSource" value="${esc(s.source_adapter_id)}" ${r.state==='blocked'?'disabled':''}> <b>${esc(s.safe_display_name)}</b><br><small>${esc(s.source_type)} · ${warn} · viewers get ${esc(s.privacy_classification)}</small></label>`}
 async function load(pid){try{const data=await req(`/creation-live/projects/${encodeURIComponent(pid)}/sources?studio_type=${studio}`);state.sources=data.sources||[];const b=$('clBody');b.innerHTML=`<p style="font-size:.78rem;color:#bdc7d8">Choose exactly what viewers may see/hear. Clean project output is preferred; full workspace is advanced and never the default.</p><div>${state.sources.map(sourceOption).join('')||'<p>No eligible project output is ready yet.</p>'}</div><div id="clPreview" style="border:1px solid #ffffff20;border-radius:12px;padding:10px;min-height:60px"></div><label style="display:block;margin:9px 0"><input id="clRights" type="checkbox"> I reviewed any rights/privacy warning for this source.</label><label id="clWorkspaceRow" style="display:none;margin:9px 0"><input id="clWorkspace" type="checkbox"> I understand full-workspace capture may expose unrelated notifications/private UI and browser masking is not guaranteed.</label><label>Shared Sky project ID<input id="clSkyProject" style="width:100%" placeholder="Choose an existing Shared Sky project"></label><label>Broadcast ID (optional)<input id="clBroadcast" style="width:100%" placeholder="Existing Shared Sky broadcast"></label><div style="display:flex;gap:7px;flex-wrap:wrap;margin-top:10px"><button id="clPreviewBtn">Preview selected</button><button id="clAttach">Attach safe source</button><button id="clRefresh">Refresh status</button><button id="clHide">BRB / emergency hide intent</button><button id="clDetach">Detach source</button><a href="/shared-sky" style="color:#f1c86f;padding:9px">Shared Sky</a></div><div id="clStatus" style="margin-top:10px"></div>`;b.querySelectorAll('input[name=clSource]').forEach(x=>x.onchange=()=>{state.selected=state.sources.find(s=>s.source_adapter_id===x.value);$('clWorkspaceRow').style.display=state.selected?.privacy_classification==='advanced_workspace'?'block':'none';$('clRights').checked=false;if($('clWorkspace'))$('clWorkspace').checked=false;renderStatus()});$('clPreviewBtn').onclick=preview;$('clAttach').onclick=attach;$('clRefresh').onclick=refresh;$('clHide').onclick=hide;$('clDetach').onclick=detach;}catch(e){msg(e.message,true)}}
 function renderStatus(){const box=$('clStatus');if(!box)return;const s=state.selected,live=state.status?.authoritative_live;if(!s){box.textContent='Select a source.';return}box.innerHTML=`<b>${esc(s.safe_display_name)}</b><div>Rights: ${esc(s.rights?.state||'unknown')} · Source: ${esc(state.status?.source_status||s.live_source_registration_state||'not registered')}</div><div>Session: ${esc(live?.session_state||'not selected')} · Programme: ${esc(live?.programme_state||'unknown')} · <b>${live?.on_air?'ON AIR':'NOT CONFIRMED ON AIR'}</b></div><small>${esc((s.rights?.messages||[]).join(' '))}</small>`}
 async function preview(){const s=state.selected;if(!s)return msg('Select a source first.',true);const box=$('clPreview');box.replaceChildren();if(s.preview_kind==='media'){if(s.media_kind==='audio'){const a=document.createElement('audio');a.controls=true;a.src=`/creation-live/projects/${encodeURIComponent(projectName())}/sources/${encodeURIComponent(s.source_adapter_id)}/media`;box.append(a)}else if(s.media_kind==='still-or-slideshow'){const i=document.createElement('img');i.alt=s.safe_display_name;i.src=`/creation-live/projects/${encodeURIComponent(projectName())}/sources/${encodeURIComponent(s.source_adapter_id)}/media`;i.style.maxWidth='100%';box.append(i)}else{const v=document.createElement('video');v.controls=true;v.playsInline=true;v.src=`/creation-live/projects/${encodeURIComponent(projectName())}/sources/${encodeURIComponent(s.source_adapter_id)}/media`;v.style.maxWidth='100%';box.append(v)}}else if(s.privacy_classification==='advanced_workspace'){if(!navigator.mediaDevices?.getDisplayMedia)return msg('This browser does not support screen/workspace capture.',true);try{state.previewStream=await navigator.mediaDevices.getDisplayMedia({video:true,audio:false});const v=document.createElement('video');v.autoplay=true;v.muted=true;v.playsInline=true;v.srcObject=state.previewStream;v.style.maxWidth='100%';box.append(v);msg('Browser-granted preview active. This is still not attached or ON AIR.')}catch(e){msg('Workspace capture permission was not granted.',true)}}else{box.textContent='This source has no browser preview path in the current editor.'}}
 async function attach(){const s=state.selected;if(!s)return msg('Select a source first.',true);const sky=$('clSkyProject')?.value.trim();if(!sky)return msg('Enter an existing Shared Sky project ID.',true);try{const data=await req(`/creation-live/projects/${encodeURIComponent(projectName())}/sources/${encodeURIComponent(s.source_adapter_id)}/attach`,{method:'POST',body:JSON.stringify({shared_sky_project_id:sky,broadcast_id:$('clBroadcast')?.value.trim()||null,editor_instance_id:state.editor,idempotency_key:crypto.randomUUID(),expected_version:s.version,rights_warning_confirmed:$('clRights')?.checked||false,full_workspace_confirmed:$('clWorkspace')?.checked||false})});state.selected=data.source;state.status=data;renderStatus();msg(data.transport?.available?'Source registered with Shared Sky transport.':'Source safely prepared; Chat 2 transport registry is pending merge. No LIVE success is being claimed.')}catch(e){msg(e.message,true)}}
 async function refresh(){if(!state.selected)return msg('Select a source first.',true);try{state.status=await req(`/creation-live/projects/${encodeURIComponent(projectName())}/sources/${encodeURIComponent(state.selected.source_adapter_id)}`);state.selected=state.status.source;renderStatus()}catch(e){msg(e.message,true)}}
 async function hide(){if(!state.selected)return msg('Select/attach a source first.',true);try{const d=await req(`/creation-live/projects/${encodeURIComponent(projectName())}/sources/${encodeURIComponent(state.selected.source_adapter_id)}/emergency-hide`,{method:'POST',body:JSON.stringify({mode:'brb',expected_version:state.selected.version,editor_instance_id:state.editor})});state.selected=d.source;state.status=d;renderStatus();msg(d.truth_note)}catch(e){msg(e.message,true)}}
 async function detach(){if(!state.selected)return msg('Select a source first.',true);try{const d=await req(`/creation-live/projects/${encodeURIComponent(projectName())}/sources/${encodeURIComponent(state.selected.source_adapter_id)}/detach`,{method:'POST',body:JSON.stringify({expected_version:state.selected.version,editor_instance_id:state.editor,idempotency_key:crypto.randomUUID(),reason:'creator_detach'})});state.selected=d.source;state.status=d;renderStatus();msg('Project contribution detached; project editing remains intact.')}catch(e){msg(e.message,true)}}
 ensure();
})();
"""


@router.get("/ui.js", include_in_schema=False)
def live_ui():
    return Response(content=LIVE_UI_SCRIPT, media_type="application/javascript", headers={"Cache-Control":"no-store"})


class CreationLiveMiddleware(BaseHTTPMiddleware):
    _PATHS = {"/studio", "/video-studio", "/image-designer"}
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if request.method.upper() != "GET" or request.url.path not in self._PATHS:
            return response
        content_type = (response.headers.get("content-type") or "").lower()
        if not content_type.startswith("text/html"):
            return response
        body=b""
        async for chunk in response.body_iterator: body+=chunk
        try:text=body.decode("utf-8")
        except UnicodeDecodeError:return Response(content=body,status_code=response.status_code,headers=dict(response.headers),background=response.background)
        marker="<script src='/creation-live/ui.js'></script>"
        if marker not in text:text=text.replace("</body>",marker+"</body>")
        encoded=text.encode("utf-8"); migrated=Response(content=encoded,status_code=response.status_code,background=response.background)
        raw=[(k,v) for k,v in response.raw_headers if k.lower()!=b"content-length"]+[(b"content-length",str(len(encoded)).encode("ascii"))]
        migrated.raw_headers=raw; return migrated


def install_creation_live(app: Any) -> None:
    if getattr(app.state, "creation_live_installed", False):
        return
    app.include_router(router)
    app.add_middleware(CreationLiveMiddleware)
    app.state.creation_live_installed = True


__all__ = [
    "router", "CreationLiveMiddleware", "CreationLiveSourceDescriptor", "CreationLiveStore", "RightsPreflight",
    "SourceCapabilities", "creation_live_store", "discover_sources", "install_creation_live", "LIVE_UI_SCRIPT",
]
