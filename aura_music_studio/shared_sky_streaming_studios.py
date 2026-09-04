from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from html import escape
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .esp_command_center import EspStore, esp
from .esp_niche import require_esp_hub_member
from .owner_identity import owner_session_authorized
from .shared_sky_relay import SharedSkyRelayError, relay
from .shared_sky_security import SharedSkyVault, SharedSkyVaultError

router = APIRouter(tags=["Shared Sky Streaming Studios"])

PRODUCT_NAME = "Shared Sky Streaming Studios"
PRODUCT_ENDORSEMENT = "Elevate Souls Productions"

WORKSPACE_MODES = ["beginner", "professional", "advanced"]
CANVAS_MODES = ["landscape", "portrait", "dual", "square", "custom"]

PLATFORM_REGISTRY: list[dict] = [
    {
        "id": "youtube",
        "name": "YouTube Live",
        "connection_modes": ["oauth", "stream_key"],
        "implementation": "adapter_required",
        "capabilities": ["live", "scheduling", "metadata", "chat", "analytics", "landscape", "vertical"],
    },
    {
        "id": "facebook",
        "name": "Facebook Live",
        "connection_modes": ["oauth", "stream_key"],
        "implementation": "adapter_required",
        "capabilities": ["live", "metadata", "chat", "analytics"],
    },
    {
        "id": "twitch",
        "name": "Twitch",
        "connection_modes": ["oauth", "stream_key"],
        "implementation": "adapter_required",
        "capabilities": ["live", "metadata", "chat", "analytics", "extensions"],
    },
    {
        "id": "tiktok",
        "name": "TikTok LIVE",
        "connection_modes": ["platform_authorised", "stream_key_when_account_provides"],
        "implementation": "platform_review_or_account_access_required",
        "capabilities": ["live", "vertical", "chat_when_authorised", "engagement_when_authorised"],
    },
    {
        "id": "instagram",
        "name": "Instagram Live",
        "connection_modes": ["platform_authorised", "stream_key_when_account_provides"],
        "implementation": "platform_review_or_account_access_required",
        "capabilities": ["live", "vertical"],
    },
    {
        "id": "linkedin",
        "name": "LinkedIn Live",
        "connection_modes": ["oauth", "stream_key_when_eligible"],
        "implementation": "adapter_required",
        "capabilities": ["live", "scheduling", "metadata"],
    },
    {
        "id": "kick",
        "name": "Kick",
        "connection_modes": ["stream_key_when_account_provides"],
        "implementation": "custom_rtmp_supported",
        "capabilities": ["live"],
    },
    {
        "id": "rumble",
        "name": "Rumble",
        "connection_modes": ["stream_key_when_account_provides"],
        "implementation": "custom_rtmp_supported",
        "capabilities": ["live"],
    },
    {
        "id": "vimeo",
        "name": "Vimeo Live",
        "connection_modes": ["stream_key_when_account_provides"],
        "implementation": "custom_rtmp_supported",
        "capabilities": ["live", "events"],
    },
    {
        "id": "trovo",
        "name": "Trovo",
        "connection_modes": ["stream_key_when_account_provides"],
        "implementation": "custom_rtmp_supported",
        "capabilities": ["live"],
    },
    {
        "id": "dlive",
        "name": "DLive",
        "connection_modes": ["stream_key_when_account_provides"],
        "implementation": "custom_rtmp_supported",
        "capabilities": ["live"],
    },
    {
        "id": "x",
        "name": "X / Live Video",
        "connection_modes": ["platform_authorised", "custom_endpoint_when_provided"],
        "implementation": "platform_access_required",
        "capabilities": ["live_when_authorised"],
    },
    {
        "id": "custom-rtmp",
        "name": "Custom RTMP / RTMPS",
        "connection_modes": ["custom_rtmp"],
        "implementation": "framework_ready",
        "capabilities": ["live", "custom_endpoint"],
    },
    {
        "id": "custom-srt",
        "name": "Custom SRT",
        "connection_modes": ["custom_srt"],
        "implementation": "framework_ready",
        "capabilities": ["live", "custom_endpoint"],
    },
]

SOURCE_CATALOG = [
    "camera", "capture_card", "screen", "window", "game_capture", "browser", "image",
    "slideshow", "video", "audio", "microphone", "desktop_audio", "application_audio",
    "text", "rich_text", "shape", "gradient", "clock", "countdown", "stopwatch",
    "website_widget", "chat_overlay", "alert_overlay", "goal_bar", "poll", "qa",
    "captions", "remote_guest", "remote_mobile_camera", "ndi", "srt", "presentation",
    "music_visualiser", "avatar", "scene_forge", "game_forge", "aura_generated", "data_graphic",
]

EFFECT_CATALOG = [
    "crop", "pad", "scale", "rotate", "skew", "perspective", "chroma_key", "luma_key",
    "colour_key", "colour_correction", "lut", "exposure", "contrast", "saturation",
    "temperature", "tint", "gamma", "sharpen", "blur", "glow", "bloom", "vignette",
    "grain", "mask", "blend_mode", "background_removal", "background_blur",
    "background_replace", "auto_frame", "face_track", "skin_smoothing", "lens_emulation",
    "depth_parallax", "motion_blur", "edge_neon", "pixelate", "cartoon", "particles",
    "light_rays", "lens_flare", "volumetric_beams", "prism", "bokeh", "virtual_set",
]

TRANSITION_CATALOG = [
    "cut", "fade", "dip_to_colour", "swipe", "slide", "push", "zoom", "camera_move",
    "luma_wipe", "shape_wipe", "stinger", "3d", "motion_blur", "glitch", "light_flash",
    "prism", "particle", "custom_video", "audio_linked", "beat_sync",
]

AUDIO_CATALOG = [
    "gain", "pan", "mute", "solo", "monitor", "noise_suppression", "noise_gate", "expander",
    "compressor", "limiter", "de_esser", "eq", "high_pass", "low_pass", "reverb", "delay",
    "voice_enhance", "ducking", "auto_gain", "loudness_meter", "sync_delay", "soundboard",
    "audio_reactive_visuals",
]

LAYOUT_CATALOG = [
    {"id": "solo", "name": "Solo", "slots": 1},
    {"id": "split", "name": "Split Screen", "slots": 2},
    {"id": "interview", "name": "Interview", "slots": 2},
    {"id": "grid-4", "name": "Grid 4", "slots": 4},
    {"id": "grid-9", "name": "Grid 9", "slots": 9},
    {"id": "host-focus", "name": "Host + Guests", "slots": 6},
    {"id": "presentation", "name": "Presentation + Speaker", "slots": 2},
    {"id": "gaming", "name": "Game + Camera", "slots": 2},
    {"id": "vertical-live", "name": "Vertical LIVE", "slots": 3},
    {"id": "dual-output", "name": "Dual Landscape + Portrait", "slots": 8},
]

MENU = [
    "Home", "Go Live", "Studio Canvas", "Scenes", "Sources", "Layouts", "Transitions",
    "Visual Effects", "Audio Mixer & Effects", "Brand Kit", "Overlays & Widgets",
    "Guests & Green Room", "Unified Chat", "Engagement", "Destinations", "Schedules",
    "Pre-Recorded Live", "Recordings", "Clips & Repurpose", "Analytics", "Templates",
    "Media Library", "Effect Library", "Soundboard", "Plugins & Integrations",
    "Connection Health", "Stream Tests", "Settings", "Help",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value, fallback):
    try:
        return json.loads(value) if isinstance(value, str) else value
    except Exception:
        return fallback


def _validate_endpoint(endpoint: str) -> str:
    clean = (endpoint or "").strip()
    if not clean:
        return ""
    allowed = ("rtmp://", "rtmps://", "srt://", "rist://")
    if not clean.lower().startswith(allowed):
        raise ValueError("Destination endpoint must use RTMP, RTMPS, SRT or RIST")
    if any(ch in clean for ch in ("\n", "\r", "\x00")):
        raise ValueError("Invalid destination endpoint")
    return clean[:2000]


def _platform(platform_id: str) -> dict:
    return next((row for row in PLATFORM_REGISTRY if row["id"] == platform_id), {
        "id": platform_id,
        "name": platform_id.replace("-", " ").title(),
        "connection_modes": ["custom_rtmp"],
        "implementation": "custom_configuration_required",
        "capabilities": ["live_when_endpoint_provided"],
    })


class ProjectCreate(BaseModel):
    name: str = Field(default="Untitled Shared Sky Show", min_length=1, max_length=160)
    description: str = Field(default="", max_length=1500)
    workspace_mode: Literal["beginner", "professional", "advanced"] = "professional"
    canvas_mode: Literal["landscape", "portrait", "dual", "square", "custom"] = "dual"


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=1500)
    workspace_mode: Literal["beginner", "professional", "advanced"] | None = None
    canvas_mode: Literal["landscape", "portrait", "dual", "square", "custom"] | None = None


class SceneCreate(BaseModel):
    name: str = Field(default="Scene", min_length=1, max_length=120)
    layout_key: str = Field(default="solo", max_length=80)
    transition_key: str = Field(default="fade", max_length=80)
    transition_ms: int = Field(default=350, ge=0, le=20000)


class SceneUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    layout_key: str | None = Field(default=None, max_length=80)
    transition_key: str | None = Field(default=None, max_length=80)
    transition_ms: int | None = Field(default=None, ge=0, le=20000)
    position: int | None = Field(default=None, ge=0, le=10000)


class SourceCreate(BaseModel):
    source_type: str = Field(min_length=1, max_length=80)
    name: str = Field(default="Source", min_length=1, max_length=120)
    config: dict = Field(default_factory=dict)
    visible: bool = True
    locked: bool = False
    z_index: int = Field(default=0, ge=-10000, le=10000)


class SourceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    config: dict | None = None
    visible: bool | None = None
    locked: bool | None = None
    z_index: int | None = Field(default=None, ge=-10000, le=10000)


class DestinationCreate(BaseModel):
    platform_id: str = Field(default="custom-rtmp", min_length=1, max_length=80)
    label: str = Field(default="Streaming Destination", min_length=1, max_length=160)
    auth_mode: Literal["oauth", "stream_key", "custom_rtmp", "custom_srt", "platform_authorised", "manual"] = "custom_rtmp"
    endpoint: str = Field(default="", max_length=2000)
    credential: str = Field(default="", max_length=4000)
    enabled: bool = True
    metadata: dict = Field(default_factory=dict)


class DestinationUpdate(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=160)
    endpoint: str | None = Field(default=None, max_length=2000)
    credential: str | None = Field(default=None, max_length=4000)
    enabled: bool | None = None
    metadata: dict | None = None


class BroadcastCreate(BaseModel):
    project_id: str
    title: str = Field(default="Shared Sky LIVE", min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    destination_ids: list[str] = Field(default_factory=list, max_length=50)
    passthrough: bool = True


class ScheduleCreate(BaseModel):
    project_id: str
    title: str = Field(default="Scheduled LIVE", min_length=1, max_length=200)
    start_at: str = Field(min_length=10, max_length=80)
    destination_ids: list[str] = Field(default_factory=list, max_length=50)
    mode: Literal["live", "pre_recorded"] = "live"


class SharedSkyStore:
    def __init__(self, esp_store: EspStore | None = None, vault: SharedSkyVault | None = None):
        self.esp = esp_store or esp
        self.db_path = self.esp.db_path
        self.vault = vault or SharedSkyVault()
        self._init_schema()

    def _connect(self):
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS shared_sky_projects (
                    id TEXT PRIMARY KEY,user_id TEXT NOT NULL,name TEXT NOT NULL,description TEXT NOT NULL DEFAULT '',
                    workspace_mode TEXT NOT NULL DEFAULT 'professional',canvas_mode TEXT NOT NULL DEFAULT 'dual',
                    created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_shared_sky_projects_user ON shared_sky_projects(user_id,updated_at DESC);
                CREATE TABLE IF NOT EXISTS shared_sky_scenes (
                    id TEXT PRIMARY KEY,project_id TEXT NOT NULL,user_id TEXT NOT NULL,name TEXT NOT NULL,position INTEGER NOT NULL DEFAULT 0,
                    layout_key TEXT NOT NULL DEFAULT 'solo',transition_key TEXT NOT NULL DEFAULT 'fade',transition_ms INTEGER NOT NULL DEFAULT 350,
                    created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES shared_sky_projects(id) ON DELETE CASCADE,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_shared_sky_scenes_project ON shared_sky_scenes(project_id,position,id);
                CREATE TABLE IF NOT EXISTS shared_sky_sources (
                    id TEXT PRIMARY KEY,scene_id TEXT NOT NULL,project_id TEXT NOT NULL,user_id TEXT NOT NULL,source_type TEXT NOT NULL,name TEXT NOT NULL,
                    config_json TEXT NOT NULL DEFAULT '{}',visible INTEGER NOT NULL DEFAULT 1,locked INTEGER NOT NULL DEFAULT 0,z_index INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
                    FOREIGN KEY(scene_id) REFERENCES shared_sky_scenes(id) ON DELETE CASCADE,
                    FOREIGN KEY(project_id) REFERENCES shared_sky_projects(id) ON DELETE CASCADE,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_shared_sky_sources_scene ON shared_sky_sources(scene_id,z_index,id);
                CREATE TABLE IF NOT EXISTS shared_sky_destinations (
                    id TEXT PRIMARY KEY,user_id TEXT NOT NULL,platform_id TEXT NOT NULL,label TEXT NOT NULL,auth_mode TEXT NOT NULL,
                    endpoint TEXT NOT NULL DEFAULT '',credential_ciphertext TEXT NOT NULL DEFAULT '',enabled INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL DEFAULT 'configured',metadata_json TEXT NOT NULL DEFAULT '{}',created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_shared_sky_destinations_user ON shared_sky_destinations(user_id,updated_at DESC);
                CREATE TABLE IF NOT EXISTS shared_sky_broadcasts (
                    id TEXT PRIMARY KEY,user_id TEXT NOT NULL,project_id TEXT NOT NULL,title TEXT NOT NULL,description TEXT NOT NULL DEFAULT '',
                    state TEXT NOT NULL DEFAULT 'draft',destination_ids_json TEXT NOT NULL DEFAULT '[]',passthrough INTEGER NOT NULL DEFAULT 1,
                    started_at TEXT,ended_at TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES shared_sky_projects(id) ON DELETE CASCADE,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_shared_sky_broadcasts_user ON shared_sky_broadcasts(user_id,created_at DESC);
                CREATE TABLE IF NOT EXISTS shared_sky_broadcast_outputs (
                    id TEXT PRIMARY KEY,broadcast_id TEXT NOT NULL,destination_id TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'pending',
                    process_id INTEGER,last_error TEXT NOT NULL DEFAULT '',started_at TEXT,ended_at TEXT,updated_at TEXT NOT NULL,
                    FOREIGN KEY(broadcast_id) REFERENCES shared_sky_broadcasts(id) ON DELETE CASCADE,
                    FOREIGN KEY(destination_id) REFERENCES shared_sky_destinations(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_shared_sky_outputs_broadcast ON shared_sky_broadcast_outputs(broadcast_id,updated_at DESC);
                CREATE TABLE IF NOT EXISTS shared_sky_schedules (
                    id TEXT PRIMARY KEY,user_id TEXT NOT NULL,project_id TEXT NOT NULL,title TEXT NOT NULL,start_at TEXT NOT NULL,
                    destination_ids_json TEXT NOT NULL DEFAULT '[]',mode TEXT NOT NULL DEFAULT 'live',state TEXT NOT NULL DEFAULT 'scheduled',
                    created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES shared_sky_projects(id) ON DELETE CASCADE,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_shared_sky_schedules_user ON shared_sky_schedules(user_id,start_at);
                CREATE TABLE IF NOT EXISTS shared_sky_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT NOT NULL,broadcast_id TEXT,event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',created_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_shared_sky_events_broadcast ON shared_sky_events(broadcast_id,id DESC);
                """
            )

    def _owned(self, table: str, row_id: str, user_id: str) -> dict:
        allowed = {
            "shared_sky_projects", "shared_sky_scenes", "shared_sky_sources",
            "shared_sky_destinations", "shared_sky_broadcasts", "shared_sky_schedules",
        }
        if table not in allowed:
            raise KeyError(table)
        with self._connect() as con:
            row = con.execute(f"SELECT * FROM {table} WHERE id=? AND user_id=?", (row_id, user_id)).fetchone()
        if not row:
            raise KeyError(row_id)
        return dict(row)

    def create_project(self, user_id: str, body: ProjectCreate) -> dict:
        now = _now(); project_id = uuid4().hex
        with self._connect() as con:
            con.execute(
                "INSERT INTO shared_sky_projects(id,user_id,name,description,workspace_mode,canvas_mode,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (project_id,user_id,body.name.strip(),body.description.strip(),body.workspace_mode,body.canvas_mode,now,now),
            )
        project = self.project(user_id, project_id)
        if not project["scenes"]:
            self.create_scene(user_id, project_id, SceneCreate(name="Opening Scene", layout_key="solo", transition_key="fade"))
        return self.project(user_id, project_id)

    def list_projects(self, user_id: str) -> list[dict]:
        with self._connect() as con:
            rows = con.execute("SELECT * FROM shared_sky_projects WHERE user_id=? ORDER BY updated_at DESC", (user_id,)).fetchall()
        return [dict(row) for row in rows]

    def project(self, user_id: str, project_id: str) -> dict:
        row = self._owned("shared_sky_projects", project_id, user_id)
        row["scenes"] = self.list_scenes(user_id, project_id)
        return row

    def update_project(self, user_id: str, project_id: str, body: ProjectUpdate) -> dict:
        current = self._owned("shared_sky_projects", project_id, user_id)
        values = {
            "name": body.name.strip() if body.name is not None else current["name"],
            "description": body.description.strip() if body.description is not None else current["description"],
            "workspace_mode": body.workspace_mode or current["workspace_mode"],
            "canvas_mode": body.canvas_mode or current["canvas_mode"],
        }
        with self._connect() as con:
            con.execute("UPDATE shared_sky_projects SET name=?,description=?,workspace_mode=?,canvas_mode=?,updated_at=? WHERE id=? AND user_id=?",
                        (values["name"],values["description"],values["workspace_mode"],values["canvas_mode"],_now(),project_id,user_id))
        return self.project(user_id, project_id)

    def delete_project(self, user_id: str, project_id: str) -> None:
        self._owned("shared_sky_projects", project_id, user_id)
        with self._connect() as con:
            con.execute("DELETE FROM shared_sky_projects WHERE id=? AND user_id=?", (project_id,user_id))

    def create_scene(self, user_id: str, project_id: str, body: SceneCreate) -> dict:
        self._owned("shared_sky_projects", project_id, user_id)
        if body.transition_key not in TRANSITION_CATALOG:
            raise ValueError("Unknown Shared Sky transition")
        with self._connect() as con:
            position = int(con.execute("SELECT COALESCE(MAX(position),-1)+1 FROM shared_sky_scenes WHERE project_id=?", (project_id,)).fetchone()[0])
            scene_id = uuid4().hex; now = _now()
            con.execute("INSERT INTO shared_sky_scenes(id,project_id,user_id,name,position,layout_key,transition_key,transition_ms,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (scene_id,project_id,user_id,body.name.strip(),position,body.layout_key,body.transition_key,body.transition_ms,now,now))
            con.execute("UPDATE shared_sky_projects SET updated_at=? WHERE id=?", (now,project_id))
        return self.scene(user_id, scene_id)

    def list_scenes(self, user_id: str, project_id: str) -> list[dict]:
        self._owned("shared_sky_projects", project_id, user_id)
        with self._connect() as con:
            rows = con.execute("SELECT * FROM shared_sky_scenes WHERE project_id=? AND user_id=? ORDER BY position,id", (project_id,user_id)).fetchall()
        out=[]
        for row in rows:
            item=dict(row); item["sources"]=self.list_sources(user_id,item["id"]); out.append(item)
        return out

    def scene(self, user_id: str, scene_id: str) -> dict:
        item=self._owned("shared_sky_scenes", scene_id, user_id); item["sources"]=self.list_sources(user_id,scene_id); return item

    def update_scene(self, user_id: str, scene_id: str, body: SceneUpdate) -> dict:
        current=self._owned("shared_sky_scenes",scene_id,user_id)
        transition=body.transition_key or current["transition_key"]
        if transition not in TRANSITION_CATALOG: raise ValueError("Unknown Shared Sky transition")
        values=(body.name.strip() if body.name is not None else current["name"],body.layout_key or current["layout_key"],transition,
                body.transition_ms if body.transition_ms is not None else current["transition_ms"],body.position if body.position is not None else current["position"])
        with self._connect() as con:
            con.execute("UPDATE shared_sky_scenes SET name=?,layout_key=?,transition_key=?,transition_ms=?,position=?,updated_at=? WHERE id=? AND user_id=?",
                        (*values,_now(),scene_id,user_id))
        return self.scene(user_id,scene_id)

    def delete_scene(self,user_id:str,scene_id:str)->None:
        current=self._owned("shared_sky_scenes",scene_id,user_id)
        with self._connect() as con:
            count=con.execute("SELECT COUNT(*) FROM shared_sky_scenes WHERE project_id=? AND user_id=?",(current["project_id"],user_id)).fetchone()[0]
            if int(count)<=1: raise ValueError("A Shared Sky project must keep at least one scene")
            con.execute("DELETE FROM shared_sky_scenes WHERE id=? AND user_id=?",(scene_id,user_id))

    def create_source(self,user_id:str,scene_id:str,body:SourceCreate)->dict:
        scene=self._owned("shared_sky_scenes",scene_id,user_id)
        if body.source_type not in SOURCE_CATALOG: raise ValueError("Unknown Shared Sky source type")
        source_id=uuid4().hex; now=_now()
        with self._connect() as con:
            con.execute("INSERT INTO shared_sky_sources(id,scene_id,project_id,user_id,source_type,name,config_json,visible,locked,z_index,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                        (source_id,scene_id,scene["project_id"],user_id,body.source_type,body.name.strip(),json.dumps(body.config),int(body.visible),int(body.locked),body.z_index,now,now))
        return self.source(user_id,source_id)

    def list_sources(self,user_id:str,scene_id:str)->list[dict]:
        self._owned("shared_sky_scenes",scene_id,user_id)
        with self._connect() as con:
            rows=con.execute("SELECT * FROM shared_sky_sources WHERE scene_id=? AND user_id=? ORDER BY z_index,id",(scene_id,user_id)).fetchall()
        return [self._source_public(dict(row)) for row in rows]

    def _source_public(self,item:dict)->dict:
        item["visible"]=bool(item["visible"]); item["locked"]=bool(item["locked"]); item["config"]=_json(item.pop("config_json","{}"),{}); return item

    def source(self,user_id:str,source_id:str)->dict: return self._source_public(self._owned("shared_sky_sources",source_id,user_id))

    def update_source(self,user_id:str,source_id:str,body:SourceUpdate)->dict:
        current=self._owned("shared_sky_sources",source_id,user_id)
        config=body.config if body.config is not None else _json(current["config_json"],{})
        with self._connect() as con:
            con.execute("UPDATE shared_sky_sources SET name=?,config_json=?,visible=?,locked=?,z_index=?,updated_at=? WHERE id=? AND user_id=?",
                        (body.name.strip() if body.name is not None else current["name"],json.dumps(config),int(body.visible if body.visible is not None else bool(current["visible"])),
                         int(body.locked if body.locked is not None else bool(current["locked"])),body.z_index if body.z_index is not None else current["z_index"],_now(),source_id,user_id))
        return self.source(user_id,source_id)

    def delete_source(self,user_id:str,source_id:str)->None:
        self._owned("shared_sky_sources",source_id,user_id)
        with self._connect() as con: con.execute("DELETE FROM shared_sky_sources WHERE id=? AND user_id=?",(source_id,user_id))

    def _destination_public(self,item:dict)->dict:
        item["enabled"]=bool(item["enabled"]); item["metadata"]=_json(item.pop("metadata_json","{}"),{}); cipher=item.pop("credential_ciphertext","")
        item["credential_stored"]=bool(cipher); item["platform"]=_platform(item["platform_id"]); return item

    def create_destination(self,user_id:str,body:DestinationCreate)->dict:
        endpoint=_validate_endpoint(body.endpoint)
        cipher=self.vault.encrypt(body.credential) if body.credential.strip() else ""
        platform=_platform(body.platform_id)
        status="configured" if endpoint else ("app_credentials_required" if body.auth_mode=="oauth" else "endpoint_required")
        destination_id=uuid4().hex; now=_now()
        with self._connect() as con:
            con.execute("INSERT INTO shared_sky_destinations(id,user_id,platform_id,label,auth_mode,endpoint,credential_ciphertext,enabled,status,metadata_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                        (destination_id,user_id,platform["id"],body.label.strip(),body.auth_mode,endpoint,cipher,int(body.enabled),status,json.dumps(body.metadata),now,now))
        return self.destination(user_id,destination_id)

    def destinations(self,user_id:str)->list[dict]:
        with self._connect() as con: rows=con.execute("SELECT * FROM shared_sky_destinations WHERE user_id=? ORDER BY updated_at DESC",(user_id,)).fetchall()
        return [self._destination_public(dict(row)) for row in rows]

    def destination(self,user_id:str,destination_id:str)->dict: return self._destination_public(self._owned("shared_sky_destinations",destination_id,user_id))

    def update_destination(self,user_id:str,destination_id:str,body:DestinationUpdate)->dict:
        current=self._owned("shared_sky_destinations",destination_id,user_id)
        endpoint=_validate_endpoint(body.endpoint) if body.endpoint is not None else current["endpoint"]
        cipher=current["credential_ciphertext"]
        if body.credential is not None:
            cipher=self.vault.encrypt(body.credential) if body.credential.strip() else ""
        status="configured" if endpoint else "endpoint_required"
        metadata=body.metadata if body.metadata is not None else _json(current["metadata_json"],{})
        with self._connect() as con:
            con.execute("UPDATE shared_sky_destinations SET label=?,endpoint=?,credential_ciphertext=?,enabled=?,status=?,metadata_json=?,updated_at=? WHERE id=? AND user_id=?",
                        (body.label.strip() if body.label is not None else current["label"],endpoint,cipher,int(body.enabled if body.enabled is not None else bool(current["enabled"])),status,json.dumps(metadata),_now(),destination_id,user_id))
        return self.destination(user_id,destination_id)

    def delete_destination(self,user_id:str,destination_id:str)->None:
        self._owned("shared_sky_destinations",destination_id,user_id)
        with self._connect() as con: con.execute("DELETE FROM shared_sky_destinations WHERE id=? AND user_id=?",(destination_id,user_id))

    def _destination_output_url(self,user_id:str,destination_id:str)->str:
        item=self._owned("shared_sky_destinations",destination_id,user_id)
        endpoint=_validate_endpoint(item["endpoint"])
        if not endpoint: raise SharedSkyRelayError("Destination endpoint is missing")
        credential=self.vault.decrypt(item["credential_ciphertext"]) if item["credential_ciphertext"] else ""
        if credential:
            if any(ch in credential for ch in ("\n","\r","\x00")): raise SharedSkyRelayError("Invalid destination credential")
            return endpoint.rstrip("/")+"/"+credential.lstrip("/")
        return endpoint

    def create_broadcast(self,user_id:str,body:BroadcastCreate)->dict:
        self._owned("shared_sky_projects",body.project_id,user_id)
        valid=[]
        for destination_id in body.destination_ids:
            self._owned("shared_sky_destinations",destination_id,user_id); valid.append(destination_id)
        broadcast_id=uuid4().hex; now=_now()
        with self._connect() as con:
            con.execute("INSERT INTO shared_sky_broadcasts(id,user_id,project_id,title,description,state,destination_ids_json,passthrough,created_at,updated_at) VALUES(?,?,?,?,?,'draft',?,?,?,?)",
                        (broadcast_id,user_id,body.project_id,body.title.strip(),body.description.strip(),json.dumps(valid),int(body.passthrough),now,now))
        self.event(user_id,broadcast_id,"broadcast_created",{"destinations":len(valid)})
        return self.broadcast(user_id,broadcast_id)

    def _broadcast_public(self,item:dict)->dict:
        item["destination_ids"]=_json(item.pop("destination_ids_json","[]"),[]); item["passthrough"]=bool(item["passthrough"]); return item

    def broadcast(self,user_id:str,broadcast_id:str)->dict:
        item=self._broadcast_public(self._owned("shared_sky_broadcasts",broadcast_id,user_id)); item["outputs"]=self.outputs(user_id,broadcast_id); return item

    def broadcasts(self,user_id:str,limit:int=50)->list[dict]:
        with self._connect() as con: rows=con.execute("SELECT * FROM shared_sky_broadcasts WHERE user_id=? ORDER BY created_at DESC LIMIT ?",(user_id,max(1,min(limit,200)))).fetchall()
        return [self._broadcast_public(dict(row)) for row in rows]

    def contribution_url(self,broadcast_id:str)->str:
        base=(os.getenv("SHARED_SKY_INGEST_BASE_URL") or "").strip().rstrip("/")
        return f"{base}/{broadcast_id}" if base else ""

    def preflight(self,user_id:str,broadcast_id:str)->dict:
        broadcast=self.broadcast(user_id,broadcast_id); project=self.project(user_id,broadcast["project_id"])
        reasons=[]; positives=[]
        scenes=project["scenes"]
        if not scenes: reasons.append("Add at least one scene")
        elif not any(source["visible"] for scene in scenes for source in scene["sources"]): reasons.append("Add at least one visible source")
        else: positives.append("Scene graph has a visible source")
        destinations=[]
        for destination_id in broadcast["destination_ids"]:
            try:
                destination=self.destination(user_id,destination_id); destinations.append(destination)
                if not destination["enabled"]: reasons.append(f"{destination['label']} is disabled")
                if not destination["endpoint"]: reasons.append(f"{destination['label']} needs a streaming endpoint")
                if destination["auth_mode"] in {"stream_key","custom_rtmp"} and not destination["credential_stored"]:
                    reasons.append(f"{destination['label']} needs its creator-provided stream credential")
            except KeyError:
                reasons.append("A selected destination no longer exists")
        if not destinations: reasons.append("Select at least one destination")
        elif not any(row["enabled"] for row in destinations): reasons.append("Enable at least one destination")
        else: positives.append("At least one destination is enabled")
        ingest=self.contribution_url(broadcast_id)
        if not ingest: reasons.append("Deployment must configure SHARED_SKY_INGEST_BASE_URL")
        else: positives.append("Contribution ingest URL is configured")
        health=relay.health()
        if not health.enabled: reasons.append("Shared Sky relay is disabled in this deployment")
        if not health.ffmpeg_available: reasons.append("FFmpeg relay binary is unavailable")
        if not self.vault.configured and any(d["credential_stored"] for d in destinations): reasons.append("Shared Sky vault is not configured")
        if health.enabled and health.ffmpeg_available: positives.append("Relay runtime is available")
        return {"ready":not reasons,"reasons":reasons,"positives":positives,"ingest_configured":bool(ingest),"relay":health.__dict__,"destination_count":len(destinations)}

    def start_broadcast(self,user_id:str,broadcast_id:str)->dict:
        broadcast=self.broadcast(user_id,broadcast_id)
        if broadcast["state"]=="live": return broadcast
        check=self.preflight(user_id,broadcast_id)
        if not check["ready"]: raise SharedSkyRelayError("Preflight failed: "+"; ".join(check["reasons"]))
        input_url=self.contribution_url(broadcast_id); started=[]; failures=[]
        for destination_id in broadcast["destination_ids"]:
            destination=self.destination(user_id,destination_id)
            if not destination["enabled"]: continue
            output_id=uuid4().hex
            try:
                output_url=self._destination_output_url(user_id,destination_id)
                pid=relay.start_output(output_id=output_id,destination_id=destination_id,input_url=input_url,output_url=output_url,passthrough=broadcast["passthrough"])
                now=_now()
                with self._connect() as con:
                    con.execute("INSERT INTO shared_sky_broadcast_outputs(id,broadcast_id,destination_id,state,process_id,started_at,updated_at) VALUES(?,?,?,'live',?,?,?)",
                                (output_id,broadcast_id,destination_id,pid,now,now))
                started.append(output_id)
            except (SharedSkyRelayError,SharedSkyVaultError) as exc:
                failures.append({"destination_id":destination_id,"error":str(exc)[:500]})
        if not started: raise SharedSkyRelayError("No Shared Sky destination could be started")
        now=_now()
        with self._connect() as con:
            con.execute("UPDATE shared_sky_broadcasts SET state='live',started_at=?,updated_at=? WHERE id=? AND user_id=?",(now,now,broadcast_id,user_id))
        self.event(user_id,broadcast_id,"broadcast_started",{"outputs":len(started),"failures":len(failures)})
        return {"broadcast":self.broadcast(user_id,broadcast_id),"started_outputs":len(started),"failures":failures,"contribution_url":input_url}

    def outputs(self,user_id:str,broadcast_id:str)->list[dict]:
        self._owned("shared_sky_broadcasts",broadcast_id,user_id)
        with self._connect() as con: rows=con.execute("SELECT * FROM shared_sky_broadcast_outputs WHERE broadcast_id=? ORDER BY updated_at DESC",(broadcast_id,)).fetchall()
        out=[]
        for row in rows:
            item=dict(row); runtime=relay.output_state(item["id"])
            if item["state"]=="live" and not runtime["running"]:
                item["state"]="ended" if runtime["returncode"]==0 else "failed"
            item["runtime"]=runtime; out.append(item)
        return out

    def stop_broadcast(self,user_id:str,broadcast_id:str,reason:str="creator_stop")->dict:
        broadcast=self.broadcast(user_id,broadcast_id); output_ids=[row["id"] for row in broadcast["outputs"] if row["state"] in {"live","starting","pending"}]
        relay.stop_many(output_ids); now=_now()
        with self._connect() as con:
            if output_ids:
                marks=",".join("?" for _ in output_ids)
                con.execute(f"UPDATE shared_sky_broadcast_outputs SET state='ended',ended_at=?,updated_at=? WHERE id IN ({marks})",(now,now,*output_ids))
            con.execute("UPDATE shared_sky_broadcasts SET state='ended',ended_at=?,updated_at=? WHERE id=? AND user_id=?",(now,now,broadcast_id,user_id))
        self.event(user_id,broadcast_id,"broadcast_stopped",{"reason":reason,"outputs":len(output_ids)})
        return self.broadcast(user_id,broadcast_id)

    def event(self,user_id:str,broadcast_id:str|None,event_type:str,payload:dict|None=None)->None:
        with self._connect() as con:
            con.execute("INSERT INTO shared_sky_events(user_id,broadcast_id,event_type,payload_json,created_at) VALUES(?,?,?,?,?)",
                        (user_id,broadcast_id,event_type[:80],json.dumps(payload or {}),_now()))

    def events(self,user_id:str,broadcast_id:str,limit:int=100)->list[dict]:
        self._owned("shared_sky_broadcasts",broadcast_id,user_id)
        with self._connect() as con: rows=con.execute("SELECT * FROM shared_sky_events WHERE user_id=? AND broadcast_id=? ORDER BY id DESC LIMIT ?",(user_id,broadcast_id,max(1,min(limit,500)))).fetchall()
        out=[]
        for row in rows: item=dict(row); item["payload"]=_json(item.pop("payload_json","{}"),{}); out.append(item)
        return out

    def create_schedule(self,user_id:str,body:ScheduleCreate)->dict:
        self._owned("shared_sky_projects",body.project_id,user_id)
        for destination_id in body.destination_ids: self._owned("shared_sky_destinations",destination_id,user_id)
        schedule_id=uuid4().hex; now=_now()
        with self._connect() as con:
            con.execute("INSERT INTO shared_sky_schedules(id,user_id,project_id,title,start_at,destination_ids_json,mode,state,created_at,updated_at) VALUES(?,?,?,?,?,?,?,'scheduled',?,?)",
                        (schedule_id,user_id,body.project_id,body.title.strip(),body.start_at.strip(),json.dumps(body.destination_ids),body.mode,now,now))
        return self.schedule(user_id,schedule_id)

    def schedule(self,user_id:str,schedule_id:str)->dict:
        item=self._owned("shared_sky_schedules",schedule_id,user_id); item["destination_ids"]=_json(item.pop("destination_ids_json","[]"),[]); return item

    def schedules(self,user_id:str)->list[dict]:
        with self._connect() as con: rows=con.execute("SELECT * FROM shared_sky_schedules WHERE user_id=? ORDER BY start_at",(user_id,)).fetchall()
        out=[]
        for row in rows: item=dict(row); item["destination_ids"]=_json(item.pop("destination_ids_json","[]"),[]); out.append(item)
        return out

    def delete_schedule(self,user_id:str,schedule_id:str)->None:
        self._owned("shared_sky_schedules",schedule_id,user_id)
        with self._connect() as con: con.execute("DELETE FROM shared_sky_schedules WHERE id=? AND user_id=?",(schedule_id,user_id))

    def owner_status(self)->dict:
        with self._connect() as con:
            counts={
                "projects":con.execute("SELECT COUNT(*) FROM shared_sky_projects").fetchone()[0],
                "destinations":con.execute("SELECT COUNT(*) FROM shared_sky_destinations").fetchone()[0],
                "live":con.execute("SELECT COUNT(*) FROM shared_sky_broadcasts WHERE state='live'").fetchone()[0],
                "broadcasts":con.execute("SELECT COUNT(*) FROM shared_sky_broadcasts").fetchone()[0],
                "schedules":con.execute("SELECT COUNT(*) FROM shared_sky_schedules WHERE state='scheduled'").fetchone()[0],
            }
            live=[dict(row) for row in con.execute("SELECT id,user_id,project_id,title,state,started_at,updated_at FROM shared_sky_broadcasts WHERE state='live' ORDER BY started_at DESC LIMIT 100").fetchall()]
        return {"counts":counts,"relay":relay.health().__dict__,"vault":self.vault.health().__dict__,"live_broadcasts":live,"platforms":PLATFORM_REGISTRY}

    def owner_emergency_stop(self)->dict:
        with self._connect() as con:
            rows=con.execute("SELECT id,user_id FROM shared_sky_broadcasts WHERE state='live'").fetchall()
        stopped=[]
        for row in rows:
            try: self.stop_broadcast(str(row["user_id"]),str(row["id"]),reason="owner_emergency_stop"); stopped.append(str(row["id"]))
            except Exception: continue
        return {"stopped":len(stopped),"broadcast_ids":stopped}


shared_sky = SharedSkyStore()


def _member(request:Request): return require_esp_hub_member(request)

def _owner(request:Request)->None:
    if not owner_session_authorized(request): raise HTTPException(401,"Owner authentication required")


def _catalog()->dict:
    return {
        "product":PRODUCT_NAME,"endorsement":PRODUCT_ENDORSEMENT,"menu":MENU,"workspace_modes":WORKSPACE_MODES,"canvas_modes":CANVAS_MODES,
        "platforms":PLATFORM_REGISTRY,"sources":SOURCE_CATALOG,"effects":EFFECT_CATALOG,"transitions":TRANSITION_CATALOG,"audio":AUDIO_CATALOG,"layouts":LAYOUT_CATALOG,
        "integrations":{
            "broadcast_tech":"/command-center/broadcast-tech","live_overlay":"/live-overlay-studio","creator_network":"/command-center/level-up",
            "owner":"/owner/shared-sky","creation_studios":"/creative-studio",
        },
        "runtime":{
            "relay":relay.health().__dict__,"vault":shared_sky.vault.health().__dict__,
            "scheduler_execution":"worker_adapter_required","oauth_connectors":"platform_app_credentials_and_review_required",
            "browser_contribution":"ingest_service_or_native_companion_required",
        },
    }


@router.get("/shared-sky/api/catalog")
def catalog(request:Request): _member(request); return _catalog()

@router.get("/shared-sky/api/state")
def state(request:Request):
    member,membership=_member(request)
    return {"role":"owner" if membership.get("status")=="owner" else membership.get("roles"),"projects":shared_sky.list_projects(member.user_id),"destinations":shared_sky.destinations(member.user_id),"broadcasts":shared_sky.broadcasts(member.user_id),"schedules":shared_sky.schedules(member.user_id),"catalog":_catalog()}

@router.post("/shared-sky/api/projects")
def create_project(body:ProjectCreate,request:Request): member,_=_member(request); return {"project":shared_sky.create_project(member.user_id,body)}

@router.get("/shared-sky/api/projects/{project_id}")
def get_project(project_id:str,request:Request):
    member,_=_member(request)
    try:return {"project":shared_sky.project(member.user_id,project_id)}
    except KeyError as exc: raise HTTPException(404,"Shared Sky project not found") from exc

@router.put("/shared-sky/api/projects/{project_id}")
def update_project(project_id:str,body:ProjectUpdate,request:Request):
    member,_=_member(request)
    try:return {"project":shared_sky.update_project(member.user_id,project_id,body)}
    except KeyError as exc: raise HTTPException(404,"Shared Sky project not found") from exc

@router.delete("/shared-sky/api/projects/{project_id}")
def delete_project(project_id:str,request:Request):
    member,_=_member(request)
    try: shared_sky.delete_project(member.user_id,project_id); return {"deleted":True}
    except KeyError as exc: raise HTTPException(404,"Shared Sky project not found") from exc

@router.post("/shared-sky/api/projects/{project_id}/scenes")
def create_scene(project_id:str,body:SceneCreate,request:Request):
    member,_=_member(request)
    try:return {"scene":shared_sky.create_scene(member.user_id,project_id,body)}
    except KeyError as exc: raise HTTPException(404,"Shared Sky project not found") from exc
    except ValueError as exc: raise HTTPException(400,str(exc)) from exc

@router.put("/shared-sky/api/scenes/{scene_id}")
def update_scene(scene_id:str,body:SceneUpdate,request:Request):
    member,_=_member(request)
    try:return {"scene":shared_sky.update_scene(member.user_id,scene_id,body)}
    except KeyError as exc: raise HTTPException(404,"Shared Sky scene not found") from exc
    except ValueError as exc: raise HTTPException(400,str(exc)) from exc

@router.delete("/shared-sky/api/scenes/{scene_id}")
def delete_scene(scene_id:str,request:Request):
    member,_=_member(request)
    try:shared_sky.delete_scene(member.user_id,scene_id);return {"deleted":True}
    except KeyError as exc:raise HTTPException(404,"Shared Sky scene not found") from exc
    except ValueError as exc:raise HTTPException(400,str(exc)) from exc

@router.post("/shared-sky/api/scenes/{scene_id}/sources")
def create_source(scene_id:str,body:SourceCreate,request:Request):
    member,_=_member(request)
    try:return {"source":shared_sky.create_source(member.user_id,scene_id,body)}
    except KeyError as exc:raise HTTPException(404,"Shared Sky scene not found") from exc
    except ValueError as exc:raise HTTPException(400,str(exc)) from exc

@router.put("/shared-sky/api/sources/{source_id}")
def update_source(source_id:str,body:SourceUpdate,request:Request):
    member,_=_member(request)
    try:return {"source":shared_sky.update_source(member.user_id,source_id,body)}
    except KeyError as exc:raise HTTPException(404,"Shared Sky source not found") from exc

@router.delete("/shared-sky/api/sources/{source_id}")
def delete_source(source_id:str,request:Request):
    member,_=_member(request)
    try:shared_sky.delete_source(member.user_id,source_id);return {"deleted":True}
    except KeyError as exc:raise HTTPException(404,"Shared Sky source not found") from exc

@router.post("/shared-sky/api/destinations")
def create_destination(body:DestinationCreate,request:Request):
    member,_=_member(request)
    try:return {"destination":shared_sky.create_destination(member.user_id,body)}
    except (ValueError,SharedSkyVaultError) as exc:raise HTTPException(400,str(exc)) from exc

@router.put("/shared-sky/api/destinations/{destination_id}")
def update_destination(destination_id:str,body:DestinationUpdate,request:Request):
    member,_=_member(request)
    try:return {"destination":shared_sky.update_destination(member.user_id,destination_id,body)}
    except KeyError as exc:raise HTTPException(404,"Shared Sky destination not found") from exc
    except (ValueError,SharedSkyVaultError) as exc:raise HTTPException(400,str(exc)) from exc

@router.delete("/shared-sky/api/destinations/{destination_id}")
def delete_destination(destination_id:str,request:Request):
    member,_=_member(request)
    try:shared_sky.delete_destination(member.user_id,destination_id);return {"deleted":True}
    except KeyError as exc:raise HTTPException(404,"Shared Sky destination not found") from exc

@router.post("/shared-sky/api/broadcasts")
def create_broadcast(body:BroadcastCreate,request:Request):
    member,_=_member(request)
    try:return {"broadcast":shared_sky.create_broadcast(member.user_id,body)}
    except KeyError as exc:raise HTTPException(404,"Shared Sky project or destination not found") from exc

@router.get("/shared-sky/api/broadcasts/{broadcast_id}/preflight")
def preflight(broadcast_id:str,request:Request):
    member,_=_member(request)
    try:return shared_sky.preflight(member.user_id,broadcast_id)
    except KeyError as exc:raise HTTPException(404,"Shared Sky broadcast not found") from exc

@router.post("/shared-sky/api/broadcasts/{broadcast_id}/start")
def start_broadcast(broadcast_id:str,request:Request):
    member,_=_member(request)
    try:return shared_sky.start_broadcast(member.user_id,broadcast_id)
    except KeyError as exc:raise HTTPException(404,"Shared Sky broadcast not found") from exc
    except (SharedSkyRelayError,SharedSkyVaultError) as exc:raise HTTPException(503,str(exc)) from exc

@router.post("/shared-sky/api/broadcasts/{broadcast_id}/stop")
def stop_broadcast(broadcast_id:str,request:Request):
    member,_=_member(request)
    try:return {"broadcast":shared_sky.stop_broadcast(member.user_id,broadcast_id)}
    except KeyError as exc:raise HTTPException(404,"Shared Sky broadcast not found") from exc

@router.get("/shared-sky/api/broadcasts/{broadcast_id}/health")
def broadcast_health(broadcast_id:str,request:Request):
    member,_=_member(request)
    try:return {"broadcast":shared_sky.broadcast(member.user_id,broadcast_id),"preflight":shared_sky.preflight(member.user_id,broadcast_id),"events":shared_sky.events(member.user_id,broadcast_id,50)}
    except KeyError as exc:raise HTTPException(404,"Shared Sky broadcast not found") from exc

@router.post("/shared-sky/api/schedules")
def create_schedule(body:ScheduleCreate,request:Request):
    member,_=_member(request)
    try:return {"schedule":shared_sky.create_schedule(member.user_id,body)}
    except KeyError as exc:raise HTTPException(404,"Shared Sky project or destination not found") from exc

@router.delete("/shared-sky/api/schedules/{schedule_id}")
def delete_schedule(schedule_id:str,request:Request):
    member,_=_member(request)
    try:shared_sky.delete_schedule(member.user_id,schedule_id);return {"deleted":True}
    except KeyError as exc:raise HTTPException(404,"Shared Sky schedule not found") from exc

@router.get("/owner/shared-sky/api/status")
def owner_shared_sky_status(request:Request): _owner(request); return shared_sky.owner_status()

@router.post("/owner/shared-sky/api/emergency-stop")
def owner_shared_sky_emergency_stop(request:Request): _owner(request); return shared_sky.owner_emergency_stop()


CSS="""
:root{--bg:#050913;--panel:#0c1527;--panel2:#101e35;--line:#ffffff20;--sky:#7bd8ff;--sun:#ffd86e;--violet:#b88cff;--text:#f8fbff;--muted:#aebbd0;--good:#70e3a7;--bad:#ff879f}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 8% 0,#143e69,transparent 34%),radial-gradient(circle at 92% 0,#48285c,transparent 30%),var(--bg);color:var(--text);font-family:Inter,ui-sans-serif,system-ui,sans-serif;min-height:100vh}.shell{display:grid;grid-template-columns:245px 1fr;min-height:100vh}.side{border-right:1px solid var(--line);background:#060b16e8;padding:18px;position:sticky;top:0;height:100vh;overflow:auto}.logo{font-weight:950;font-size:1.18rem;letter-spacing:-.03em}.sky{color:var(--sky)}.endorse{color:var(--muted);font-size:.75rem;margin:5px 0 18px}.nav button{display:block;width:100%;text-align:left;margin:3px 0;background:transparent;color:var(--muted);border:0;border-radius:10px;padding:9px 10px;cursor:pointer}.nav button.active,.nav button:hover{background:#ffffff0c;color:#fff}.main{padding:18px 24px 60px;min-width:0}.top{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}.actions{display:flex;gap:8px;flex-wrap:wrap}.btn,button,select,input,textarea{font:inherit}.btn,button{border:1px solid var(--line);background:#ffffff0b;color:#fff;padding:9px 12px;border-radius:11px;font-weight:800;cursor:pointer}.primary{background:linear-gradient(115deg,var(--sky),var(--violet));color:#07101b;border:0}.danger{border-color:#ff879f66;color:#ffdbe2}.card{background:linear-gradient(145deg,#0f1a2bee,#09111fee);border:1px solid var(--line);border-radius:18px;padding:16px;margin:12px 0;box-shadow:0 16px 55px #0005}.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}.grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px}.metric{border:1px solid var(--line);border-radius:14px;padding:12px;background:#ffffff05}.metric b{display:block;font-size:1.45rem;margin-top:3px}.muted{color:var(--muted);line-height:1.5}.status{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--line);border-radius:99px;padding:5px 9px;font-size:.75rem}.dot{width:8px;height:8px;border-radius:50%;background:var(--good)}.dot.bad{background:var(--bad)}.stage{aspect-ratio:16/9;background:linear-gradient(145deg,#070a11,#111c31);border:1px solid #7bd8ff44;border-radius:18px;position:relative;overflow:hidden;display:grid;place-items:center}.stage video{width:100%;height:100%;object-fit:cover}.stage .empty{text-align:center;color:var(--muted)}.scene{padding:10px;border:1px solid var(--line);border-radius:12px;margin:7px 0;background:#ffffff05;cursor:pointer}.scene.active{border-color:var(--sky)}.panel{display:none}.panel.active{display:block}input,select,textarea{width:100%;border:1px solid var(--line);border-radius:10px;padding:10px;background:#050a13;color:#fff;margin:4px 0 10px}label{font-size:.82rem;font-weight:800}.chip{display:inline-block;border:1px solid var(--line);border-radius:99px;padding:5px 8px;margin:3px;font-size:.75rem;color:var(--muted)}table{width:100%;border-collapse:collapse}th,td{border-bottom:1px solid var(--line);padding:9px;text-align:left;vertical-align:top}.scroll{overflow:auto}.notice{border-left:3px solid var(--sun);padding:10px 12px;background:#ffd86e0b;border-radius:8px}.canvas-tools{display:grid;grid-template-columns:240px 1fr 260px;gap:12px}.source-item{border:1px solid var(--line);padding:8px;border-radius:10px;margin:6px 0}.hide{display:none}@media(max-width:1100px){.canvas-tools{grid-template-columns:210px 1fr}.inspector{grid-column:1/-1}.grid{grid-template-columns:1fr 1fr}}@media(max-width:760px){.shell{grid-template-columns:1fr}.side{position:relative;height:auto;border-right:0;border-bottom:1px solid var(--line)}.nav{display:flex;overflow:auto}.nav button{min-width:max-content}.main{padding:14px}.grid,.grid2,.canvas-tools{grid-template-columns:1fr}}
"""


def _studio_html(role:str)->str:
    nav="".join(f"<button data-panel='{i}' class='{'active' if i==0 else ''}'>{escape(name)}</button>" for i,name in enumerate(MENU))
    return f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>{PRODUCT_NAME}</title><style>{CSS}</style></head><body><div class='shell'><aside class='side'><div class='logo'><span class='sky'>Shared Sky</span><br>Streaming Studios</div><div class='endorse'>by Elevate Souls Productions · {escape(role.title())} access</div><div class='nav'>{nav}</div></aside><main class='main'><header class='top'><div><div class='sky' style='font-weight:900'>PROFESSIONAL MULTI-PLATFORM LIVE PRODUCTION</div><h1 style='margin:.2em 0'>Shared Sky Control Room</h1></div><div class='actions'><a class='btn' href='/command-center/broadcast-tech'>Preflight Tech Desk</a><a class='btn' href='/live-overlay-studio'>Overlay Studio</a><button class='primary' id='refresh'>Refresh</button></div></header>
<section class='panel active' data-panel='0'><div class='grid'><div class='metric'><small>Projects</small><b id='mProjects'>0</b></div><div class='metric'><small>Destinations</small><b id='mDestinations'>0</b></div><div class='metric'><small>Broadcasts</small><b id='mBroadcasts'>0</b></div><div class='metric'><small>Schedules</small><b id='mSchedules'>0</b></div></div><div class='grid2'><div class='card'><h2>Start a production</h2><p class='muted'>Build scenes once, create landscape and portrait layouts, then send one contribution feed to Shared Sky for independent cloud relay to every authorised destination.</p><button class='primary' onclick='showPanel(2)'>Open Studio Canvas</button></div><div class='card'><h2>Runtime health</h2><div id='runtimeHealth' class='muted'>Loading…</div><p class='muted'>Platform OAuth still requires each provider's app approval and creator authorisation. Shared Sky does not bypass platform eligibility.</p></div></div><div class='card'><h2>Your Shared Sky projects</h2><div id='projectList'></div><button onclick='newProject()'>+ New project</button></div></section>
<section class='panel' data-panel='1'><div class='card'><h2>Go Live</h2><p class='muted'>Choose a project and destinations. Shared Sky runs a fail-closed preflight before any relay starts.</p><label>Project</label><select id='liveProject'></select><label>Destinations</label><div id='liveDestinations'></div><label>Broadcast title</label><input id='liveTitle' value='Shared Sky LIVE'><button onclick='prepareBroadcast()'>Create & Preflight</button><div id='liveResult' class='card muted'>No broadcast prepared.</div></div></section>
<section class='panel' data-panel='2'><div class='canvas-tools'><div class='card'><h3>Scenes</h3><select id='canvasProject' onchange='selectProject(this.value)'></select><div id='sceneList'></div><button onclick='addScene()'>+ Scene</button></div><div><div class='stage' id='stage'><div class='empty'><b>Programme Preview</b><br>Choose a scene or start a local camera preview.</div></div><div class='actions' style='margin-top:10px'><button onclick='previewCamera()'>Camera Preview</button><button onclick='previewScreen()'>Screen Preview</button><button onclick='stopPreview()'>Stop Preview</button><button class='primary'>TAKE → PROGRAMME</button></div><div class='card'><h3>Sources</h3><div id='sourceList'></div><div class='actions'><select id='sourceType' style='width:auto'></select><button onclick='addSource()'>+ Add Source</button></div></div></div><div class='card inspector'><h3>Inspector</h3><div id='inspector' class='muted'>Select a scene/source. Transform, crop, effects, audio routing and layout metadata are stored in the same scene graph.</div></div></div></section>
<section class='panel' data-panel='3'><div class='card'><h2>Scenes</h2><div id='scenesPanel'></div></div></section>
<section class='panel' data-panel='4'><div class='card'><h2>Source Library</h2><div id='sourceCatalog'></div></div></section>
<section class='panel' data-panel='5'><div class='card'><h2>Layouts</h2><div id='layoutCatalog'></div></div></section>
<section class='panel' data-panel='6'><div class='card'><h2>Transitions</h2><div id='transitionCatalog'></div></div></section>
<section class='panel' data-panel='7'><div class='card'><h2>Visual Effects</h2><div id='effectCatalog'></div><p class='muted'>Effect stacks are stored as source configuration now; GPU/browser/native rendering adapters can execute only the effects a scene actually uses.</p></div></section>
<section class='panel' data-panel='8'><div class='card'><h2>Professional Audio</h2><div id='audioCatalog'></div><p class='muted'>Designed for per-source gain/pan, buses, monitoring, filters, loudness metering, soundboard and multi-track recording.</p></div></section>
<section class='panel' data-panel='9'><div class='card'><h2>Brand Kit</h2><p class='muted'>Show packages, logos, lower thirds, fonts, colours, intros/outros, sponsor cards, QR codes and portrait/landscape safe areas connect to the wider Creation Studios and Asset Vault.</p></div></section>
<section class='panel' data-panel='10'><div class='card'><h2>Overlays & Widgets</h2><p class='muted'>Shared Sky reuses the existing secure Aura LIVE Overlay Studio for browser-source alerts, goals, leaderboards, TTS, captions and interactive widgets.</p><a class='btn primary' href='/live-overlay-studio'>Open Overlay Studio</a></div></section>
<section class='panel' data-panel='11'><div class='card'><h2>Guests & Green Room</h2><p class='notice'>Guest/WebRTC signalling, isolated local recordings and backstage admission are represented in the blueprint; production media transport requires the dedicated WebRTC/SFU service before this panel can be called live-ready.</p></div></section>
<section class='panel' data-panel='12'><div class='card'><h2>Unified Chat</h2><p class='notice'>Chat adapters are provider-specific and can only activate after official API scopes are approved. The interface is reserved without pretending unsupported providers are connected.</p></div></section>
<section class='panel' data-panel='13'><div class='card'><h2>Engagement</h2><p class='muted'>Polls, Q&A, highlights, goals, giveaways, moderation queues and Aura-assisted chat summaries plug into destination adapters where APIs permit.</p></div></section>
<section class='panel' data-panel='14'><div class='grid2'><div class='card'><h2>Destinations</h2><div id='destinationList'></div></div><div class='card'><h2>Add destination</h2><label>Platform</label><select id='dPlatform'></select><label>Label</label><input id='dLabel' value='My channel'><label>Auth mode</label><select id='dAuth'><option>custom_rtmp</option><option>stream_key</option><option>custom_srt</option><option>oauth</option><option>platform_authorised</option></select><label>Server endpoint</label><input id='dEndpoint' placeholder='rtmps://server.example/live'><label>Stream credential / key</label><input id='dCredential' type='password' autocomplete='off'><button class='primary' onclick='addDestination()'>Save securely</button><p class='muted'>Credentials are encrypted server-side only when SHARED_SKY_VAULT_SECRET is configured and are never returned by the API.</p></div></div></section>
<section class='panel' data-panel='15'><div class='card'><h2>Schedules</h2><div id='scheduleList'></div><p class='muted'>Schedule persistence is live. Automatic scheduled execution remains fail-closed until the dedicated scheduler/relay worker is deployed.</p></div></section>
<section class='panel' data-panel='16'><div class='card'><h2>Pre-Recorded Live</h2><p class='notice'>Playlist/rundown and pre-recorded scheduling data model is ready; media playout worker/transcoding service is the next infrastructure adapter.</p></div></section>
<section class='panel' data-panel='17'><div class='card'><h2>Recordings</h2><p class='muted'>Programme/clean/ISO recording targets integrate with the existing recording and Video/Cinema systems. Dedicated cloud recording workers are required for full Shared Sky output capture.</p></div></section>
<section class='panel' data-panel='18'><div class='card'><h2>Clips & Repurpose</h2><p class='muted'>Finished streams can flow into the existing Video/Cinema and Social Manager systems for clips, captions, thumbnails and publishing packs.</p></div></section>
<section class='panel' data-panel='19'><div class='card'><h2>Analytics</h2><p class='muted'>Broadcast lifecycle events are recorded now. Viewer, chat, revenue and retention analytics activate per platform as authorised provider APIs are connected.</p></div></section>
<section class='panel' data-panel='20'><div class='card'><h2>Templates</h2><p class='muted'>Scene/project JSON is persistence-ready for personal, ESP-approved and marketplace template layers.</p></div></section>
<section class='panel' data-panel='21'><div class='card'><h2>Media Library</h2><p class='muted'>Connects to the central Asset Vault and Creation Studios rather than creating a duplicate media store.</p></div></section>
<section class='panel' data-panel='22'><div class='card'><h2>Effect Library</h2><div id='effectCatalog2'></div></div></section>
<section class='panel' data-panel='23'><div class='card'><h2>Soundboard</h2><p class='muted'>Soundboard triggering is part of the professional audio catalogue and integrates with existing media assets and hotkey/MIDI/Stream-Deck-style control adapters.</p></div></section>
<section class='panel' data-panel='24'><div class='card'><h2>Plugins & Integrations</h2><div id='platformCatalog'></div><p class='muted'>Creator-made widgets/plugins must be sandboxed, permission-scoped and signed before marketplace distribution.</p></div></section>
<section class='panel' data-panel='25'><div class='card'><h2>Connection Health</h2><div id='connectionHealth'></div></div></section>
<section class='panel' data-panel='26'><div class='card'><h2>Stream Tests</h2><p class='muted'>Use the ESP Broadcast & Tech Desk for network diagnostics, privacy checks, test recording and device preflight.</p><a class='btn primary' href='/command-center/broadcast-tech'>Open Tech Desk</a></div></section>
<section class='panel' data-panel='27'><div class='card'><h2>Settings</h2><p class='muted'>Workspace mode, canvas mode, source/effect performance budget, platform connections and relay preferences are scoped per project/account.</p></div></section>
<section class='panel' data-panel='28'><div class='card'><h2>Help</h2><p class='muted'>Shared Sky is role-gated to approved ESP creators, agents and owners. Platform connections require creator consent and provider-authorised access.</p></div></section>
<script>
let S={{projects:[],destinations:[],broadcasts:[],schedules:[],catalog:{{}}}},activeProject=null,activeScene=null,previewStream=null;
async function api(url,opt={{}}){{const r=await fetch(url,{{credentials:'same-origin',headers:{{'Content-Type':'application/json',...(opt.headers||{{}})}},...opt}});let d={{}};try{{d=await r.json()}}catch(e){{}}if(!r.ok)throw new Error(d.detail||`Request failed ${{r.status}}`);return d}}
function showPanel(i){{document.querySelectorAll('.panel').forEach(x=>x.classList.toggle('active',x.dataset.panel==i));document.querySelectorAll('.nav button').forEach(x=>x.classList.toggle('active',x.dataset.panel==i))}}
document.querySelectorAll('.nav button').forEach(b=>b.onclick=()=>showPanel(b.dataset.panel));
function chips(items){{return (items||[]).map(x=>`<span class='chip'>${{String(x).replaceAll('_',' ')}}</span>`).join('')}}
async function load(){{try{{S=await api('/shared-sky/api/state');render()}}catch(e){{alert(e.message)}}}}
function render(){{mProjects.textContent=S.projects.length;mDestinations.textContent=S.destinations.length;mBroadcasts.textContent=S.broadcasts.length;mSchedules.textContent=S.schedules.length;const r=S.catalog.runtime||{{}};runtimeHealth.innerHTML=`<span class='status'><span class='dot ${{r.relay?.enabled&&r.relay?.ffmpeg_available?'':'bad'}}'></span>Relay ${{r.relay?.enabled?'enabled':'disabled'}}</span> <span class='status'><span class='dot ${{r.vault?.configured?'':'bad'}}'></span>Vault ${{r.vault?.configured?'configured':'not configured'}}</span>`;projectList.innerHTML=S.projects.map(p=>`<div class='scene'><b>${{esc(p.name)}}</b><div class='muted'>${{p.canvas_mode}} · ${{p.workspace_mode}}</div><button onclick="selectProject('${{p.id}}');showPanel(2)">Open</button></div>`).join('')||`<p class='muted'>No Shared Sky project yet.</p>`;[liveProject,canvasProject].forEach(sel=>sel.innerHTML=S.projects.map(p=>`<option value='${{p.id}}'>${{esc(p.name)}}</option>`).join(''));liveDestinations.innerHTML=S.destinations.map(d=>`<label><input type='checkbox' class='liveD' value='${{d.id}}' ${{d.enabled?'checked':''}}> ${{esc(d.label)}} · ${{esc(d.platform.name)}}</label><br>`).join('')||`<span class='muted'>Add a destination first.</span>`;destinationList.innerHTML=S.destinations.map(d=>`<div class='scene'><b>${{esc(d.label)}}</b><div class='muted'>${{esc(d.platform.name)}} · ${{d.status}} · credential ${{d.credential_stored?'stored':'not stored'}}</div><button class='danger' onclick="removeDestination('${{d.id}}')">Remove</button></div>`).join('')||`<p class='muted'>No destinations connected.</p>`;scheduleList.innerHTML=S.schedules.map(x=>`<div class='scene'><b>${{esc(x.title)}}</b><div class='muted'>${{esc(x.start_at)}} · ${{x.mode}} · ${{x.state}}</div></div>`).join('')||`<p class='muted'>No schedules.</p>`;const c=S.catalog;sourceType.innerHTML=(c.sources||[]).map(x=>`<option>${{x}}</option>`).join('');sourceCatalog.innerHTML=chips(c.sources);transitionCatalog.innerHTML=chips(c.transitions);effectCatalog.innerHTML=chips(c.effects);effectCatalog2.innerHTML=chips(c.effects);audioCatalog.innerHTML=chips(c.audio);layoutCatalog.innerHTML=(c.layouts||[]).map(x=>`<span class='chip'>${{esc(x.name)}} · ${{x.slots}} slots</span>`).join('');dPlatform.innerHTML=(c.platforms||[]).map(x=>`<option value='${{x.id}}'>${{esc(x.name)}} · ${{x.implementation}}</option>`).join('');platformCatalog.innerHTML=(c.platforms||[]).map(x=>`<div class='scene'><b>${{esc(x.name)}}</b><div class='muted'>${{x.implementation}}</div>${{chips(x.capabilities)}}</div>`).join('');connectionHealth.innerHTML=S.destinations.map(d=>`<div class='scene'><b>${{esc(d.label)}}</b><span class='status'><span class='dot ${{d.status==='configured'?'':'bad'}}'></span>${{d.status}}</span></div>`).join('')||'<p class="muted">No destinations.</p>';if(!activeProject&&S.projects[0])activeProject=S.projects[0].id;if(activeProject)renderProject()}}
function esc(s){{return String(s??'').replace(/[&<>"']/g,m=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[m]))}}
async function newProject(){{const name=prompt('Shared Sky project name','My LIVE Show');if(!name)return;await api('/shared-sky/api/projects',{{method:'POST',body:JSON.stringify({{name,canvas_mode:'dual',workspace_mode:'professional'}})}});await load()}}
async function selectProject(id){{activeProject=id;const d=await api('/shared-sky/api/projects/'+id);const p=d.project;const i=S.projects.findIndex(x=>x.id===id);if(i>=0)S.projects[i]=p;activeScene=p.scenes?.[0]?.id||null;renderProject()}}
function renderProject(){{const p=S.projects.find(x=>x.id===activeProject);if(!p)return;canvasProject.value=p.id;sceneList.innerHTML=(p.scenes||[]).map(s=>`<div class='scene ${{s.id===activeScene?'active':''}}' onclick="activeScene='${{s.id}}';renderProject()"><b>${{esc(s.name)}}</b><div class='muted'>${{s.layout_key}} · ${{s.transition_key}}</div></div>`).join('');const sc=(p.scenes||[]).find(x=>x.id===activeScene)||p.scenes?.[0];if(sc)activeScene=sc.id;sourceList.innerHTML=sc?(sc.sources||[]).map(x=>`<div class='source-item'><b>${{esc(x.name)}}</b><div class='muted'>${{x.source_type}} · z ${{x.z_index}} · ${{x.visible?'visible':'hidden'}}</div><button class='danger' onclick="removeSource('${{x.id}}')">Remove</button></div>`).join(''):'<p class="muted">No scene selected.</p>';scenesPanel.innerHTML=(p.scenes||[]).map(s=>`<div class='scene'><b>${{esc(s.name)}}</b>${{chips([s.layout_key,s.transition_key,s.transition_ms+'ms'])}}</div>`).join('')}}
async function addScene(){{if(!activeProject)return alert('Create a project first');const name=prompt('Scene name','New Scene');if(!name)return;await api(`/shared-sky/api/projects/${{activeProject}}/scenes`,{{method:'POST',body:JSON.stringify({{name}})}});await selectProject(activeProject)}}
async function addSource(){{if(!activeScene)return alert('Choose a scene');const t=sourceType.value;const name=prompt('Source name',t.replaceAll('_',' '));if(!name)return;await api(`/shared-sky/api/scenes/${{activeScene}}/sources`,{{method:'POST',body:JSON.stringify({{source_type:t,name,config:{{effects:[]}}}})}});await selectProject(activeProject)}}
async function removeSource(id){{if(!confirm('Remove this source?'))return;await api('/shared-sky/api/sources/'+id,{{method:'DELETE'}});await selectProject(activeProject)}}
async function addDestination(){{try{{await api('/shared-sky/api/destinations',{{method:'POST',body:JSON.stringify({{platform_id:dPlatform.value,label:dLabel.value,auth_mode:dAuth.value,endpoint:dEndpoint.value,credential:dCredential.value}})}});dCredential.value='';await load();showPanel(14)}}catch(e){{alert(e.message)}}}}
async function removeDestination(id){{if(!confirm('Remove this destination?'))return;await api('/shared-sky/api/destinations/'+id,{{method:'DELETE'}});await load();showPanel(14)}}
async function prepareBroadcast(){{try{{const ids=[...document.querySelectorAll('.liveD:checked')].map(x=>x.value);const d=await api('/shared-sky/api/broadcasts',{{method:'POST',body:JSON.stringify({{project_id:liveProject.value,title:liveTitle.value,destination_ids:ids,passthrough:true}})}});const id=d.broadcast.id;const p=await api(`/shared-sky/api/broadcasts/${{id}}/preflight`);liveResult.innerHTML=`<b>${{p.ready?'Ready':'Not ready'}}</b><br>${{p.reasons.map(esc).join('<br>')}}<br>${{p.ready?`<button class='primary' onclick="goLive('${{id}}')">GO LIVE</button>`:''}}`;await load()}}catch(e){{liveResult.textContent=e.message}}}}
async function goLive(id){{if(!confirm('Start this Shared Sky broadcast to every selected destination?'))return;try{{const d=await api(`/shared-sky/api/broadcasts/${{id}}/start`,{{method:'POST'}});liveResult.innerHTML=`<b>LIVE</b><br>${{d.started_outputs}} destination output(s) started.<br><button class='danger' onclick="stopLive('${{id}}')">End broadcast</button>`;await load()}}catch(e){{liveResult.textContent=e.message}}}}
async function stopLive(id){{await api(`/shared-sky/api/broadcasts/${{id}}/stop`,{{method:'POST'}});await load();liveResult.textContent='Broadcast ended.'}}
async function previewCamera(){{stopPreview();try{{previewStream=await navigator.mediaDevices.getUserMedia({{video:true,audio:true}});stage.innerHTML='<video autoplay muted playsinline></video>';stage.querySelector('video').srcObject=previewStream}}catch(e){{alert('Camera/microphone preview unavailable: '+e.message)}}}}
async function previewScreen(){{stopPreview();try{{previewStream=await navigator.mediaDevices.getDisplayMedia({{video:true,audio:true}});stage.innerHTML='<video autoplay muted playsinline></video>';stage.querySelector('video').srcObject=previewStream}}catch(e){{alert('Screen preview unavailable: '+e.message)}}}}
function stopPreview(){{if(previewStream)previewStream.getTracks().forEach(t=>t.stop());previewStream=null;stage.innerHTML='<div class="empty"><b>Programme Preview</b><br>Local preview stopped.</div>'}}
refresh.onclick=load;load();
</script></main></div></body></html>"""


@router.get("/shared-sky",response_class=HTMLResponse,include_in_schema=False)
def shared_sky_page(request:Request):
    _member_obj,membership=_member(request)
    role="owner" if membership.get("status")=="owner" else (membership.get("roles") or "ESP member")
    return HTMLResponse(_studio_html(role),headers={"Cache-Control":"no-store"})


def _owner_html(status:dict)->str:
    counts=status["counts"]; relay_health=status["relay"]; vault=status["vault"]
    live="".join(f"<tr><td>{escape(str(row['title']))}</td><td>{escape(str(row['user_id'])[:12])}</td><td>{escape(str(row.get('started_at') or ''))}</td><td>{escape(row['id'])}</td></tr>" for row in status["live_broadcasts"]) or "<tr><td colspan='4'>No active Shared Sky broadcasts.</td></tr>"
    return f"""<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'><title>Shared Sky Owner Control</title><style>{CSS}</style></head><body><main class='main' style='max-width:1400px;margin:auto'><div class='top'><div><div class='sky' style='font-weight:900'>MARY / KEV OWNER CONTROL</div><h1>Shared Sky Streaming Studios</h1></div><div class='actions'><a class='btn' href='/owner/dashboard'>Owner Command Center</a><a class='btn primary' href='/shared-sky'>Open Studio as Creator</a></div></div><div class='grid'><div class='metric'><small>Projects</small><b>{counts['projects']}</b></div><div class='metric'><small>Destinations</small><b>{counts['destinations']}</b></div><div class='metric'><small>Active LIVE</small><b>{counts['live']}</b></div><div class='metric'><small>Broadcasts</small><b>{counts['broadcasts']}</b></div></div><div class='grid2'><div class='card'><h2>Infrastructure</h2><p>Relay: <b>{'enabled' if relay_health['enabled'] else 'disabled'}</b></p><p>FFmpeg: <b>{'available' if relay_health['ffmpeg_available'] else 'unavailable'}</b></p><p>Vault: <b>{'configured' if vault['configured'] else 'not configured'}</b></p><p class='muted'>Provider OAuth connections remain disabled until ESP registers the relevant developer apps and each platform grants required permissions.</p></div><div class='card'><h2>Emergency control</h2><p class='muted'>Stops every Shared Sky output owned by this application runtime and records owner emergency-stop events.</p><button class='danger' onclick='emergency()'>STOP ALL SHARED SKY BROADCASTS</button><div id='result'></div></div></div><div class='card'><h2>Active broadcasts</h2><div class='scroll'><table><thead><tr><th>Title</th><th>User</th><th>Started</th><th>Broadcast ID</th></tr></thead><tbody>{live}</tbody></table></div></div><div class='card'><h2>Platform integration registry</h2>{''.join(f"<div class='scene'><b>{escape(row['name'])}</b><div class='muted'>{escape(row['implementation'])}</div></div>" for row in status['platforms'])}</div><script>async function emergency(){{if(!confirm('Emergency stop every Shared Sky broadcast?'))return;const r=await fetch('/owner/shared-sky/api/emergency-stop',{{method:'POST',credentials:'same-origin'}});const d=await r.json();result.textContent=`Stopped ${{d.stopped}} broadcast(s).`;setTimeout(()=>location.reload(),800)}}</script></main></body></html>"""


@router.get("/owner/shared-sky",response_class=HTMLResponse,include_in_schema=False)
def owner_shared_sky_page(request:Request):
    _owner(request); return HTMLResponse(_owner_html(shared_sky.owner_status()),headers={"Cache-Control":"no-store"})


__all__=["router","SharedSkyStore","shared_sky","PLATFORM_REGISTRY","SOURCE_CATALOG","EFFECT_CATALOG","TRANSITION_CATALOG","AUDIO_CATALOG","LAYOUT_CATALOG","MENU"]
