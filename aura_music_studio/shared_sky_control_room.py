from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
from ipaddress import ip_address
from typing import Any, Literal, Protocol, Sequence
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .esp_niche import require_esp_hub_member
from .shared_sky_streaming_studios import (
    SceneCreate,
    SceneUpdate,
    SourceCreate,
    SourceUpdate,
    shared_sky,
)

router = APIRouter(tags=["Shared Sky Professional Control Room"])

PROFILE_REGISTRY: dict[str, dict[str, Any]] = {
    "landscape-1080": {"key": "landscape-1080", "width": 1920, "height": 1080, "aspect": "16:9", "orientation": "landscape"},
    "portrait-1080": {"key": "portrait-1080", "width": 1080, "height": 1920, "aspect": "9:16", "orientation": "portrait"},
    "square-1080": {"key": "square-1080", "width": 1080, "height": 1080, "aspect": "1:1", "orientation": "square"},
}
TRANSITIONS = {"cut", "fade", "dip_to_colour", "slide", "swipe", "push", "zoom"}
WIDGET_KINDS = {"chat", "poll", "qa", "gift_goal", "supporter", "battle_score", "captions", "now_playing", "custom_text"}
PROGRAMME_SAFE_PRIVACY = {"public", "programme", "programme_safe", "shared"}
SECRET_KEYS = {
    "access_token", "refresh_token", "oauth_token", "oauth_secret", "stream_key", "password",
    "credential", "credential_ciphertext", "client_secret", "api_secret", "private_key", "secret",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StudioError(RuntimeError):
    pass


class StudioConflict(StudioError):
    pass


class StudioInvariantError(StudioError):
    pass


class StudioTransportError(StudioError):
    pass


def _loads(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value) if value else fallback
    except (TypeError, json.JSONDecodeError):
        return fallback


def _json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _contains_secret(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).strip().lower() in SECRET_KEYS:
                return True
            if _contains_secret(child):
                return True
    elif isinstance(value, list):
        return any(_contains_secret(child) for child in value)
    return False


def validate_no_secrets(value: Any) -> None:
    if _contains_secret(value):
        raise StudioInvariantError("Studio state/presets may not contain provider secrets or credentials")


def validate_web_source_url(url: str) -> str:
    clean = (url or "").strip()
    parsed = urlparse(clean)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise StudioInvariantError("Browser sources require an HTTP(S) URL")
    if parsed.username or parsed.password:
        raise StudioInvariantError("Browser source URLs may not contain embedded credentials")
    host = parsed.hostname.lower().rstrip(".")
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        raise StudioInvariantError("Local/private browser source hosts are not allowed")
    try:
        addr = ip_address(host)
        if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
            raise StudioInvariantError("Local/private browser source hosts are not allowed")
    except ValueError:
        pass
    if len(clean) > 2048:
        raise StudioInvariantError("Browser source URL is too long")
    return clean


def normalize_transform(value: dict[str, Any] | None) -> dict[str, float]:
    value = value or {}
    result = {
        "x": float(value.get("x", 0.0)), "y": float(value.get("y", 0.0)),
        "width": float(value.get("width", 1.0)), "height": float(value.get("height", 1.0)),
        "rotation": float(value.get("rotation", 0.0)), "opacity": float(value.get("opacity", 1.0)),
        "crop_top": float(value.get("crop_top", 0.0)), "crop_right": float(value.get("crop_right", 0.0)),
        "crop_bottom": float(value.get("crop_bottom", 0.0)), "crop_left": float(value.get("crop_left", 0.0)),
    }
    for key in ("x", "y"):
        result[key] = max(-4.0, min(4.0, result[key]))
    for key in ("width", "height"):
        result[key] = max(0.001, min(8.0, result[key]))
    result["rotation"] = ((result["rotation"] + 180.0) % 360.0) - 180.0
    result["opacity"] = max(0.0, min(1.0, result["opacity"]))
    for key in ("crop_top", "crop_right", "crop_bottom", "crop_left"):
        result[key] = max(0.0, min(0.95, result[key]))
    if result["crop_left"] + result["crop_right"] >= 0.99 or result["crop_top"] + result["crop_bottom"] >= 0.99:
        raise StudioInvariantError("Crop cannot remove the complete source")
    return result


def normalize_effects(value: dict[str, Any] | None) -> dict[str, float]:
    value = value or {}
    bounds = {
        "brightness": (0.0, 2.0, 1.0), "contrast": (0.0, 2.0, 1.0), "saturation": (0.0, 3.0, 1.0),
        "hue": (-180.0, 180.0, 0.0), "blur": (0.0, 30.0, 0.0), "rounded": (0.0, 0.5, 0.0),
    }
    out: dict[str, float] = {}
    for key, (minimum, maximum, default) in bounds.items():
        out[key] = max(minimum, min(maximum, float(value.get(key, default))))
    return out


def normalize_audio(value: dict[str, Any] | None) -> dict[str, Any]:
    value = value or {}
    return {
        "muted": bool(value.get("muted", False)),
        "gain": max(0.0, min(4.0, float(value.get("gain", 1.0)))),
        "pan": max(-1.0, min(1.0, float(value.get("pan", 0.0)))),
        "delay_ms": max(0, min(5000, int(value.get("delay_ms", 0)))),
        "monitor": value.get("monitor", "off") if value.get("monitor", "off") in {"off", "headphones"} else "off",
        "high_pass_hz": max(20.0, min(400.0, float(value.get("high_pass_hz", 80.0)))),
        "compressor": bool(value.get("compressor", True)),
        "limiter": bool(value.get("limiter", True)),
    }


def calculate_audio_meter(samples: Sequence[float]) -> dict[str, Any]:
    if not samples:
        return {"available": False, "rms": None, "peak": None, "dbfs": None, "clipping": False}
    finite = [float(sample) for sample in samples if math.isfinite(float(sample))]
    if not finite:
        return {"available": False, "rms": None, "peak": None, "dbfs": None, "clipping": False}
    rms = math.sqrt(sum(sample * sample for sample in finite) / len(finite))
    peak = max(abs(sample) for sample in finite)
    dbfs = 20.0 * math.log10(max(rms, 1e-12))
    return {"available": True, "rms": rms, "peak": peak, "dbfs": dbfs, "clipping": peak >= 0.999}


def participant_layout(layout_key: str, count: int, profile_key: str = "landscape-1080") -> list[dict[str, float]]:
    if profile_key not in PROFILE_REGISTRY:
        raise StudioInvariantError("Unknown production profile")
    if count < 1 or count > 8:
        raise StudioInvariantError("Participant layouts support one to eight tiles")
    key = layout_key or "grid"
    if key in {"solo", "speaker_focus"} and count == 1:
        return [{"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}]
    portrait = PROFILE_REGISTRY[profile_key]["orientation"] == "portrait"
    if key in {"side_by_side", "interview"} and count == 2:
        if portrait:
            return [{"x": 0.0, "y": i * 0.5, "width": 1.0, "height": 0.5} for i in range(2)]
        return [{"x": i * 0.5, "y": 0.0, "width": 0.5, "height": 1.0} for i in range(2)]
    if key == "vertical_stack":
        height = 1.0 / count
        return [{"x": 0.0, "y": i * height, "width": 1.0, "height": height} for i in range(count)]
    if key == "host_guests" and count > 1:
        if portrait:
            host_h = 0.55
            guest_h = (1.0 - host_h) / math.ceil((count - 1) / 2)
            slots = [{"x": 0.0, "y": 0.0, "width": 1.0, "height": host_h}]
            for idx in range(count - 1):
                slots.append({"x": (idx % 2) * 0.5, "y": host_h + (idx // 2) * guest_h, "width": 0.5, "height": guest_h})
            return slots
        host_w = 0.62
        guest_h = 1.0 / (count - 1)
        return [{"x": 0.0, "y": 0.0, "width": host_w, "height": 1.0}] + [
            {"x": host_w, "y": i * guest_h, "width": 1.0 - host_w, "height": guest_h} for i in range(count - 1)
        ]
    columns = 1 if count == 1 else 2 if count <= 4 else 3
    if portrait and count >= 5:
        columns = 2
    rows = math.ceil(count / columns)
    width, height = 1.0 / columns, 1.0 / rows
    return [{"x": (i % columns) * width, "y": (i // columns) * height, "width": width, "height": height} for i in range(count)]


def programme_safe_source(source: dict[str, Any]) -> tuple[bool, str]:
    config = source.get("config") or {}
    validate_no_secrets(config)
    privacy = str(config.get("privacy") or "programme_safe").lower()
    if privacy not in PROGRAMME_SAFE_PRIVACY or bool(config.get("backstage_only", False)):
        return False, f"Source {source.get('id') or source.get('name')} is private/backstage"
    if source.get("source_type") == "browser":
        validate_web_source_url(str(config.get("url") or ""))
    return True, ""


class GraphAdapter(Protocol):
    def project(self, user_id: str, project_id: str) -> dict[str, Any]: ...
    def scene(self, user_id: str, scene_id: str) -> dict[str, Any]: ...
    def create_scene(self, user_id: str, project_id: str, body: Any) -> dict[str, Any]: ...
    def update_scene(self, user_id: str, scene_id: str, body: Any) -> dict[str, Any]: ...
    def create_source(self, user_id: str, scene_id: str, body: Any) -> dict[str, Any]: ...
    def update_source(self, user_id: str, source_id: str, body: Any) -> dict[str, Any]: ...
    def source(self, user_id: str, source_id: str) -> dict[str, Any]: ...
    def broadcast(self, user_id: str, broadcast_id: str) -> dict[str, Any]: ...
    def preflight(self, user_id: str, broadcast_id: str) -> dict[str, Any]: ...
    def event(self, user_id: str, broadcast_id: str | None, event_type: str, payload: dict[str, Any] | None = None) -> None: ...


@dataclass(frozen=True)
class TransportCommit:
    accepted: bool
    authoritative: bool
    state: str
    reason: str = ""
    correlation_id: str = ""


class SharedSkyTransportCompatibilityAdapter:
    """Narrow Chat 2 adapter. Live mutation fails closed until Chat 2 exposes set_programme_snapshot."""

    def __init__(self, graph: GraphAdapter):
        self.graph = graph

    def status(self, user_id: str, broadcast_id: str | None) -> dict[str, Any]:
        if not broadcast_id:
            return {"state": "offline", "authoritative": True, "programme_commit_supported": True, "source": "studio"}
        broadcast = self.graph.broadcast(user_id, broadcast_id)
        preflight = self.graph.preflight(user_id, broadcast_id)
        commit_supported = callable(getattr(self.graph, "set_programme_snapshot", None))
        return {
            "state": str(broadcast.get("state") or "unknown"), "authoritative": True,
            "programme_commit_supported": commit_supported or str(broadcast.get("state")) not in {"live", "starting"},
            "preflight": preflight, "source": "chat2",
        }

    def commit_programme(self, user_id: str, broadcast_id: str | None, snapshot: dict[str, Any], correlation_id: str) -> TransportCommit:
        validate_no_secrets(snapshot)
        if not broadcast_id:
            return TransportCommit(True, True, "offline_programme", correlation_id=correlation_id)
        broadcast = self.graph.broadcast(user_id, broadcast_id)
        setter = getattr(self.graph, "set_programme_snapshot", None)
        if callable(setter):
            result = setter(user_id, broadcast_id, snapshot, correlation_id=correlation_id)
            return TransportCommit(bool(result.get("accepted")), True, str(result.get("state") or "unknown"), str(result.get("reason") or ""), correlation_id)
        if str(broadcast.get("state") or "") in {"live", "starting"}:
            return TransportCommit(False, True, str(broadcast.get("state")), "Chat 2 live programme commit contract is not available", correlation_id)
        return TransportCommit(True, True, str(broadcast.get("state") or "draft"), correlation_id=correlation_id)

    def recording_capabilities(self, user_id: str, broadcast_id: str | None) -> dict[str, Any]:
        if not broadcast_id:
            return {"supported": False, "state": "unavailable", "reason": "Recording requires a broadcast session"}
        getter = getattr(self.graph, "recording_status", None)
        if not callable(getter):
            return {"supported": False, "state": "unavailable", "reason": "Chat 2 recording contract not merged"}
        return {"supported": True, **getter(user_id, broadcast_id)}


class StudioSessionCreate(BaseModel):
    project_id: str = Field(min_length=1, max_length=128)
    broadcast_id: str | None = Field(default=None, max_length=128)
    profile_key: Literal["landscape-1080", "portrait-1080", "square-1080"] = "landscape-1080"


class PreviewSelect(BaseModel):
    scene_id: str = Field(min_length=1, max_length=128)
    expected_version: int = Field(ge=1)


class CutRequest(BaseModel):
    expected_version: int = Field(ge=1)


class TransitionRequest(BaseModel):
    expected_version: int = Field(ge=1)
    transition_key: str = Field(default="fade", min_length=1, max_length=80)
    duration_ms: int = Field(default=350, ge=0, le=20000)
    reduced_motion: bool = False


class TransitionComplete(BaseModel):
    expected_version: int = Field(ge=1)
    transition_id: str = Field(min_length=1, max_length=128)


class SourceTransformPatch(BaseModel):
    transform: dict[str, Any] = Field(default_factory=dict)
    effects: dict[str, Any] | None = None
    expected_session_version: int = Field(ge=1)


class AudioPatch(BaseModel):
    audio: dict[str, Any] = Field(default_factory=dict)
    expected_session_version: int = Field(ge=1)


class SceneDuplicateRequest(BaseModel):
    name: str | None = Field(default=None, max_length=120)


class SceneReorderRequest(BaseModel):
    scene_ids: list[str] = Field(min_length=1, max_length=200)


class BrandKitUpsert(BaseModel):
    name: str = Field(default="Shared Sky Brand Kit", min_length=1, max_length=160)
    colors: list[str] = Field(default_factory=list, max_length=24)
    font_keys: list[str] = Field(default_factory=list, max_length=24)
    asset_refs: dict[str, str] = Field(default_factory=dict)
    style: dict[str, Any] = Field(default_factory=dict)
    expected_version: int | None = Field(default=None, ge=1)


class StudioRepository:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path, timeout=15)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=5000")
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS shared_sky_studio_sessions (
                    id TEXT PRIMARY KEY,user_id TEXT NOT NULL,project_id TEXT NOT NULL,broadcast_id TEXT,
                    profile_key TEXT NOT NULL,preview_scene_id TEXT,programme_scene_id TEXT,
                    programme_snapshot_json TEXT NOT NULL DEFAULT '{}',transition_state TEXT NOT NULL DEFAULT 'idle',
                    transition_json TEXT NOT NULL DEFAULT '{}',autosave_state_json TEXT NOT NULL DEFAULT '{}',
                    last_transport_state_json TEXT NOT NULL DEFAULT '{}',version INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,updated_at TEXT NOT NULL,UNIQUE(user_id,project_id),
                    FOREIGN KEY(project_id) REFERENCES shared_sky_projects(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_shared_sky_studio_session_project ON shared_sky_studio_sessions(project_id,updated_at DESC);
                CREATE TABLE IF NOT EXISTS shared_sky_studio_versions (
                    session_id TEXT NOT NULL,version INTEGER NOT NULL,state_json TEXT NOT NULL,created_at TEXT NOT NULL,
                    PRIMARY KEY(session_id,version),FOREIGN KEY(session_id) REFERENCES shared_sky_studio_sessions(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS shared_sky_brand_kits (
                    id TEXT PRIMARY KEY,user_id TEXT NOT NULL,project_id TEXT NOT NULL,name TEXT NOT NULL,
                    config_json TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES shared_sky_projects(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_shared_sky_brand_kits_project ON shared_sky_brand_kits(project_id,updated_at DESC);
                """
            )

    def _project_owned(self, con: sqlite3.Connection, user_id: str, project_id: str) -> None:
        if not con.execute("SELECT 1 FROM shared_sky_projects WHERE id=? AND user_id=?", (project_id, user_id)).fetchone():
            raise KeyError(project_id)

    def _scene_owned(self, con: sqlite3.Connection, user_id: str, project_id: str, scene_id: str) -> None:
        if not con.execute("SELECT 1 FROM shared_sky_scenes WHERE id=? AND project_id=? AND user_id=?", (scene_id, project_id, user_id)).fetchone():
            raise KeyError(scene_id)

    def ensure_session(self, user_id: str, project_id: str, broadcast_id: str | None, profile_key: str) -> dict[str, Any]:
        if profile_key not in PROFILE_REGISTRY:
            raise StudioInvariantError("Unknown production profile")
        now = utc_now()
        with self._connect() as con:
            self._project_owned(con, user_id, project_id)
            row = con.execute("SELECT * FROM shared_sky_studio_sessions WHERE user_id=? AND project_id=?", (user_id, project_id)).fetchone()
            if row:
                session_id = str(row["id"])
                if broadcast_id is not None and broadcast_id != row["broadcast_id"]:
                    con.execute("UPDATE shared_sky_studio_sessions SET broadcast_id=?,updated_at=? WHERE id=?", (broadcast_id, now, session_id))
            else:
                scene = con.execute("SELECT id FROM shared_sky_scenes WHERE user_id=? AND project_id=? ORDER BY position,id LIMIT 1", (user_id, project_id)).fetchone()
                session_id = uuid4().hex
                con.execute(
                    "INSERT INTO shared_sky_studio_sessions(id,user_id,project_id,broadcast_id,profile_key,preview_scene_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                    (session_id, user_id, project_id, broadcast_id, profile_key, scene["id"] if scene else None, now, now),
                )
        session = self.get_session(user_id, session_id)
        self._record_version(session)
        return session

    def get_session(self, user_id: str, session_id: str) -> dict[str, Any]:
        with self._connect() as con:
            row = con.execute("SELECT * FROM shared_sky_studio_sessions WHERE id=? AND user_id=?", (session_id, user_id)).fetchone()
        if not row:
            raise KeyError(session_id)
        return self._public(dict(row))

    def _public(self, row: dict[str, Any]) -> dict[str, Any]:
        row["programme_snapshot"] = _loads(row.pop("programme_snapshot_json", "{}"), {})
        row["transition"] = _loads(row.pop("transition_json", "{}"), {})
        row["autosave_state"] = _loads(row.pop("autosave_state_json", "{}"), {})
        row["last_transport_state"] = _loads(row.pop("last_transport_state_json", "{}"), {})
        row["profile"] = PROFILE_REGISTRY.get(str(row["profile_key"]), {})
        return row

    def _record_version(self, session: dict[str, Any]) -> None:
        state = {key: value for key, value in session.items() if key not in {"created_at", "updated_at"}}
        with self._connect() as con:
            con.execute("INSERT OR IGNORE INTO shared_sky_studio_versions(session_id,version,state_json,created_at) VALUES(?,?,?,?)", (session["id"], session["version"], _json(state), utc_now()))
            con.execute(
                "DELETE FROM shared_sky_studio_versions WHERE session_id=? AND version NOT IN (SELECT version FROM shared_sky_studio_versions WHERE session_id=? ORDER BY version DESC LIMIT 50)",
                (session["id"], session["id"]),
            )

    def _mutate(self, user_id: str, session_id: str, expected_version: int, fields: dict[str, Any]) -> dict[str, Any]:
        fields = dict(fields)
        validate_no_secrets(fields)
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM shared_sky_studio_sessions WHERE id=? AND user_id=?", (session_id, user_id)).fetchone()
            if not row:
                raise KeyError(session_id)
            if int(row["version"]) != expected_version:
                raise StudioConflict(f"Studio version conflict: expected {expected_version}, current {row['version']}")
            sets: list[str] = []
            params: list[Any] = []
            for key, value in fields.items():
                if key in {"programme_snapshot", "transition", "autosave_state", "last_transport_state"}:
                    key = f"{key}_json"
                    value = _json(value)
                sets.append(f"{key}=?")
                params.append(value)
            sets.extend(["version=version+1", "updated_at=?"])
            params.extend([utc_now(), session_id, user_id, expected_version])
            cursor = con.execute(f"UPDATE shared_sky_studio_sessions SET {','.join(sets)} WHERE id=? AND user_id=? AND version=?", tuple(params))
            if cursor.rowcount != 1:
                raise StudioConflict("Studio state changed in another tab/operator")
        session = self.get_session(user_id, session_id)
        self._record_version(session)
        return session

    def select_preview(self, user_id: str, session_id: str, scene_id: str, expected_version: int) -> dict[str, Any]:
        current = self.get_session(user_id, session_id)
        with self._connect() as con:
            self._scene_owned(con, user_id, current["project_id"], scene_id)
        return self._mutate(user_id, session_id, expected_version, {"preview_scene_id": scene_id})

    def set_autosave_state(self, user_id: str, session_id: str, expected_version: int, state: dict[str, Any]) -> dict[str, Any]:
        validate_no_secrets(state)
        return self._mutate(user_id, session_id, expected_version, {"autosave_state": state})

    def begin_transition(self, user_id: str, session_id: str, expected_version: int, transition: dict[str, Any]) -> dict[str, Any]:
        current = self.get_session(user_id, session_id)
        if current["transition_state"] != "idle":
            raise StudioConflict("A programme transition is already in progress")
        return self._mutate(user_id, session_id, expected_version, {"transition_state": "in_progress", "transition": transition})

    def complete_programme(self, user_id: str, session_id: str, expected_version: int, snapshot: dict[str, Any], transport_state: dict[str, Any], transition_id: str | None = None) -> dict[str, Any]:
        current = self.get_session(user_id, session_id)
        if transition_id is not None and (current["transition_state"] != "in_progress" or current["transition"].get("transition_id") != transition_id):
            raise StudioConflict("Transition token is stale or no longer active")
        return self._mutate(user_id, session_id, expected_version, {
            "programme_scene_id": snapshot.get("scene", {}).get("id"), "programme_snapshot": snapshot,
            "last_transport_state": transport_state, "transition_state": "idle", "transition": {},
        })

    def abort_transition(self, user_id: str, session_id: str, expected_version: int, reason: str) -> dict[str, Any]:
        return self._mutate(user_id, session_id, expected_version, {"transition_state": "idle", "transition": {"last_error": reason[:400]}})

    def versions(self, user_id: str, session_id: str, limit: int = 20) -> list[dict[str, Any]]:
        self.get_session(user_id, session_id)
        with self._connect() as con:
            rows = con.execute("SELECT version,state_json,created_at FROM shared_sky_studio_versions WHERE session_id=? ORDER BY version DESC LIMIT ?", (session_id, max(1, min(limit, 50)))).fetchall()
        return [{"version": row["version"], "state": _loads(row["state_json"], {}), "created_at": row["created_at"]} for row in rows]

    def upsert_brand_kit(self, user_id: str, project_id: str, body: BrandKitUpsert, kit_id: str | None = None) -> dict[str, Any]:
        config = {"colors": body.colors, "font_keys": body.font_keys, "asset_refs": body.asset_refs, "style": body.style}
        validate_no_secrets(config)
        if any(not isinstance(ref, str) or len(ref) > 256 for ref in body.asset_refs.values()):
            raise StudioInvariantError("Brand Kit assets must use bounded asset references")
        now = utc_now()
        with self._connect() as con:
            self._project_owned(con, user_id, project_id)
            if kit_id:
                current = con.execute("SELECT * FROM shared_sky_brand_kits WHERE id=? AND user_id=? AND project_id=?", (kit_id, user_id, project_id)).fetchone()
                if not current:
                    raise KeyError(kit_id)
                if body.expected_version is None or int(current["version"]) != body.expected_version:
                    raise StudioConflict("Brand Kit version conflict")
                con.execute("UPDATE shared_sky_brand_kits SET name=?,config_json=?,version=version+1,updated_at=? WHERE id=? AND version=?", (body.name.strip(), _json(config), now, kit_id, body.expected_version))
            else:
                kit_id = uuid4().hex
                con.execute("INSERT INTO shared_sky_brand_kits(id,user_id,project_id,name,config_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", (kit_id, user_id, project_id, body.name.strip(), _json(config), now, now))
        return self.brand_kit(user_id, str(kit_id))

    def brand_kit(self, user_id: str, kit_id: str) -> dict[str, Any]:
        with self._connect() as con:
            row = con.execute("SELECT * FROM shared_sky_brand_kits WHERE id=? AND user_id=?", (kit_id, user_id)).fetchone()
        if not row:
            raise KeyError(kit_id)
        item = dict(row)
        item["config"] = _loads(item.pop("config_json", "{}"), {})
        return item

    def brand_kits(self, user_id: str, project_id: str) -> list[dict[str, Any]]:
        with self._connect() as con:
            self._project_owned(con, user_id, project_id)
            rows = con.execute("SELECT id FROM shared_sky_brand_kits WHERE user_id=? AND project_id=? ORDER BY updated_at DESC", (user_id, project_id)).fetchall()
        return [self.brand_kit(user_id, str(row["id"])) for row in rows]


class StudioService:
    def __init__(self, repo: StudioRepository, graph: GraphAdapter, transport: SharedSkyTransportCompatibilityAdapter | None = None):
        self.repo = repo
        self.graph = graph
        self.transport = transport or SharedSkyTransportCompatibilityAdapter(graph)

    def create_session(self, user_id: str, body: StudioSessionCreate) -> dict[str, Any]:
        self.graph.project(user_id, body.project_id)
        if body.broadcast_id:
            broadcast = self.graph.broadcast(user_id, body.broadcast_id)
            if broadcast.get("project_id") != body.project_id:
                raise StudioInvariantError("Broadcast does not belong to the selected Shared Sky project")
        return self.hydrate(user_id, self.repo.ensure_session(user_id, body.project_id, body.broadcast_id, body.profile_key))

    def hydrate(self, user_id: str, session: dict[str, Any]) -> dict[str, Any]:
        return {
            "session": session, "project": self.graph.project(user_id, session["project_id"]),
            "transport": self.transport.status(user_id, session.get("broadcast_id")),
            "profiles": PROFILE_REGISTRY, "layouts": self.layout_catalog(),
        }

    def session(self, user_id: str, session_id: str) -> dict[str, Any]:
        return self.hydrate(user_id, self.repo.get_session(user_id, session_id))

    def _snapshot_scene(self, user_id: str, scene_id: str, profile_key: str) -> dict[str, Any]:
        scene = self.graph.scene(user_id, scene_id)
        sources: list[dict[str, Any]] = []
        for source in scene.get("sources", []):
            ok, reason = programme_safe_source(source)
            if source.get("visible") and not ok:
                raise StudioInvariantError(reason)
            item = dict(source)
            config = dict(item.get("config") or {})
            config["transform"] = normalize_transform(config.get("transform"))
            config["effects"] = normalize_effects(config.get("effects"))
            if source.get("source_type") in {"microphone", "audio", "desktop_audio", "application_audio", "video"}:
                config["audio"] = normalize_audio(config.get("audio"))
            item["config"] = config
            sources.append(item)
        snapshot = {
            "schema_version": 1, "captured_at": utc_now(), "profile": PROFILE_REGISTRY[profile_key],
            "scene": {key: scene.get(key) for key in ("id", "name", "layout_key", "transition_key", "transition_ms")},
            "sources": sources,
        }
        validate_no_secrets(snapshot)
        return snapshot

    def select_preview(self, user_id: str, session_id: str, body: PreviewSelect) -> dict[str, Any]:
        return self.hydrate(user_id, self.repo.select_preview(user_id, session_id, body.scene_id, body.expected_version))

    def cut(self, user_id: str, session_id: str, expected_version: int) -> dict[str, Any]:
        current = self.repo.get_session(user_id, session_id)
        if current["transition_state"] != "idle":
            raise StudioConflict("Cannot CUT while a transition is in progress")
        if not current.get("preview_scene_id"):
            raise StudioInvariantError("No Preview scene is selected")
        snapshot = self._snapshot_scene(user_id, current["preview_scene_id"], current["profile_key"])
        correlation_id = uuid4().hex
        commit = self.transport.commit_programme(user_id, current.get("broadcast_id"), snapshot, correlation_id)
        if not commit.accepted:
            raise StudioTransportError(commit.reason or "Transport rejected programme commit")
        session = self.repo.complete_programme(user_id, session_id, expected_version, snapshot, commit.__dict__)
        self.graph.event(user_id, current.get("broadcast_id"), "studio_cut", {"session_id": session_id, "scene_id": session["programme_scene_id"], "correlation_id": correlation_id})
        return self.hydrate(user_id, session)

    def begin_transition(self, user_id: str, session_id: str, body: TransitionRequest) -> dict[str, Any]:
        if body.transition_key not in TRANSITIONS:
            raise StudioInvariantError("Unsupported transition")
        current = self.repo.get_session(user_id, session_id)
        if not current.get("preview_scene_id"):
            raise StudioInvariantError("No Preview scene is selected")
        snapshot = self._snapshot_scene(user_id, current["preview_scene_id"], current["profile_key"])
        transition = {
            "transition_id": uuid4().hex, "key": body.transition_key,
            "duration_ms": 0 if body.reduced_motion and body.transition_key != "cut" else body.duration_ms,
            "target_scene_id": current["preview_scene_id"], "target_snapshot": snapshot, "started_at": utc_now(),
        }
        return self.hydrate(user_id, self.repo.begin_transition(user_id, session_id, body.expected_version, transition))

    def complete_transition(self, user_id: str, session_id: str, body: TransitionComplete) -> dict[str, Any]:
        current = self.repo.get_session(user_id, session_id)
        transition = current.get("transition") or {}
        if current["transition_state"] != "in_progress" or transition.get("transition_id") != body.transition_id:
            raise StudioConflict("Transition token is stale or no longer active")
        snapshot = transition.get("target_snapshot") or {}
        correlation_id = uuid4().hex
        commit = self.transport.commit_programme(user_id, current.get("broadcast_id"), snapshot, correlation_id)
        if not commit.accepted:
            self.repo.abort_transition(user_id, session_id, body.expected_version, commit.reason)
            raise StudioTransportError(commit.reason or "Transport rejected programme transition")
        session = self.repo.complete_programme(user_id, session_id, body.expected_version, snapshot, commit.__dict__, body.transition_id)
        self.graph.event(user_id, current.get("broadcast_id"), "studio_transition", {"session_id": session_id, "transition_id": body.transition_id, "scene_id": session["programme_scene_id"], "correlation_id": correlation_id})
        return self.hydrate(user_id, session)

    def duplicate_scene(self, user_id: str, scene_id: str, name: str | None = None) -> dict[str, Any]:
        source = self.graph.scene(user_id, scene_id)
        duplicate = self.graph.create_scene(user_id, source["project_id"], SceneCreate(
            name=(name or f"{source['name']} Copy")[:120], layout_key=source.get("layout_key") or "solo",
            transition_key=source.get("transition_key") or "fade", transition_ms=int(source.get("transition_ms") or 350),
        ))
        for item in source.get("sources", []):
            config = dict(item.get("config") or {})
            validate_no_secrets(config)
            self.graph.create_source(user_id, duplicate["id"], SourceCreate(
                source_type=item["source_type"], name=item["name"], config=config,
                visible=bool(item.get("visible", True)), locked=bool(item.get("locked", False)), z_index=int(item.get("z_index", 0)),
            ))
        return self.graph.scene(user_id, duplicate["id"])

    def reorder_scenes(self, user_id: str, project_id: str, scene_ids: list[str]) -> list[dict[str, Any]]:
        project = self.graph.project(user_id, project_id)
        existing = [scene["id"] for scene in project.get("scenes", [])]
        if len(scene_ids) != len(existing) or set(scene_ids) != set(existing):
            raise StudioInvariantError("Scene reorder must contain every project scene exactly once")
        for position, scene_id in enumerate(scene_ids):
            self.graph.update_scene(user_id, scene_id, SceneUpdate(position=position))
        return self.graph.project(user_id, project_id).get("scenes", [])

    def update_transform(self, user_id: str, session_id: str, source_id: str, body: SourceTransformPatch) -> dict[str, Any]:
        session = self.repo.get_session(user_id, session_id)
        if session["version"] != body.expected_session_version:
            raise StudioConflict("Studio state changed in another tab/operator")
        source = self.graph.source(user_id, source_id)
        if source.get("project_id") != session["project_id"]:
            raise StudioInvariantError("Source does not belong to this studio project")
        config = dict(source.get("config") or {})
        config["transform"] = normalize_transform(body.transform)
        if body.effects is not None:
            config["effects"] = normalize_effects(body.effects)
        validate_no_secrets(config)
        updated = self.graph.update_source(user_id, source_id, SourceUpdate(config=config))
        bumped = self.repo.set_autosave_state(user_id, session_id, session["version"], {"reason": "source_transform", "source_id": source_id, "saved_at": utc_now()})
        return {"source": updated, "session": bumped}

    def update_audio(self, user_id: str, session_id: str, source_id: str, body: AudioPatch) -> dict[str, Any]:
        session = self.repo.get_session(user_id, session_id)
        if session["version"] != body.expected_session_version:
            raise StudioConflict("Studio state changed in another tab/operator")
        source = self.graph.source(user_id, source_id)
        if source.get("project_id") != session["project_id"]:
            raise StudioInvariantError("Source does not belong to this studio project")
        config = dict(source.get("config") or {})
        config["audio"] = normalize_audio(body.audio)
        validate_no_secrets(config)
        updated = self.graph.update_source(user_id, source_id, SourceUpdate(config=config))
        bumped = self.repo.set_autosave_state(user_id, session_id, session["version"], {"reason": "audio_mix", "source_id": source_id, "saved_at": utc_now()})
        return {"source": updated, "session": bumped}

    def widget_binding(self, kind: str, binding: dict[str, Any]) -> dict[str, Any]:
        if kind not in WIDGET_KINDS:
            raise StudioInvariantError("Unknown studio widget kind")
        validate_no_secrets(binding)
        return {"schema_version": 1, "kind": kind, "binding": binding, "authoritative_state_owned_externally": kind != "custom_text"}

    @staticmethod
    def layout_catalog() -> list[dict[str, Any]]:
        return [
            {"key": "solo", "slots": 1}, {"key": "side_by_side", "slots": 2}, {"key": "interview", "slots": 2},
            {"key": "grid", "slots": 8}, {"key": "speaker_focus", "slots": 8}, {"key": "host_guests", "slots": 8},
            {"key": "picture_in_picture", "slots": 2}, {"key": "vertical_stack", "slots": 8}, {"key": "battle_teams", "slots": 8},
        ]


studio_repo = StudioRepository(shared_sky.db_path)
studio = StudioService(studio_repo, shared_sky)


def _member(request: Request):
    return require_esp_hub_member(request)


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        return HTTPException(404, "Shared Sky studio resource not found")
    if isinstance(exc, StudioConflict):
        return HTTPException(409, str(exc))
    if isinstance(exc, StudioTransportError):
        return HTTPException(503, str(exc))
    if isinstance(exc, StudioInvariantError):
        return HTTPException(400, str(exc))
    return HTTPException(500, "Shared Sky studio operation failed")


@router.post("/shared-sky/studio/api/sessions")
def create_studio_session(body: StudioSessionCreate, request: Request):
    member, _ = _member(request)
    try:
        return studio.create_session(member.user_id, body)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get("/shared-sky/studio/api/sessions/{session_id}")
def get_studio_session(session_id: str, request: Request):
    member, _ = _member(request)
    try:
        return studio.session(member.user_id, session_id)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get("/shared-sky/studio/api/sessions/{session_id}/versions")
def get_studio_versions(session_id: str, request: Request):
    member, _ = _member(request)
    try:
        return {"versions": studio_repo.versions(member.user_id, session_id)}
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/shared-sky/studio/api/sessions/{session_id}/preview")
def select_preview(session_id: str, body: PreviewSelect, request: Request):
    member, _ = _member(request)
    try:
        return studio.select_preview(member.user_id, session_id, body)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/shared-sky/studio/api/sessions/{session_id}/cut")
def cut_programme(session_id: str, body: CutRequest, request: Request):
    member, _ = _member(request)
    try:
        return studio.cut(member.user_id, session_id, body.expected_version)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/shared-sky/studio/api/sessions/{session_id}/transition")
def begin_transition(session_id: str, body: TransitionRequest, request: Request):
    member, _ = _member(request)
    try:
        return studio.begin_transition(member.user_id, session_id, body)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/shared-sky/studio/api/sessions/{session_id}/transition/complete")
def complete_transition(session_id: str, body: TransitionComplete, request: Request):
    member, _ = _member(request)
    try:
        return studio.complete_transition(member.user_id, session_id, body)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/shared-sky/studio/api/scenes/{scene_id}/duplicate")
def duplicate_scene(scene_id: str, body: SceneDuplicateRequest, request: Request):
    member, _ = _member(request)
    try:
        return {"scene": studio.duplicate_scene(member.user_id, scene_id, body.name)}
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/shared-sky/studio/api/projects/{project_id}/scenes/reorder")
def reorder_scenes(project_id: str, body: SceneReorderRequest, request: Request):
    member, _ = _member(request)
    try:
        return {"scenes": studio.reorder_scenes(member.user_id, project_id, body.scene_ids)}
    except Exception as exc:
        raise _http_error(exc) from exc


@router.patch("/shared-sky/studio/api/sessions/{session_id}/sources/{source_id}/transform")
def patch_source_transform(session_id: str, source_id: str, body: SourceTransformPatch, request: Request):
    member, _ = _member(request)
    try:
        return studio.update_transform(member.user_id, session_id, source_id, body)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.patch("/shared-sky/studio/api/sessions/{session_id}/sources/{source_id}/audio")
def patch_source_audio(session_id: str, source_id: str, body: AudioPatch, request: Request):
    member, _ = _member(request)
    try:
        return studio.update_audio(member.user_id, session_id, source_id, body)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get("/shared-sky/studio/api/sessions/{session_id}/recording")
def recording_state(session_id: str, request: Request):
    member, _ = _member(request)
    try:
        current = studio_repo.get_session(member.user_id, session_id)
        return studio.transport.recording_capabilities(member.user_id, current.get("broadcast_id"))
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get("/shared-sky/studio/api/layout/{layout_key}/{count}")
def layout_preview(layout_key: str, count: int, profile_key: str, request: Request):
    _member(request)
    try:
        return {"layout": participant_layout(layout_key, count, profile_key), "profile": PROFILE_REGISTRY[profile_key]}
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/shared-sky/studio/api/projects/{project_id}/brand-kits")
def create_brand_kit(project_id: str, body: BrandKitUpsert, request: Request):
    member, _ = _member(request)
    try:
        return {"brand_kit": studio_repo.upsert_brand_kit(member.user_id, project_id, body)}
    except Exception as exc:
        raise _http_error(exc) from exc


@router.put("/shared-sky/studio/api/projects/{project_id}/brand-kits/{kit_id}")
def update_brand_kit(project_id: str, kit_id: str, body: BrandKitUpsert, request: Request):
    member, _ = _member(request)
    try:
        return {"brand_kit": studio_repo.upsert_brand_kit(member.user_id, project_id, body, kit_id)}
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get("/shared-sky/studio/api/projects/{project_id}/brand-kits")
def list_brand_kits(project_id: str, request: Request):
    member, _ = _member(request)
    try:
        return {"brand_kits": studio_repo.brand_kits(member.user_id, project_id)}
    except Exception as exc:
        raise _http_error(exc) from exc


STUDIO_CSS = """
:root{--bg:#061019;--panel:#0b1925;--line:#ffffff24;--text:#f5fbff;--muted:#a9bfca;--sky:#5eead4;--amber:#f9d071;--red:#ff6b7a;--green:#7ef0a5}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font-family:Inter,system-ui,sans-serif}.studio{min-height:100vh;display:grid;grid-template-columns:220px minmax(0,1fr) 300px;grid-template-rows:auto 1fr auto;gap:10px;padding:10px}.bar,.panel,.monitor{background:var(--panel);border:1px solid var(--line);border-radius:14px}.bar{grid-column:1/-1;padding:10px 14px;display:flex;gap:10px;align-items:center;flex-wrap:wrap}.scenes{grid-row:2;grid-column:1;padding:10px}.work{grid-row:2;grid-column:2;display:grid;gap:10px;min-width:0}.inspector{grid-row:2;grid-column:3;padding:10px}.mixer{grid-column:1/-1;padding:10px;display:flex;gap:8px;overflow:auto}.monitors{display:grid;grid-template-columns:1fr 1fr;gap:10px}.monitor{position:relative;aspect-ratio:16/9;overflow:hidden;background:#02080d}.monitor h2{position:absolute;z-index:2;left:8px;top:6px;margin:0;font-size:.75rem;padding:4px 7px;background:#000b}.programme{outline:2px solid var(--red)}.preview{outline:2px solid var(--green)}.canvas{position:absolute;inset:0}.source{position:absolute;overflow:hidden;transform-origin:center}.source.selected{border:1px solid var(--sky)}.scene{width:100%;display:block;text-align:left;margin:5px 0;padding:9px;border-radius:10px;border:1px solid var(--line);background:#ffffff08;color:var(--text)}.scene.active{border-color:var(--green)}button,input,select{font:inherit}.btn,button{background:#102638;border:1px solid var(--line);color:var(--text);padding:8px 11px;border-radius:9px;font-weight:750}.take{background:var(--red);color:#160408}.cut{background:var(--amber);color:#171004}.state,.muted{font-size:.82rem;color:var(--muted)}.channel{min-width:150px;border:1px solid var(--line);border-radius:10px;padding:8px}.meter{height:8px;background:#0008;border-radius:5px;overflow:hidden}.meter>i{display:block;height:100%;width:0}.meter.unavailable{opacity:.45}.notice{border-left:3px solid var(--amber);padding:9px;background:#f9d07112}:focus-visible{outline:3px solid var(--sky);outline-offset:2px}@media(max-width:980px){.studio{grid-template-columns:170px 1fr}.inspector{grid-column:1/-1;grid-row:3}.mixer{grid-row:4}.monitors{grid-template-columns:1fr}}@media(max-width:680px){.studio{display:block}.panel,.bar,.work{margin-bottom:10px}.inspector{display:none}.monitors{display:block}.monitor{margin-bottom:10px}}
"""

STUDIO_JS = r"""
const state={session:null,project:null,transport:null,selectedSource:null};const $=s=>document.querySelector(s),$$=s=>[...document.querySelectorAll(s)];
function esc(v){return String(v??'').replace(/[&<>\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[c]));}
async function api(url,opt={}){const r=await fetch(url,{headers:{'Content-Type':'application/json'},...opt});let d={};try{d=await r.json()}catch{}if(!r.ok)throw new Error(d.detail||`Request failed (${r.status})`);return d}
function scene(id){return state.project?.scenes?.find(s=>s.id===id)}
function style(src){const t=src.config?.transform||{},e=src.config?.effects||{};return `left:${(t.x||0)*100}%;top:${(t.y||0)*100}%;width:${(t.width??1)*100}%;height:${(t.height??1)*100}%;opacity:${t.opacity??1};transform:rotate(${t.rotation||0}deg);filter:brightness(${e.brightness??1}) contrast(${e.contrast??1}) saturate(${e.saturation??1}) hue-rotate(${e.hue??0}deg) blur(${e.blur??0}px);border-radius:${(e.rounded??0)*100}%`}
function node(src,programme=false){if(!src.visible)return'';return `<div class='source ${!programme&&state.selectedSource===src.id?'selected':''}' data-source='${esc(src.id)}' style='${style(src)}'><div style='display:grid;place-items:center;width:100%;height:100%;background:#102638'>${esc(src.name)}</div></div>`}
function assign(d){state.session=d.session;state.project=d.project;state.transport=d.transport;render()}
function render(){const s=state.session,p=state.project;if(!s||!p)return;$('#version').textContent=`v${s.version}`;$('#transport').textContent=`Transport: ${state.transport?.state||'unknown'}`;$('#transitionState').textContent=s.transition_state==='idle'?'Ready':`Transition ${s.transition?.key||''} in progress`;$('#sceneList').innerHTML=p.scenes.map(sc=>`<button class='scene ${sc.id===s.preview_scene_id?'active':''}' data-scene='${sc.id}'>${esc(sc.name)}</button>`).join('');const prev=scene(s.preview_scene_id);$('#previewCanvas').innerHTML=(prev?.sources||[]).map(x=>node(x)).join('');const prog=s.programme_snapshot||{};$('#programmeCanvas').innerHTML=(prog.sources||[]).map(x=>node(x,true)).join('')||`<div class='muted' style='display:grid;place-items:center;height:100%'>OFF AIR / no programme snapshot</div>`;renderMixer(prev?.sources||[]);$$('[data-scene]').forEach(b=>b.onclick=()=>selectPreview(b.dataset.scene));$$('#previewCanvas [data-source]').forEach(n=>n.onclick=()=>{state.selectedSource=n.dataset.source;render();renderInspector()});renderInspector()}
async function selectPreview(id){try{assign(await api(`/shared-sky/studio/api/sessions/${state.session.id}/preview`,{method:'POST',body:JSON.stringify({scene_id:id,expected_version:state.session.version})}))}catch(e){await conflict(e)}}
async function cut(){try{assign(await api(`/shared-sky/studio/api/sessions/${state.session.id}/cut`,{method:'POST',body:JSON.stringify({expected_version:state.session.version})}))}catch(e){await conflict(e)}}
async function transition(){try{assign(await api(`/shared-sky/studio/api/sessions/${state.session.id}/transition`,{method:'POST',body:JSON.stringify({expected_version:state.session.version,transition_key:$('#transitionKey').value,duration_ms:Number($('#duration').value),reduced_motion:matchMedia('(prefers-reduced-motion: reduce)').matches})}));const token=state.session.transition.transition_id,d=state.session.transition.duration_ms||0;setTimeout(async()=>{try{assign(await api(`/shared-sky/studio/api/sessions/${state.session.id}/transition/complete`,{method:'POST',body:JSON.stringify({expected_version:state.session.version,transition_id:token})}))}catch(e){await conflict(e)}},d)}catch(e){await conflict(e)}}
async function conflict(e){$('#alert').textContent=e.message;if(/version conflict|another tab/i.test(e.message)){assign(await api(`/shared-sky/studio/api/sessions/${state.session.id}`))}}
function renderInspector(){const src=(scene(state.session.preview_scene_id)?.sources||[]).find(x=>x.id===state.selectedSource);if(!src){$('#inspectorBody').innerHTML='<p class=muted>Select a Preview source. Programme remains isolated.</p>';return}const t=src.config?.transform||{};$('#inspectorBody').innerHTML=`<h3>${esc(src.name)}</h3><label>X <input id=tx type=number step=.01 value='${t.x||0}'></label><label>Y <input id=ty type=number step=.01 value='${t.y||0}'></label><label>Width <input id=tw type=number step=.01 value='${t.width??1}'></label><label>Height <input id=th type=number step=.01 value='${t.height??1}'></label><button id=saveTransform>Save transform</button><p class=muted>Preview only until CUT/TRANSITION.</p>`;$('#saveTransform').onclick=()=>saveTransform(src)}
async function saveTransform(src){try{const d=await api(`/shared-sky/studio/api/sessions/${state.session.id}/sources/${src.id}/transform`,{method:'PATCH',body:JSON.stringify({expected_session_version:state.session.version,transform:{x:Number($('#tx').value),y:Number($('#ty').value),width:Number($('#tw').value),height:Number($('#th').value)}})});state.session=d.session;assign(await api(`/shared-sky/studio/api/sessions/${state.session.id}`))}catch(e){await conflict(e)}}
function renderMixer(sources){const audio=sources.filter(x=>['microphone','audio','desktop_audio','application_audio','video'].includes(x.source_type));$('#mixer').innerHTML=audio.length?audio.map(src=>`<div class=channel data-channel='${src.id}'><b>${esc(src.name)}</b><div class='meter unavailable' aria-label='Signal meter unavailable until a real browser source is attached'><i></i></div><label>Gain <input type=range min=0 max=2 step=.01 value='${src.config?.audio?.gain??1}'></label></div>`).join(''):'<span class=muted>No audio sources in Preview.</span>'}
async function attachMeter(sourceId,stream){const ctx=new AudioContext(),src=ctx.createMediaStreamSource(stream),hp=ctx.createBiquadFilter(),comp=ctx.createDynamicsCompressor(),gain=ctx.createGain(),analyser=ctx.createAnalyser();hp.type='highpass';hp.frequency.value=80;analyser.fftSize=1024;src.connect(hp).connect(comp).connect(gain).connect(analyser);const data=new Float32Array(analyser.fftSize),channel=$(`[data-channel='${sourceId}'] .meter`);channel?.classList.remove('unavailable');function tick(){analyser.getFloatTimeDomainData(data);let sum=0;for(const v of data)sum+=v*v;const rms=Math.sqrt(sum/data.length),pct=Math.min(100,Math.max(0,(20*Math.log10(Math.max(rms,1e-7))+60)/60*100)),bar=channel?.querySelector('i');if(bar)bar.style.width=`${pct}%`;if(stream.getTracks().some(t=>t.readyState==='live'))requestAnimationFrame(tick)}tick();return()=>{stream.getTracks().forEach(t=>t.stop());ctx.close()}}
function hotkeySafe(e){const el=e.target;if(el&&(el.matches('input,textarea,select,[contenteditable=true]')||el.closest?.('[contenteditable=true]')))return false;return true}
document.addEventListener('keydown',e=>{if(!hotkeySafe(e))return;if(e.key==='Enter'&&e.ctrlKey){e.preventDefault();transition()}if(e.key==='c'&&e.altKey){e.preventDefault();cut()}if(e.key==='b'&&e.altKey){e.preventDefault();$('#alert').textContent='Emergency BRB requires an explicitly configured BRB scene; no automatic on-air change was made.'}});$('#cut').onclick=cut;$('#take').onclick=transition;$('#refresh').onclick=async()=>assign(await api(`/shared-sky/studio/api/sessions/${state.session.id}`));
(async()=>assign(await api('/shared-sky/studio/api/sessions',{method:'POST',body:JSON.stringify({project_id:document.body.dataset.project,profile_key:document.body.dataset.profile||'landscape-1080'})})))().catch(e=>$('#alert').textContent=e.message);
"""


def studio_html(project_id: str, profile_key: str) -> str:
    return f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>Shared Sky Professional Studio</title><style>{STUDIO_CSS}</style></head><body data-project='{escape(project_id, quote=True)}' data-profile='{escape(profile_key, quote=True)}'><main class='studio'>
<header class='bar'><strong>Shared Sky · Professional Control Room</strong><span id='version'>v—</span><span id='transitionState'>Loading</span><span id='transport'>Transport: checking</span><span id='alert' class='notice' role='status' aria-live='polite'>Preview edits never change Programme until CUT/TRANSITION succeeds.</span><button id='refresh'>Refresh</button></header>
<aside class='scenes panel' aria-label='Scenes'><h2>Scenes</h2><div id='sceneList'></div><p class='muted'>Stable scene IDs, copy/reorder and typed source state use the canonical Shared Sky project graph.</p></aside>
<section class='work'><div class='monitors'><section class='monitor preview' aria-label='Preview monitor'><h2>PREVIEW</h2><div id='previewCanvas' class='canvas'></div></section><section class='monitor programme' aria-label='Programme monitor'><h2>PROGRAMME</h2><div id='programmeCanvas' class='canvas'></div></section></div><div class='bar'><label>Transition <select id='transitionKey'><option>fade</option><option>dip_to_colour</option><option>slide</option><option>push</option><option>zoom</option></select></label><label>ms <input id='duration' type='number' min='0' max='20000' value='350' style='width:90px'></label><button id='cut' class='cut'>CUT</button><button id='take' class='take'>TRANSITION</button></div></section>
<aside class='inspector panel' aria-label='Source inspector'><h2>Inspector</h2><div id='inspectorBody'></div><hr><h3>Operator safety</h3><p class='muted'>Stale writes return 409. Private/backstage sources and secret-bearing configs are blocked from Programme. Live commits fail closed until Chat 2 provides its authoritative Programme commit contract.</p></aside>
<section class='mixer panel' id='mixer' aria-label='Audio mixer'><span class='muted'>Audio mixer loading…</span></section></main><script>{STUDIO_JS}</script></body></html>"""


@router.get("/shared-sky/studio", response_class=HTMLResponse, include_in_schema=False)
def studio_page(project_id: str, request: Request, profile_key: str = "landscape-1080"):
    member, _ = _member(request)
    if profile_key not in PROFILE_REGISTRY:
        raise HTTPException(400, "Unknown Shared Sky production profile")
    try:
        shared_sky.project(member.user_id, project_id)
    except KeyError as exc:
        raise HTTPException(404, "Shared Sky project not found") from exc
    return HTMLResponse(studio_html(project_id, profile_key), headers={"Cache-Control": "no-store"})


def install_shared_sky_control_room(app: Any) -> None:
    """Mount Chat 3 routes once on the canonical FastAPI application."""
    existing = {getattr(route, "path", "") for route in app.router.routes}
    if "/shared-sky/studio/api/sessions" not in existing:
        app.include_router(router)


__all__ = [
    "PROFILE_REGISTRY", "SharedSkyTransportCompatibilityAdapter", "StudioConflict", "StudioInvariantError",
    "StudioRepository", "StudioService", "StudioTransportError", "calculate_audio_meter", "install_shared_sky_control_room",
    "normalize_audio", "normalize_effects", "normalize_transform", "participant_layout", "programme_safe_source", "router",
    "studio", "studio_repo", "validate_no_secrets", "validate_web_source_url",
]
