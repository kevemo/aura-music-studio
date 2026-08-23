from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class VisualFxRenderOwnershipError(RuntimeError):
    pass


class VisualFxRenderOwnership:
    """Fail-closed tenant ownership for completed Visual FX exports.

    Files in the renderer output directory are not capabilities. A client may download an
    export only when this ledger binds the render id to the signed-in user who created it.
    """

    VALID_OUTPUT_KINDS = {"mp4", "png"}

    def __init__(self, db_path: str | Path, output_root: str | Path):
        self.db_path = str(db_path)
        self.output_root = Path(output_root).resolve()
        self.output_root.mkdir(parents=True, exist_ok=True)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self):
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        return con

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS visual_fx_render_ownership (
                    render_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    output_kind TEXT NOT NULL,
                    output_name TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_visual_fx_render_owner
                ON visual_fx_render_ownership(user_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_visual_fx_render_project
                ON visual_fx_render_ownership(user_id, project_id, created_at DESC);
                """
            )

    def register(
        self,
        *,
        user_id: str,
        project_id: str,
        render_id: str,
        output_kind: str,
        output_path: str | Path,
    ) -> dict:
        user = (user_id or "").strip()
        project = (project_id or "").strip()
        render = (render_id or "").strip()
        kind = (output_kind or "").strip().lower()
        if not user or not project or not render:
            raise VisualFxRenderOwnershipError("Render ownership requires user, project and render identifiers")
        if kind not in self.VALID_OUTPUT_KINDS:
            raise VisualFxRenderOwnershipError("Unsupported Visual FX output kind")

        candidate = Path(output_path).resolve()
        expected = (self.output_root / f"{render}.{kind}").resolve()
        if candidate != expected or self.output_root not in candidate.parents:
            raise VisualFxRenderOwnershipError("Renderer output is outside the Visual FX export boundary")
        if not candidate.is_file():
            raise VisualFxRenderOwnershipError("Rendered output is unavailable")

        now = self._now()
        with self._connect() as con:
            existing = con.execute(
                "SELECT * FROM visual_fx_render_ownership WHERE render_id=?", (render,)
            ).fetchone()
            if existing:
                item = dict(existing)
                if (
                    item["user_id"] != user
                    or item["project_id"] != project
                    or item["output_kind"] != kind
                    or item["output_name"] != candidate.name
                ):
                    raise VisualFxRenderOwnershipError("Render id is already owned by a different export")
                return item
            con.execute(
                """INSERT INTO visual_fx_render_ownership
                   (render_id,user_id,project_id,output_kind,output_name,created_at)
                   VALUES (?,?,?,?,?,?)""",
                (render, user, project, kind, candidate.name, now),
            )
        return {
            "render_id": render,
            "user_id": user,
            "project_id": project,
            "output_kind": kind,
            "output_name": candidate.name,
            "created_at": now,
        }

    def get(self, *, user_id: str, render_id: str, output_kind: str | None = None) -> dict:
        kind = (output_kind or "").strip().lower() or None
        if kind is not None and kind not in self.VALID_OUTPUT_KINDS:
            raise VisualFxRenderOwnershipError("Rendered output is unavailable")
        with self._connect() as con:
            if kind:
                row = con.execute(
                    """SELECT * FROM visual_fx_render_ownership
                       WHERE render_id=? AND user_id=? AND output_kind=?""",
                    (render_id, user_id, kind),
                ).fetchone()
            else:
                row = con.execute(
                    "SELECT * FROM visual_fx_render_ownership WHERE render_id=? AND user_id=?",
                    (render_id, user_id),
                ).fetchone()
        if not row:
            # Deliberately indistinguishable from a nonexistent render to prevent ownership probing.
            raise VisualFxRenderOwnershipError("Rendered output is unavailable")
        return dict(row)

    def resolve(self, *, user_id: str, render_id: str, output_kind: str) -> Path:
        item = self.get(user_id=user_id, render_id=render_id, output_kind=output_kind)
        output = (self.output_root / item["output_name"]).resolve()
        expected = (self.output_root / f"{item['render_id']}.{item['output_kind']}").resolve()
        if output != expected or self.output_root not in output.parents or not output.is_file():
            raise VisualFxRenderOwnershipError("Rendered output is unavailable")
        return output

    def list_for_user(self, user_id: str, *, project_id: str | None = None, limit: int = 100) -> list[dict]:
        capped = max(1, min(int(limit), 500))
        with self._connect() as con:
            if project_id:
                rows = con.execute(
                    """SELECT render_id,project_id,output_kind,created_at
                       FROM visual_fx_render_ownership
                       WHERE user_id=? AND project_id=? ORDER BY created_at DESC LIMIT ?""",
                    (user_id, project_id, capped),
                ).fetchall()
            else:
                rows = con.execute(
                    """SELECT render_id,project_id,output_kind,created_at
                       FROM visual_fx_render_ownership
                       WHERE user_id=? ORDER BY created_at DESC LIMIT ?""",
                    (user_id, capped),
                ).fetchall()
        return [dict(row) for row in rows]
