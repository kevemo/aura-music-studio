from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class VideoJobStore:
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
                CREATE TABLE IF NOT EXISTS video_generation_jobs (
                    id TEXT PRIMARY KEY,
                    user_id TEXT,
                    project_id TEXT,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    provider_job_id TEXT,
                    mode TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    status TEXT NOT NULL,
                    output_url TEXT,
                    output_path TEXT,
                    request_json TEXT NOT NULL,
                    provenance_hash TEXT NOT NULL,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def save(self, *, user_id: str | None, result: dict[str, Any], mode: str, prompt: str, project_id: str | None, provenance_hash: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as con:
            con.execute(
                """
                INSERT OR REPLACE INTO video_generation_jobs
                (id,user_id,project_id,provider,model,provider_job_id,mode,prompt,status,output_url,output_path,request_json,provenance_hash,error,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    result["id"], user_id, project_id, result["provider"], result["model"], result.get("provider_job_id"),
                    mode, prompt, result["status"], result.get("output_url"), result.get("output_path"),
                    result.get("request_json") or json.dumps({}), provenance_hash, result.get("error"),
                    result.get("created_at") or now, now,
                ),
            )

    def list_for_user(self, user_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM video_generation_jobs WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
                (user_id, max(1, min(limit, 200))),
            ).fetchall()
        return [dict(row) for row in rows]
