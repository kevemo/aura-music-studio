from __future__ import annotations

import json
import os
import shlex
import sqlite3
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


@dataclass
class VisualKeyframe:
    time_seconds: float
    property: str
    value: float | str | list[float]
    easing: str = "linear"


@dataclass
class VisualEffect:
    kind: str
    enabled: bool = True
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass
class VisualLayer:
    id: str
    name: str
    layer_type: str
    source: str | None
    start_seconds: float
    end_seconds: float
    z_index: int
    opacity: float = 1.0
    blend_mode: str = "normal"
    transform: dict[str, Any] = field(default_factory=dict)
    mask: dict[str, Any] | None = None
    effects: list[VisualEffect] = field(default_factory=list)
    keyframes: list[VisualKeyframe] = field(default_factory=list)
    text: str | None = None


class VisualFxError(RuntimeError):
    pass


class VisualFxStore:
    VALID_LAYER_TYPES = {"video", "image", "text", "audio", "shape", "adjustment", "effect"}
    VALID_BLEND_MODES = {
        "normal", "multiply", "screen", "overlay", "soft_light", "hard_light", "darken", "lighten", "add", "difference"
    }

    def __init__(self, db_path: str | Path | None = None, output_root: str | Path | None = None):
        self.db_path = str(db_path or os.getenv("LSS_DB_PATH") or "data/live_sound_studio.sqlite3")
        self.output_root = Path(output_root or os.getenv("AURA_VISUAL_FX_OUTPUT_DIR", "outputs/visual_fx"))
        self.output_root.mkdir(parents=True, exist_ok=True)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self):
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS visual_fx_projects (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    width INTEGER NOT NULL,
                    height INTEGER NOT NULL,
                    fps REAL NOT NULL,
                    duration_seconds REAL NOT NULL,
                    background TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_visual_fx_projects_user ON visual_fx_projects(user_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS visual_fx_layers (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    layer_type TEXT NOT NULL,
                    source TEXT,
                    start_seconds REAL NOT NULL,
                    end_seconds REAL NOT NULL,
                    z_index INTEGER NOT NULL,
                    opacity REAL NOT NULL,
                    blend_mode TEXT NOT NULL,
                    transform_json TEXT NOT NULL,
                    mask_json TEXT,
                    effects_json TEXT NOT NULL,
                    keyframes_json TEXT NOT NULL,
                    text_value TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES visual_fx_projects(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_visual_fx_layers_project ON visual_fx_layers(project_id, z_index, created_at);
                """
            )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def create_project(
        self,
        *,
        user_id: str,
        name: str,
        width: int,
        height: int,
        fps: float,
        duration_seconds: float,
        background: str = "#000000",
    ) -> dict[str, Any]:
        if not name.strip():
            raise VisualFxError("Project name is required")
        if width < 256 or height < 256 or width > 7680 or height > 7680:
            raise VisualFxError("Canvas dimensions must be between 256 and 7680 pixels")
        if fps < 1 or fps > 240:
            raise VisualFxError("Frame rate must be between 1 and 240 fps")
        if duration_seconds <= 0 or duration_seconds > 21600:
            raise VisualFxError("Project duration must be between 0 and 6 hours")
        project_id = uuid4().hex
        now = self._now()
        with self._connect() as con:
            con.execute(
                """INSERT INTO visual_fx_projects
                (id,user_id,name,width,height,fps,duration_seconds,background,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (project_id, user_id, name.strip(), width, height, fps, duration_seconds, background, now, now),
            )
        return self.get_project(user_id, project_id)

    def list_projects(self, user_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM visual_fx_projects WHERE user_id=? ORDER BY updated_at DESC LIMIT ?",
                (user_id, max(1, min(limit, 200))),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_project(self, user_id: str, project_id: str) -> dict[str, Any]:
        with self._connect() as con:
            project = con.execute(
                "SELECT * FROM visual_fx_projects WHERE user_id=? AND id=?", (user_id, project_id)
            ).fetchone()
            if not project:
                raise VisualFxError("Visual FX project not found")
            layers = con.execute(
                "SELECT * FROM visual_fx_layers WHERE user_id=? AND project_id=? ORDER BY z_index ASC, created_at ASC",
                (user_id, project_id),
            ).fetchall()
        result = dict(project)
        result["layers"] = [self._decode_layer(row) for row in layers]
        return result

    @staticmethod
    def _decode_layer(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["transform"] = json.loads(data.pop("transform_json") or "{}")
        data["mask"] = json.loads(data.pop("mask_json")) if data.get("mask_json") else None
        data.pop("mask_json", None)
        data["effects"] = json.loads(data.pop("effects_json") or "[]")
        data["keyframes"] = json.loads(data.pop("keyframes_json") or "[]")
        data["text"] = data.pop("text_value")
        return data

    def add_layer(self, *, user_id: str, project_id: str, layer: VisualLayer) -> dict[str, Any]:
        project = self.get_project(user_id, project_id)
        if layer.layer_type not in self.VALID_LAYER_TYPES:
            raise VisualFxError(f"Unsupported layer type: {layer.layer_type}")
        if layer.blend_mode not in self.VALID_BLEND_MODES:
            raise VisualFxError(f"Unsupported blend mode: {layer.blend_mode}")
        if layer.start_seconds < 0 or layer.end_seconds <= layer.start_seconds:
            raise VisualFxError("Layer timing is invalid")
        if layer.end_seconds > float(project["duration_seconds"]) + 0.001:
            raise VisualFxError("Layer exceeds project duration")
        if not 0 <= layer.opacity <= 1:
            raise VisualFxError("Layer opacity must be between 0 and 1")
        if layer.layer_type in {"video", "image", "audio"} and not layer.source:
            raise VisualFxError(f"{layer.layer_type} layer requires a source")
        if layer.layer_type == "text" and not (layer.text or "").strip():
            raise VisualFxError("Text layer requires text")
        now = self._now()
        with self._connect() as con:
            con.execute(
                """INSERT INTO visual_fx_layers
                (id,project_id,user_id,name,layer_type,source,start_seconds,end_seconds,z_index,opacity,blend_mode,
                 transform_json,mask_json,effects_json,keyframes_json,text_value,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    layer.id, project_id, user_id, layer.name, layer.layer_type, layer.source,
                    layer.start_seconds, layer.end_seconds, layer.z_index, layer.opacity, layer.blend_mode,
                    json.dumps(layer.transform), json.dumps(layer.mask) if layer.mask is not None else None,
                    json.dumps([asdict(x) for x in layer.effects]),
                    json.dumps([asdict(x) for x in layer.keyframes]), layer.text, now, now,
                ),
            )
            con.execute("UPDATE visual_fx_projects SET updated_at=? WHERE id=? AND user_id=?", (now, project_id, user_id))
        return self.get_project(user_id, project_id)

    def update_layer(self, *, user_id: str, project_id: str, layer_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        project = self.get_project(user_id, project_id)
        current = next((x for x in project["layers"] if x["id"] == layer_id), None)
        if not current:
            raise VisualFxError("Visual FX layer not found")
        allowed = {
            "name", "source", "start_seconds", "end_seconds", "z_index", "opacity", "blend_mode",
            "transform", "mask", "effects", "keyframes", "text",
        }
        unknown = set(changes) - allowed
        if unknown:
            raise VisualFxError(f"Unsupported layer fields: {', '.join(sorted(unknown))}")
        merged = {**current, **changes}
        if merged["blend_mode"] not in self.VALID_BLEND_MODES:
            raise VisualFxError(f"Unsupported blend mode: {merged['blend_mode']}")
        if float(merged["end_seconds"]) > float(project["duration_seconds"]) + 0.001:
            raise VisualFxError("Layer exceeds project duration")
        now = self._now()
        with self._connect() as con:
            cursor = con.execute(
                """UPDATE visual_fx_layers SET
                name=?, source=?, start_seconds=?, end_seconds=?, z_index=?, opacity=?, blend_mode=?, transform_json=?,
                mask_json=?, effects_json=?, keyframes_json=?, text_value=?, updated_at=?
                WHERE id=? AND project_id=? AND user_id=?""",
                (
                    merged["name"], merged.get("source"), float(merged["start_seconds"]), float(merged["end_seconds"]),
                    int(merged["z_index"]), float(merged["opacity"]), merged["blend_mode"],
                    json.dumps(merged.get("transform") or {}),
                    json.dumps(merged.get("mask")) if merged.get("mask") is not None else None,
                    json.dumps(merged.get("effects") or []), json.dumps(merged.get("keyframes") or []), merged.get("text"),
                    now, layer_id, project_id, user_id,
                ),
            )
            if cursor.rowcount != 1:
                raise VisualFxError("Visual FX layer not found")
            con.execute("UPDATE visual_fx_projects SET updated_at=? WHERE id=? AND user_id=?", (now, project_id, user_id))
        return self.get_project(user_id, project_id)

    def delete_layer(self, *, user_id: str, project_id: str, layer_id: str) -> dict[str, Any]:
        with self._connect() as con:
            cursor = con.execute(
                "DELETE FROM visual_fx_layers WHERE id=? AND project_id=? AND user_id=?", (layer_id, project_id, user_id)
            )
            if cursor.rowcount != 1:
                raise VisualFxError("Visual FX layer not found")
            con.execute(
                "UPDATE visual_fx_projects SET updated_at=? WHERE id=? AND user_id=?", (self._now(), project_id, user_id)
            )
        return self.get_project(user_id, project_id)

    def render_project(self, *, user_id: str, project_id: str, output_kind: str = "mp4") -> dict[str, Any]:
        """Render through a configured production compositor.

        The core owns the canonical non-destructive project schema. Rendering is delegated to a real
        compositor command so unsupported effects are never silently ignored. The command receives a
        project JSON path and output path through environment variables and must create the output.
        """
        project = self.get_project(user_id, project_id)
        command = os.getenv("AURA_VISUAL_FX_RENDER_CMD", "").strip()
        if not command:
            raise VisualFxError("AURA_VISUAL_FX_RENDER_CMD is not configured")
        if output_kind not in {"mp4", "png"}:
            raise VisualFxError("Visual FX export must be mp4 or png")
        render_id = uuid4().hex
        work = self.output_root / f"{render_id}.project.json"
        output = self.output_root / f"{render_id}.{output_kind}"
        work.write_text(json.dumps(project, indent=2), encoding="utf-8")
        env = os.environ.copy()
        env.update(
            {
                "AURA_VISUAL_FX_PROJECT_JSON": str(work.resolve()),
                "AURA_VISUAL_FX_OUTPUT": str(output.resolve()),
                "AURA_VISUAL_FX_OUTPUT_KIND": output_kind,
            }
        )
        completed = subprocess.run(
            shlex.split(command),
            env=env,
            capture_output=True,
            text=True,
            timeout=int(os.getenv("AURA_VISUAL_FX_RENDER_TIMEOUT", "3600")),
            check=False,
        )
        if completed.returncode != 0:
            raise VisualFxError(completed.stderr.strip() or "Visual FX renderer failed")
        if not output.exists() or output.stat().st_size < 1024:
            raise VisualFxError("Visual FX renderer did not produce a valid output")
        return {"id": render_id, "status": "completed", "output_path": str(output), "output_kind": output_kind}
