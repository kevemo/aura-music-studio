from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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
            con.execute(
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
                )
                """
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_image_jobs_user_created ON image_generation_jobs(user_id, created_at DESC)"
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
