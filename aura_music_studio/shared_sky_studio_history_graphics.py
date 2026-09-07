from __future__ import annotations

import json
import re
import sqlite3
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .esp_niche import require_esp_hub_member
from .shared_sky_control_room import (
    TRANSITIONS,
    StudioConflict,
    StudioInvariantError,
    normalize_effects,
    normalize_transform,
    studio,
    studio_repo,
    utc_now,
    validate_no_secrets,
)
from .shared_sky_streaming_studios import shared_sky

router = APIRouter(tags=["Shared Sky Studio History & Graphics"])

GRAPHIC_KINDS = {
    "lower_third",
    "title",
    "subtitle",
    "social_handle",
    "banner",
    "sponsor_card",
    "custom_text",
}
SOURCE_TYPES = {"camera", "microphone", "screen", "text", "shape", "gradient", "image", "video", "audio"}
LAYOUT_KEYS = {row["key"] for row in studio.layout_catalog()}
HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def _loads(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value) if value else fallback
    except (TypeError, json.JSONDecodeError):
        return fallback


def _json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _bounded_text(value: str, limit: int) -> str:
    return (value or "").strip()[:limit]


def normalize_graphic_style(value: dict[str, Any] | None) -> dict[str, Any]:
    value = value or {}
    text_color = str(value.get("text_color") or "#ffffff")
    background_color = str(value.get("background_color") or "#07131c")
    if not HEX_COLOR.fullmatch(text_color) or not HEX_COLOR.fullmatch(background_color):
        raise StudioInvariantError("Graphic colours must use six-digit hex values")
    align = str(value.get("align") or "left")
    if align not in {"left", "center", "right"}:
        raise StudioInvariantError("Graphic alignment must be left, center or right")
    return {
        "font_size": max(8, min(200, int(value.get("font_size", 42)))),
        "font_weight": max(100, min(900, int(value.get("font_weight", 700)))),
        "align": align,
        "text_color": text_color.lower(),
        "background_color": background_color.lower(),
        "background_opacity": max(0.0, min(1.0, float(value.get("background_opacity", 0.78)))),
        "padding": max(0, min(80, int(value.get("padding", 18)))),
        "corner_radius": max(0, min(80, int(value.get("corner_radius", 14)))),
    }


class UndoRedoRequest(BaseModel):
    expected_version: int = Field(ge=1)


class BatchTransformItem(BaseModel):
    source_id: str = Field(min_length=1, max_length=128)
    transform: dict[str, Any] = Field(default_factory=dict)
    effects: dict[str, Any] | None = None


class BatchTransformRequest(BaseModel):
    items: list[BatchTransformItem] = Field(min_length=1, max_length=100)
    expected_version: int = Field(ge=1)


class TrackedSourceCreate(BaseModel):
    source_type: Literal["camera", "microphone", "screen", "text", "shape", "gradient", "image", "video", "audio"]
    name: str = Field(default="Studio Source", min_length=1, max_length=120)
    visible: bool = True
    config: dict[str, Any] = Field(default_factory=dict)
    expected_version: int = Field(ge=1)


class BatchDeleteRequest(BaseModel):
    source_ids: list[str] = Field(min_length=1, max_length=100)
    expected_version: int = Field(ge=1)


class SceneCreateTracked(BaseModel):
    name: str = Field(default="Scene", min_length=1, max_length=120)
    layout_key: str = Field(default="solo", min_length=1, max_length=80)
    transition_key: str = Field(default="fade", min_length=1, max_length=80)
    transition_ms: int = Field(default=350, ge=0, le=20000)
    notes: str = Field(default="", max_length=2000)
    folder: str = Field(default="", max_length=120)
    expected_version: int = Field(ge=1)


class ScenePatchTracked(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    layout_key: str | None = Field(default=None, min_length=1, max_length=80)
    transition_key: str | None = Field(default=None, min_length=1, max_length=80)
    transition_ms: int | None = Field(default=None, ge=0, le=20000)
    locked: bool | None = None
    notes: str | None = Field(default=None, max_length=2000)
    folder: str | None = Field(default=None, max_length=120)
    expected_version: int = Field(ge=1)


class SceneDeleteTracked(BaseModel):
    expected_version: int = Field(ge=1)
    confirm_programme_reference: bool = False


class SceneDuplicateTracked(BaseModel):
    expected_version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=120)


class SceneReorderTracked(BaseModel):
    scene_ids: list[str] = Field(min_length=1, max_length=200)
    expected_version: int = Field(ge=1)


class GraphicCreateRequest(BaseModel):
    kind: Literal["lower_third", "title", "subtitle", "social_handle", "banner", "sponsor_card", "custom_text"]
    name: str = Field(default="Shared Sky Graphic", min_length=1, max_length=120)
    text: str = Field(default="", max_length=500)
    secondary_text: str = Field(default="", max_length=500)
    style: dict[str, Any] = Field(default_factory=dict)
    transform: dict[str, Any] | None = None
    expected_version: int = Field(ge=1)


