from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


class ImageJobStore:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
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
                CREATE TABLE IF NOT EXISTS image_generation_jobs (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    project_id TEXT,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    status TEXT NOT NULL,
                    output_path TEXT,
                    request_json TEXT NOT NULL,
                    provenance_hash TEXT NOT NULL,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_image_jobs_user_created
                    ON image_generation_jobs(user_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS image_edit_lineage (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    parent_job_id TEXT NOT NULL,
                    child_job_id TEXT NOT NULL UNIQUE,
                    edit_prompt TEXT NOT NULL,
                    source_sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(parent_job_id) REFERENCES image_generation_jobs(id) ON DELETE CASCADE,
                    FOREIGN KEY(child_job_id) REFERENCES image_generation_jobs(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_image_edit_lineage_user_parent
                    ON image_edit_lineage(user_id, parent_job_id, created_at DESC);
                """
            )

    def save(
        self,
        *,
        user_id: str,
        result: dict[str, Any],
        mode: str,
        prompt: str,
        project_id: str | None,
        provenance_hash: str,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as con:
            con.execute(
                """
                INSERT OR REPLACE INTO image_generation_jobs
                (id,user_id,project_id,provider,model,mode,prompt,status,output_path,request_json,provenance_hash,error,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    result["id"], user_id, project_id, result["provider"], result["model"], mode, prompt,
                    result["status"], result.get("output_path"), result.get("request_json") or "{}",
                    provenance_hash, result.get("error"), result.get("created_at") or now, now,
                ),
            )

    def save_edit_lineage(
        self,
        *,
        user_id: str,
        parent_job_id: str,
        child_job_id: str,
        edit_prompt: str,
        source_sha256: str,
    ) -> dict[str, Any]:
        parent = self.get_for_user(user_id, parent_job_id)
        child = self.get_for_user(user_id, child_job_id)
        if not parent or not child:
            raise ValueError("Image edit lineage must reference user-owned image jobs")
        now = datetime.now(timezone.utc).isoformat()
        lineage_id = uuid4().hex
        with self._connect() as con:
            con.execute(
                """INSERT INTO image_edit_lineage
                   (id,user_id,parent_job_id,child_job_id,edit_prompt,source_sha256,created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (lineage_id, user_id, parent_job_id, child_job_id, edit_prompt, source_sha256, now),
            )
        return {
            "id": lineage_id,
            "parent_job_id": parent_job_id,
            "child_job_id": child_job_id,
            "edit_prompt": edit_prompt,
            "source_sha256": source_sha256,
            "created_at": now,
        }

    def list_for_user(self, user_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM image_generation_jobs WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
                (user_id, max(1, min(limit, 200))),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_for_user(self, user_id: str, job_id: str) -> dict[str, Any] | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM image_generation_jobs WHERE user_id=? AND id=?",
                (user_id, job_id),
            ).fetchone()
        return dict(row) if row else None

    def lineage_for_user(self, user_id: str, job_id: str) -> dict[str, Any]:
        job = self.get_for_user(user_id, job_id)
        if not job:
            raise KeyError(job_id)
        with self._connect() as con:
            parent = con.execute(
                """SELECT parent_job_id,edit_prompt,source_sha256,created_at
                   FROM image_edit_lineage WHERE user_id=? AND child_job_id=?""",
                (user_id, job_id),
            ).fetchone()
            children = con.execute(
                """SELECT child_job_id,edit_prompt,source_sha256,created_at
                   FROM image_edit_lineage WHERE user_id=? AND parent_job_id=?
                   ORDER BY created_at ASC""",
                (user_id, job_id),
            ).fetchall()
        return {
            "job_id": job_id,
            "parent": dict(parent) if parent else None,
            "children": [dict(row) for row in children],
        }