class HistoryRepository:
    """Atomic Preview graph history using the canonical Shared Sky SQLite scene/source tables."""

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
                CREATE TABLE IF NOT EXISTS shared_sky_scene_metadata (
                    scene_id TEXT PRIMARY KEY,user_id TEXT NOT NULL,project_id TEXT NOT NULL,
                    locked INTEGER NOT NULL DEFAULT 0,notes TEXT NOT NULL DEFAULT '',folder TEXT NOT NULL DEFAULT '',
                    version INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
                    FOREIGN KEY(scene_id) REFERENCES shared_sky_scenes(id) ON DELETE CASCADE,
                    FOREIGN KEY(project_id) REFERENCES shared_sky_projects(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_shared_sky_scene_meta_project
                    ON shared_sky_scene_metadata(project_id,folder,updated_at DESC);
                CREATE TABLE IF NOT EXISTS shared_sky_studio_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,session_id TEXT NOT NULL,user_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,action_key TEXT NOT NULL,before_json TEXT NOT NULL,after_json TEXT NOT NULL,
                    undone INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES shared_sky_studio_sessions(id) ON DELETE CASCADE,
                    FOREIGN KEY(project_id) REFERENCES shared_sky_projects(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_shared_sky_history_session
                    ON shared_sky_studio_history(session_id,id DESC);
                """
            )

    def _session(
        self,
        con: sqlite3.Connection,
        user_id: str,
        session_id: str,
        expected_version: int | None = None,
        *,
        require_idle: bool = True,
    ) -> dict[str, Any]:
        row = con.execute(
            "SELECT * FROM shared_sky_studio_sessions WHERE id=? AND user_id=?",
            (session_id, user_id),
        ).fetchone()
        if not row:
            raise KeyError(session_id)
        item = dict(row)
        if expected_version is not None and int(item["version"]) != expected_version:
            raise StudioConflict(
                f"Studio version conflict: expected {expected_version}, current {item['version']}"
            )
        if require_idle and item.get("transition_state") != "idle":
            raise StudioConflict("Preview edits are locked while a transition is in progress")
        return item

    def _scene_meta_from_con(
        self, con: sqlite3.Connection, user_id: str, project_id: str
    ) -> dict[str, dict[str, Any]]:
        rows = con.execute(
            "SELECT scene_id,locked,notes,folder,version FROM shared_sky_scene_metadata "
            "WHERE user_id=? AND project_id=?",
            (user_id, project_id),
        ).fetchall()
        return {
            str(row["scene_id"]): {
                "locked": bool(row["locked"]),
                "notes": str(row["notes"] or ""),
                "folder": str(row["folder"] or ""),
                "version": int(row["version"]),
            }
            for row in rows
        }

    def scene_metadata(self, user_id: str, project_id: str) -> dict[str, dict[str, Any]]:
        with self._connect() as con:
            owned = con.execute(
                "SELECT 1 FROM shared_sky_projects WHERE id=? AND user_id=?", (project_id, user_id)
            ).fetchone()
            if not owned:
                raise KeyError(project_id)
            return self._scene_meta_from_con(con, user_id, project_id)

    def _capture(
        self, con: sqlite3.Connection, user_id: str, session_id: str
    ) -> dict[str, Any]:
        session = self._session(con, user_id, session_id, require_idle=False)
        project_id = str(session["project_id"])
        metadata = self._scene_meta_from_con(con, user_id, project_id)
        scene_rows = con.execute(
            "SELECT id,name,position,layout_key,transition_key,transition_ms,created_at,updated_at "
            "FROM shared_sky_scenes WHERE user_id=? AND project_id=? ORDER BY position,id",
            (user_id, project_id),
        ).fetchall()
        scenes: list[dict[str, Any]] = []
        for row in scene_rows:
            scene = dict(row)
            source_rows = con.execute(
                "SELECT id,source_type,name,config_json,visible,locked,z_index,created_at,updated_at "
                "FROM shared_sky_sources WHERE user_id=? AND project_id=? AND scene_id=? "
                "ORDER BY z_index,id",
                (user_id, project_id, row["id"]),
            ).fetchall()
            sources: list[dict[str, Any]] = []
            for source_row in source_rows:
                source = dict(source_row)
                source["config"] = _loads(source.pop("config_json", "{}"), {})
                source["visible"] = bool(source["visible"])
                source["locked"] = bool(source["locked"])
                sources.append(source)
            scene["sources"] = sources
            scene["metadata"] = metadata.get(
                str(row["id"]), {"locked": False, "notes": "", "folder": "", "version": 1}
            )
            scenes.append(scene)
        snapshot = {
            "schema_version": 1,
            "project_id": project_id,
            "preview_scene_id": session.get("preview_scene_id"),
            "scenes": scenes,
        }
        validate_no_secrets(snapshot)
        return snapshot

    def _record(
        self,
        con: sqlite3.Connection,
        session: dict[str, Any],
        action_key: str,
        before: dict[str, Any],
        after: dict[str, Any],
    ) -> None:
        validate_no_secrets(before)
        validate_no_secrets(after)
        now = utc_now()
        con.execute(
            "DELETE FROM shared_sky_studio_history WHERE session_id=? AND user_id=? AND undone=1",
            (session["id"], session["user_id"]),
        )
        con.execute(
            "INSERT INTO shared_sky_studio_history(session_id,user_id,project_id,action_key,before_json,after_json,undone,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,0,?,?)",
            (
                session["id"],
                session["user_id"],
                session["project_id"],
                action_key[:80],
                _json(before),
                _json(after),
                now,
                now,
            ),
        )
        con.execute(
            "DELETE FROM shared_sky_studio_history WHERE session_id=? AND id NOT IN "
            "(SELECT id FROM shared_sky_studio_history WHERE session_id=? ORDER BY id DESC LIMIT 100)",
            (session["id"], session["id"]),
        )

    def _bump(
        self,
        con: sqlite3.Connection,
        session: dict[str, Any],
        reason: str,
        *,
        preview_scene_id: str | None = None,
    ) -> None:
        autosave = {
            "reason": reason[:80],
            "saved_at": utc_now(),
            "history_managed": True,
        }
        target_preview = preview_scene_id if preview_scene_id is not None else session.get("preview_scene_id")
        cursor = con.execute(
            "UPDATE shared_sky_studio_sessions SET preview_scene_id=?,autosave_state_json=?,version=version+1,updated_at=? "
            "WHERE id=? AND user_id=? AND version=?",
            (
                target_preview,
                _json(autosave),
                utc_now(),
                session["id"],
                session["user_id"],
                session["version"],
            ),
        )
        if cursor.rowcount != 1:
            raise StudioConflict("Studio state changed in another tab/operator")

    def state(self, user_id: str, session_id: str) -> dict[str, Any]:
        with self._connect() as con:
            self._session(con, user_id, session_id, require_idle=False)
            active = con.execute(
                "SELECT id,action_key,created_at FROM shared_sky_studio_history "
                "WHERE session_id=? AND user_id=? AND undone=0 ORDER BY id DESC LIMIT 1",
                (session_id, user_id),
            ).fetchone()
            redo = con.execute(
                "SELECT id,action_key,created_at FROM shared_sky_studio_history "
                "WHERE session_id=? AND user_id=? AND undone=1 ORDER BY id ASC LIMIT 1",
                (session_id, user_id),
            ).fetchone()
            counts = con.execute(
                "SELECT SUM(CASE WHEN undone=0 THEN 1 ELSE 0 END) AS active_count,"
                "SUM(CASE WHEN undone=1 THEN 1 ELSE 0 END) AS redo_count "
                "FROM shared_sky_studio_history WHERE session_id=? AND user_id=?",
                (session_id, user_id),
            ).fetchone()
        return {
            "can_undo": bool(active),
            "can_redo": bool(redo),
            "undo_action": str(active["action_key"]) if active else None,
            "redo_action": str(redo["action_key"]) if redo else None,
            "undo_depth": int((counts["active_count"] if counts else 0) or 0),
            "redo_depth": int((counts["redo_count"] if counts else 0) or 0),
            "limit": 100,
        }

    def batch_transform(
        self, user_id: str, session_id: str, body: BatchTransformRequest
    ) -> None:
        seen = {item.source_id for item in body.items}
        if len(seen) != len(body.items):
            raise StudioInvariantError("Each source may appear only once in a transform batch")
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            session = self._session(con, user_id, session_id, body.expected_version)
            before = self._capture(con, user_id, session_id)
            for item in body.items:
                row = con.execute(
                    "SELECT * FROM shared_sky_sources WHERE id=? AND user_id=? AND project_id=?",
                    (item.source_id, user_id, session["project_id"]),
                ).fetchone()
                if not row:
                    raise KeyError(item.source_id)
                if row["scene_id"] != session.get("preview_scene_id"):
                    raise StudioInvariantError("Only sources in the current Preview scene can be edited")
                if bool(row["locked"]):
                    raise StudioInvariantError(f"Source {item.source_id} is locked")
                config = _loads(row["config_json"], {})
                config["transform"] = normalize_transform(item.transform)
                if item.effects is not None:
                    config["effects"] = normalize_effects(item.effects)
                validate_no_secrets(config)
                con.execute(
                    "UPDATE shared_sky_sources SET config_json=?,updated_at=? WHERE id=? AND user_id=?",
                    (_json(config), utc_now(), item.source_id, user_id),
                )
            self._bump(con, session, "batch_transform")
            after = self._capture(con, user_id, session_id)
            self._record(con, session, "source_transform", before, after)

    def create_source(
        self, user_id: str, session_id: str, body: TrackedSourceCreate
    ) -> str:
        if body.source_type not in SOURCE_TYPES:
            raise StudioInvariantError("Unsupported professional studio source type")
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            session = self._session(con, user_id, session_id, body.expected_version)
            scene_id = session.get("preview_scene_id")
            if not scene_id:
                raise StudioInvariantError("Select a Preview scene before adding a source")
            before = self._capture(con, user_id, session_id)
            config = dict(body.config)
            config.setdefault("privacy", "programme_safe")
            config["transform"] = normalize_transform(config.get("transform"))
            if body.source_type in {"camera", "microphone", "screen"}:
                config["browser_capture"] = True
                config["capture_state"] = "detached"
            validate_no_secrets(config)
            z_index = int(
                con.execute(
                    "SELECT COALESCE(MAX(z_index),-1)+1 FROM shared_sky_sources WHERE scene_id=?",
                    (scene_id,),
                ).fetchone()[0]
            )
            source_id = uuid4().hex
            now = utc_now()
            con.execute(
                "INSERT INTO shared_sky_sources(id,scene_id,project_id,user_id,source_type,name,config_json,visible,locked,z_index,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    source_id,
                    scene_id,
                    session["project_id"],
                    user_id,
                    body.source_type,
                    body.name.strip(),
                    _json(config),
                    int(body.visible),
                    0,
                    z_index,
                    now,
                    now,
                ),
            )
            self._bump(con, session, "source_created")
            after = self._capture(con, user_id, session_id)
            self._record(con, session, "source_create", before, after)
            return source_id

    def delete_sources(
        self, user_id: str, session_id: str, body: BatchDeleteRequest
    ) -> None:
        ids = list(dict.fromkeys(body.source_ids))
        if len(ids) != len(body.source_ids):
            raise StudioInvariantError("Duplicate source IDs are not allowed")
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            session = self._session(con, user_id, session_id, body.expected_version)
            before = self._capture(con, user_id, session_id)
            for source_id in ids:
                row = con.execute(
                    "SELECT scene_id,locked FROM shared_sky_sources WHERE id=? AND user_id=? AND project_id=?",
                    (source_id, user_id, session["project_id"]),
                ).fetchone()
                if not row:
                    raise KeyError(source_id)
                if row["scene_id"] != session.get("preview_scene_id"):
                    raise StudioInvariantError("Only sources in the current Preview scene can be removed")
                if bool(row["locked"]):
                    raise StudioInvariantError(f"Source {source_id} is locked")
            marks = ",".join("?" for _ in ids)
            con.execute(
                f"DELETE FROM shared_sky_sources WHERE user_id=? AND id IN ({marks})",
                (user_id, *ids),
            )
            self._bump(con, session, "source_deleted")
            after = self._capture(con, user_id, session_id)
            self._record(con, session, "source_delete", before, after)

    def create_scene(
        self, user_id: str, session_id: str, body: SceneCreateTracked
    ) -> str:
        if body.layout_key not in LAYOUT_KEYS:
            raise StudioInvariantError("Unknown Shared Sky studio layout")
        if body.transition_key not in TRANSITIONS:
            raise StudioInvariantError("Unsupported Shared Sky transition")
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            session = self._session(con, user_id, session_id, body.expected_version)
            before = self._capture(con, user_id, session_id)
            scene_id = uuid4().hex
            now = utc_now()
            position = int(
                con.execute(
                    "SELECT COALESCE(MAX(position),-1)+1 FROM shared_sky_scenes WHERE project_id=? AND user_id=?",
                    (session["project_id"], user_id),
                ).fetchone()[0]
            )
            con.execute(
                "INSERT INTO shared_sky_scenes(id,project_id,user_id,name,position,layout_key,transition_key,transition_ms,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    scene_id,
                    session["project_id"],
                    user_id,
                    body.name.strip(),
                    position,
                    body.layout_key,
                    body.transition_key,
                    body.transition_ms,
                    now,
                    now,
                ),
            )
            con.execute(
                "INSERT INTO shared_sky_scene_metadata(scene_id,user_id,project_id,locked,notes,folder,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (
                    scene_id,
                    user_id,
                    session["project_id"],
                    0,
                    body.notes.strip(),
                    body.folder.strip(),
                    now,
                    now,
                ),
            )
            self._bump(con, session, "scene_created", preview_scene_id=scene_id)
            after = self._capture(con, user_id, session_id)
            self._record(con, session, "scene_create", before, after)
            return scene_id

    def duplicate_scene(
        self, user_id: str, session_id: str, scene_id: str, body: SceneDuplicateTracked
    ) -> str:
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            session = self._session(con, user_id, session_id, body.expected_version)
            source_scene = con.execute(
                "SELECT * FROM shared_sky_scenes WHERE id=? AND user_id=? AND project_id=?",
                (scene_id, user_id, session["project_id"]),
            ).fetchone()
            if not source_scene:
                raise KeyError(scene_id)
            before = self._capture(con, user_id, session_id)
            new_scene_id = uuid4().hex
            now = utc_now()
            position = int(
                con.execute(
                    "SELECT COALESCE(MAX(position),-1)+1 FROM shared_sky_scenes WHERE project_id=? AND user_id=?",
                    (session["project_id"], user_id),
                ).fetchone()[0]
            )
            con.execute(
                "INSERT INTO shared_sky_scenes(id,project_id,user_id,name,position,layout_key,transition_key,transition_ms,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    new_scene_id,
                    session["project_id"],
                    user_id,
                    (body.name or f"{source_scene['name']} Copy")[:120],
                    position,
                    source_scene["layout_key"],
                    source_scene["transition_key"],
                    source_scene["transition_ms"],
                    now,
                    now,
                ),
            )
            meta = con.execute(
                "SELECT notes,folder FROM shared_sky_scene_metadata WHERE scene_id=? AND user_id=?",
                (scene_id, user_id),
            ).fetchone()
            con.execute(
                "INSERT INTO shared_sky_scene_metadata(scene_id,user_id,project_id,locked,notes,folder,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (
                    new_scene_id,
                    user_id,
                    session["project_id"],
                    0,
                    str(meta["notes"] if meta else ""),
                    str(meta["folder"] if meta else ""),
                    now,
                    now,
                ),
            )
            sources = con.execute(
                "SELECT * FROM shared_sky_sources WHERE scene_id=? AND user_id=? ORDER BY z_index,id",
                (scene_id, user_id),
            ).fetchall()
            for source in sources:
                config = _loads(source["config_json"], {})
                validate_no_secrets(config)
                con.execute(
                    "INSERT INTO shared_sky_sources(id,scene_id,project_id,user_id,source_type,name,config_json,visible,locked,z_index,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        uuid4().hex,
                        new_scene_id,
                        session["project_id"],
                        user_id,
                        source["source_type"],
                        source["name"],
                        _json(config),
                        source["visible"],
                        source["locked"],
                        source["z_index"],
                        now,
                        now,
                    ),
                )
            self._bump(con, session, "scene_duplicated", preview_scene_id=new_scene_id)
            after = self._capture(con, user_id, session_id)
            self._record(con, session, "scene_duplicate", before, after)
            return new_scene_id

    def patch_scene(
        self, user_id: str, session_id: str, scene_id: str, body: ScenePatchTracked
    ) -> None:
        if body.layout_key is not None and body.layout_key not in LAYOUT_KEYS:
            raise StudioInvariantError("Unknown Shared Sky studio layout")
        if body.transition_key is not None and body.transition_key not in TRANSITIONS:
            raise StudioInvariantError("Unsupported Shared Sky transition")
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            session = self._session(con, user_id, session_id, body.expected_version)
            scene = con.execute(
                "SELECT * FROM shared_sky_scenes WHERE id=? AND user_id=? AND project_id=?",
                (scene_id, user_id, session["project_id"]),
            ).fetchone()
            if not scene:
                raise KeyError(scene_id)
            meta = con.execute(
                "SELECT * FROM shared_sky_scene_metadata WHERE scene_id=? AND user_id=?",
                (scene_id, user_id),
            ).fetchone()
            currently_locked = bool(meta["locked"]) if meta else False
            content_change = any(
                value is not None
                for value in (
                    body.name,
                    body.layout_key,
                    body.transition_key,
                    body.transition_ms,
                    body.notes,
                    body.folder,
                )
            )
            if currently_locked and content_change:
                raise StudioInvariantError("Unlock this scene before editing it")
            before = self._capture(con, user_id, session_id)
            con.execute(
                "UPDATE shared_sky_scenes SET name=?,layout_key=?,transition_key=?,transition_ms=?,updated_at=? "
                "WHERE id=? AND user_id=?",
                (
                    body.name.strip() if body.name is not None else scene["name"],
                    body.layout_key or scene["layout_key"],
                    body.transition_key or scene["transition_key"],
                    body.transition_ms if body.transition_ms is not None else scene["transition_ms"],
                    utc_now(),
                    scene_id,
                    user_id,
                ),
            )
            locked = int(body.locked if body.locked is not None else currently_locked)
            notes = body.notes.strip() if body.notes is not None else str(meta["notes"] if meta else "")
            folder = body.folder.strip() if body.folder is not None else str(meta["folder"] if meta else "")
            now = utc_now()
            con.execute(
                "INSERT INTO shared_sky_scene_metadata(scene_id,user_id,project_id,locked,notes,folder,version,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,1,?,?) ON CONFLICT(scene_id) DO UPDATE SET locked=excluded.locked,notes=excluded.notes,"
                "folder=excluded.folder,version=shared_sky_scene_metadata.version+1,updated_at=excluded.updated_at",
                (scene_id, user_id, session["project_id"], locked, notes, folder, now, now),
            )
            self._bump(con, session, "scene_updated")
            after = self._capture(con, user_id, session_id)
            self._record(con, session, "scene_update", before, after)

    def delete_scene(
        self, user_id: str, session_id: str, scene_id: str, body: SceneDeleteTracked
    ) -> None:
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            session = self._session(con, user_id, session_id, body.expected_version)
            scene = con.execute(
                "SELECT * FROM shared_sky_scenes WHERE id=? AND user_id=? AND project_id=?",
                (scene_id, user_id, session["project_id"]),
            ).fetchone()
            if not scene:
                raise KeyError(scene_id)
            meta = con.execute(
                "SELECT locked FROM shared_sky_scene_metadata WHERE scene_id=? AND user_id=?",
                (scene_id, user_id),
            ).fetchone()
            if meta and bool(meta["locked"]):
                raise StudioInvariantError("Unlock this scene before deleting it")
            count = int(
                con.execute(
                    "SELECT COUNT(*) FROM shared_sky_scenes WHERE project_id=? AND user_id=?",
                    (session["project_id"], user_id),
                ).fetchone()[0]
            )
            if count <= 1:
                raise StudioInvariantError("A Shared Sky project must keep at least one scene")
            if session.get("programme_scene_id") == scene_id and not body.confirm_programme_reference:
                raise StudioInvariantError(
                    "This scene is the identity of the current Programme snapshot; explicit confirmation is required"
                )
            before = self._capture(con, user_id, session_id)
            alternatives = con.execute(
                "SELECT id FROM shared_sky_scenes WHERE project_id=? AND user_id=? AND id<>? ORDER BY position,id",
                (session["project_id"], user_id, scene_id),
            ).fetchall()
            next_preview = session.get("preview_scene_id")
            if next_preview == scene_id:
                next_preview = str(alternatives[0]["id"])
            con.execute(
                "DELETE FROM shared_sky_scenes WHERE id=? AND user_id=?", (scene_id, user_id)
            )
            self._bump(con, session, "scene_deleted", preview_scene_id=str(next_preview))
            after = self._capture(con, user_id, session_id)
            self._record(con, session, "scene_delete", before, after)

    def reorder_scenes(
        self, user_id: str, session_id: str, body: SceneReorderTracked
    ) -> None:
        if len(set(body.scene_ids)) != len(body.scene_ids):
            raise StudioInvariantError("Scene reorder contains duplicate scene IDs")
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            session = self._session(con, user_id, session_id, body.expected_version)
            rows = con.execute(
                "SELECT id FROM shared_sky_scenes WHERE project_id=? AND user_id=? ORDER BY position,id",
                (session["project_id"], user_id),
            ).fetchall()
            existing = {str(row["id"]) for row in rows}
            if set(body.scene_ids) != existing or len(body.scene_ids) != len(existing):
                raise StudioInvariantError("Scene reorder must contain every project scene exactly once")
            before = self._capture(con, user_id, session_id)
            for position, scene_id in enumerate(body.scene_ids):
                con.execute(
                    "UPDATE shared_sky_scenes SET position=?,updated_at=? WHERE id=? AND user_id=?",
                    (position, utc_now(), scene_id, user_id),
                )
            self._bump(con, session, "scene_reordered")
            after = self._capture(con, user_id, session_id)
            self._record(con, session, "scene_reorder", before, after)

    def create_graphic(
        self, user_id: str, session_id: str, body: GraphicCreateRequest
    ) -> str:
        if body.kind not in GRAPHIC_KINDS:
            raise StudioInvariantError("Unsupported Shared Sky graphic type")
        style = normalize_graphic_style(body.style)
        defaults = {
            "lower_third": {"x": 0.05, "y": 0.72, "width": 0.58, "height": 0.18},
            "title": {"x": 0.10, "y": 0.08, "width": 0.80, "height": 0.20},
            "subtitle": {"x": 0.12, "y": 0.78, "width": 0.76, "height": 0.12},
            "social_handle": {"x": 0.05, "y": 0.84, "width": 0.42, "height": 0.10},
            "banner": {"x": 0.05, "y": 0.05, "width": 0.90, "height": 0.12},
            "sponsor_card": {"x": 0.68, "y": 0.06, "width": 0.27, "height": 0.18},
            "custom_text": {"x": 0.15, "y": 0.35, "width": 0.70, "height": 0.20},
        }
        transform = normalize_transform(body.transform or defaults[body.kind])
        config = {
            "privacy": "programme_safe",
            "text": _bounded_text(body.text, 500),
            "graphic": {
                "schema_version": 1,
                "kind": body.kind,
                "text": _bounded_text(body.text, 500),
                "secondary_text": _bounded_text(body.secondary_text, 500),
                "style": style,
            },
            "transform": transform,
        }
        return self.create_source(
            user_id,
            session_id,
            TrackedSourceCreate(
                source_type="text",
                name=body.name,
                visible=True,
                config=config,
                expected_version=body.expected_version,
            ),
        )

    def _restore(
        self,
        con: sqlite3.Connection,
        user_id: str,
        session: dict[str, Any],
        snapshot: dict[str, Any],
    ) -> str:
        validate_no_secrets(snapshot)
        if snapshot.get("project_id") != session["project_id"]:
            raise StudioInvariantError("History snapshot belongs to a different studio project")
        scenes = snapshot.get("scenes") or []
        if not scenes:
            raise StudioInvariantError("History snapshot cannot restore an empty project")
        desired_scene_ids = [str(scene["id"]) for scene in scenes]
        desired_sources = [source for scene in scenes for source in (scene.get("sources") or [])]
        desired_source_ids = [str(source["id"]) for source in desired_sources]
        if desired_source_ids:
            marks = ",".join("?" for _ in desired_source_ids)
            con.execute(
                f"DELETE FROM shared_sky_sources WHERE project_id=? AND user_id=? AND id NOT IN ({marks})",
                (session["project_id"], user_id, *desired_source_ids),
            )
        else:
            con.execute(
                "DELETE FROM shared_sky_sources WHERE project_id=? AND user_id=?",
                (session["project_id"], user_id),
            )
        marks = ",".join("?" for _ in desired_scene_ids)
        con.execute(
            f"DELETE FROM shared_sky_scenes WHERE project_id=? AND user_id=? AND id NOT IN ({marks})",
            (session["project_id"], user_id, *desired_scene_ids),
        )
        now = utc_now()
        for scene in scenes:
            con.execute(
                "INSERT INTO shared_sky_scenes(id,project_id,user_id,name,position,layout_key,transition_key,transition_ms,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,position=excluded.position,"
                "layout_key=excluded.layout_key,transition_key=excluded.transition_key,transition_ms=excluded.transition_ms,updated_at=excluded.updated_at",
                (
                    scene["id"],
                    session["project_id"],
                    user_id,
                    scene["name"],
                    scene["position"],
                    scene["layout_key"],
                    scene["transition_key"],
                    scene["transition_ms"],
                    scene.get("created_at") or now,
                    now,
                ),
            )
            meta = scene.get("metadata") or {}
            con.execute(
                "INSERT INTO shared_sky_scene_metadata(scene_id,user_id,project_id,locked,notes,folder,version,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?, ?,?) ON CONFLICT(scene_id) DO UPDATE SET locked=excluded.locked,notes=excluded.notes,"
                "folder=excluded.folder,version=shared_sky_scene_metadata.version+1,updated_at=excluded.updated_at",
                (
                    scene["id"],
                    user_id,
                    session["project_id"],
                    int(bool(meta.get("locked", False))),
                    str(meta.get("notes") or "")[:2000],
                    str(meta.get("folder") or "")[:120],
                    max(1, int(meta.get("version", 1))),
                    now,
                    now,
                ),
            )
            for source in scene.get("sources") or []:
                config = source.get("config") or {}
                validate_no_secrets(config)
                con.execute(
                    "INSERT INTO shared_sky_sources(id,scene_id,project_id,user_id,source_type,name,config_json,visible,locked,z_index,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET scene_id=excluded.scene_id,source_type=excluded.source_type,"
                    "name=excluded.name,config_json=excluded.config_json,visible=excluded.visible,locked=excluded.locked,z_index=excluded.z_index,updated_at=excluded.updated_at",
                    (
                        source["id"],
                        scene["id"],
                        session["project_id"],
                        user_id,
                        source["source_type"],
                        source["name"],
                        _json(config),
                        int(bool(source.get("visible", True))),
                        int(bool(source.get("locked", False))),
                        int(source.get("z_index", 0)),
                        source.get("created_at") or now,
                        now,
                    ),
                )
        preview_scene_id = str(snapshot.get("preview_scene_id") or desired_scene_ids[0])
        if preview_scene_id not in set(desired_scene_ids):
            preview_scene_id = desired_scene_ids[0]
        return preview_scene_id

    def undo(self, user_id: str, session_id: str, expected_version: int) -> str:
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            session = self._session(con, user_id, session_id, expected_version)
            row = con.execute(
                "SELECT * FROM shared_sky_studio_history WHERE session_id=? AND user_id=? AND undone=0 "
                "ORDER BY id DESC LIMIT 1",
                (session_id, user_id),
            ).fetchone()
            if not row:
                raise StudioConflict("Nothing to undo")
            before = _loads(row["before_json"], {})
            preview_scene_id = self._restore(con, user_id, session, before)
            self._bump(con, session, "undo", preview_scene_id=preview_scene_id)
            con.execute(
                "UPDATE shared_sky_studio_history SET undone=1,updated_at=? WHERE id=?",
                (utc_now(), row["id"]),
            )
            action = str(row["action_key"])
        latest = studio_repo.get_session(user_id, session_id)
        studio_repo._record_version(latest)
        return action

    def redo(self, user_id: str, session_id: str, expected_version: int) -> str:
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            session = self._session(con, user_id, session_id, expected_version)
            row = con.execute(
                "SELECT * FROM shared_sky_studio_history WHERE session_id=? AND user_id=? AND undone=1 "
                "ORDER BY id ASC LIMIT 1",
                (session_id, user_id),
            ).fetchone()
            if not row:
                raise StudioConflict("Nothing to redo")
            after = _loads(row["after_json"], {})
            preview_scene_id = self._restore(con, user_id, session, after)
            self._bump(con, session, "redo", preview_scene_id=preview_scene_id)
            con.execute(
                "UPDATE shared_sky_studio_history SET undone=0,updated_at=? WHERE id=?",
                (utc_now(), row["id"]),
            )
            action = str(row["action_key"])
        latest = studio_repo.get_session(user_id, session_id)
        studio_repo._record_version(latest)
        return action


history_repo = HistoryRepository(shared_sky.db_path)


def _member(request: Request):
    return require_esp_hub_member(request)


def _raise(exc: Exception) -> None:
    if isinstance(exc, KeyError):
        raise HTTPException(404, "Shared Sky studio resource not found") from exc
    if isinstance(exc, StudioConflict):
        raise HTTPException(409, str(exc)) from exc
    if isinstance(exc, StudioInvariantError):
        raise HTTPException(400, str(exc)) from exc
    raise HTTPException(500, "Shared Sky studio history/graphics operation failed") from exc


def _hydrate(user_id: str, session_id: str) -> dict[str, Any]:
    payload = studio.session(user_id, session_id)
    payload["history"] = history_repo.state(user_id, session_id)
    payload["scene_meta"] = history_repo.scene_metadata(user_id, payload["session"]["project_id"])
    return payload


@router.get("/shared-sky/studio/api/sessions/{session_id}/history")
def history_state(session_id: str, request: Request):
    member, _ = _member(request)
    try:
        payload = _hydrate(member.user_id, session_id)
        return {"history": payload["history"], "scene_meta": payload["scene_meta"]}
    except Exception as exc:
        _raise(exc)


@router.post("/shared-sky/studio/api/sessions/{session_id}/undo")
def undo(session_id: str, body: UndoRedoRequest, request: Request):
    member, _ = _member(request)
    try:
        action = history_repo.undo(member.user_id, session_id, body.expected_version)
        shared_sky.event(member.user_id, None, "studio_undo", {"session_id": session_id, "action": action})
        return _hydrate(member.user_id, session_id)
    except Exception as exc:
        _raise(exc)


@router.post("/shared-sky/studio/api/sessions/{session_id}/redo")
def redo(session_id: str, body: UndoRedoRequest, request: Request):
    member, _ = _member(request)
    try:
        action = history_repo.redo(member.user_id, session_id, body.expected_version)
        shared_sky.event(member.user_id, None, "studio_redo", {"session_id": session_id, "action": action})
        return _hydrate(member.user_id, session_id)
    except Exception as exc:
        _raise(exc)


@router.patch("/shared-sky/studio/api/sessions/{session_id}/sources/batch-transform")
def batch_transform(session_id: str, body: BatchTransformRequest, request: Request):
    member, _ = _member(request)
    try:
        history_repo.batch_transform(member.user_id, session_id, body)
        return _hydrate(member.user_id, session_id)
    except Exception as exc:
        _raise(exc)


@router.post("/shared-sky/studio/api/sessions/{session_id}/sources/tracked")
def create_source(session_id: str, body: TrackedSourceCreate, request: Request):
    member, _ = _member(request)
    try:
        source_id = history_repo.create_source(member.user_id, session_id, body)
        payload = _hydrate(member.user_id, session_id)
        payload["source_id"] = source_id
        return payload
    except Exception as exc:
        _raise(exc)


@router.post("/shared-sky/studio/api/sessions/{session_id}/sources/batch-delete")
def delete_sources(session_id: str, body: BatchDeleteRequest, request: Request):
    member, _ = _member(request)
    try:
        history_repo.delete_sources(member.user_id, session_id, body)
        return _hydrate(member.user_id, session_id)
    except Exception as exc:
        _raise(exc)


@router.post("/shared-sky/studio/api/sessions/{session_id}/scenes/tracked")
def create_scene(session_id: str, body: SceneCreateTracked, request: Request):
    member, _ = _member(request)
    try:
        scene_id = history_repo.create_scene(member.user_id, session_id, body)
        payload = _hydrate(member.user_id, session_id)
        payload["scene_id"] = scene_id
        return payload
    except Exception as exc:
        _raise(exc)


@router.post("/shared-sky/studio/api/sessions/{session_id}/scenes/{scene_id}/duplicate-tracked")
def duplicate_scene(session_id: str, scene_id: str, body: SceneDuplicateTracked, request: Request):
    member, _ = _member(request)
    try:
        new_scene_id = history_repo.duplicate_scene(member.user_id, session_id, scene_id, body)
        payload = _hydrate(member.user_id, session_id)
        payload["scene_id"] = new_scene_id
        return payload
    except Exception as exc:
        _raise(exc)


@router.patch("/shared-sky/studio/api/sessions/{session_id}/scenes/{scene_id}/tracked")
def patch_scene(session_id: str, scene_id: str, body: ScenePatchTracked, request: Request):
    member, _ = _member(request)
    try:
        history_repo.patch_scene(member.user_id, session_id, scene_id, body)
        return _hydrate(member.user_id, session_id)
    except Exception as exc:
        _raise(exc)


@router.delete("/shared-sky/studio/api/sessions/{session_id}/scenes/{scene_id}/tracked")
def delete_scene(session_id: str, scene_id: str, body: SceneDeleteTracked, request: Request):
    member, _ = _member(request)
    try:
        history_repo.delete_scene(member.user_id, session_id, scene_id, body)
        return _hydrate(member.user_id, session_id)
    except Exception as exc:
        _raise(exc)


@router.post("/shared-sky/studio/api/sessions/{session_id}/scenes/reorder-tracked")
def reorder_scenes(session_id: str, body: SceneReorderTracked, request: Request):
    member, _ = _member(request)
    try:
        history_repo.reorder_scenes(member.user_id, session_id, body)
        return _hydrate(member.user_id, session_id)
    except Exception as exc:
        _raise(exc)


@router.post("/shared-sky/studio/api/sessions/{session_id}/graphics")
def create_graphic(session_id: str, body: GraphicCreateRequest, request: Request):
    member, _ = _member(request)
    try:
        source_id = history_repo.create_graphic(member.user_id, session_id, body)
        payload = _hydrate(member.user_id, session_id)
        payload["source_id"] = source_id
        return payload
    except Exception as exc:
        _raise(exc)


def install_shared_sky_studio_history_graphics(app: Any) -> None:
    existing = {getattr(route, "path", "") for route in app.router.routes}
    marker = "/shared-sky/studio/api/sessions/{session_id}/history"
    if marker not in existing:
        app.include_router(router)


__all__ = [
    "BatchDeleteRequest",
    "BatchTransformRequest",
    "GRAPHIC_KINDS",
    "GraphicCreateRequest",
    "HistoryRepository",
    "SceneCreateTracked",
    "SceneDeleteTracked",
    "SceneDuplicateTracked",
    "ScenePatchTracked",
    "SceneReorderTracked",
    "TrackedSourceCreate",
    "history_repo",
    "install_shared_sky_studio_history_graphics",
    "normalize_graphic_style",
    "router",
]
