from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .esp_command_center import EspStore, esp
from .esp_niche import EspNicheStore, niche_definition

_ALLOWED_EXTENSIONS = {".csv", ".json", ".xlsx", ".txt", ".pdf", ".png", ".jpg", ".jpeg", ".webp"}
_MAX_UPLOAD_BYTES = 10 * 1024 * 1024


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_filename(name: str) -> str:
    raw = Path(name or "analysis-upload").name
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", raw).strip("._")[:160]
    return safe or "analysis-upload"


class EspProgressStore:
    def __init__(self, esp_store: EspStore | None = None):
        self.esp = esp_store or esp
        self.db_path = self.esp.db_path
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
                CREATE TABLE IF NOT EXISTS esp_performance_submissions (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    period_label TEXT,
                    metrics_json TEXT NOT NULL DEFAULT '{}',
                    notes TEXT,
                    upload_name TEXT,
                    upload_path TEXT,
                    upload_content_type TEXT,
                    aura_guidance_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_esp_performance_user_created
                    ON esp_performance_submissions(user_id, created_at DESC);
                """
            )

    def add(
        self,
        user_id: str,
        *,
        kind: str,
        period_label: str = "",
        metrics: dict | None = None,
        notes: str = "",
        upload_name: str | None = None,
        upload_path: str | None = None,
        upload_content_type: str | None = None,
    ) -> dict:
        kind = (kind or "").strip().lower()
        if kind not in {"live", "video"}:
            raise ValueError("Progress type must be live or video")
        cleaned_metrics: dict[str, float | int | str] = {}
        for key, value in (metrics or {}).items():
            if value in (None, ""):
                continue
            name = re.sub(r"[^a-z0-9_]+", "_", str(key).lower()).strip("_")[:80]
            if not name:
                continue
            if isinstance(value, (int, float)):
                cleaned_metrics[name] = value
            else:
                text = str(value).strip()[:240]
                try:
                    cleaned_metrics[name] = float(text) if "." in text else int(text)
                except ValueError:
                    cleaned_metrics[name] = text
        guidance = self.guidance(user_id, kind, cleaned_metrics)
        row_id = uuid4().hex
        with self._connect() as con:
            con.execute(
                """INSERT INTO esp_performance_submissions
                   (id,user_id,kind,period_label,metrics_json,notes,upload_name,upload_path,
                    upload_content_type,aura_guidance_json,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    row_id,
                    user_id,
                    kind,
                    (period_label or "").strip()[:160],
                    json.dumps(cleaned_metrics, sort_keys=True),
                    (notes or "").strip()[:4000],
                    upload_name,
                    upload_path,
                    upload_content_type,
                    json.dumps(guidance),
                    _now(),
                ),
            )
        return self.get(row_id) or {}

    def get(self, submission_id: str) -> dict | None:
        with self._connect() as con:
            row = con.execute("SELECT * FROM esp_performance_submissions WHERE id=?", (submission_id,)).fetchone()
        return self._decode(row)

    def list_for_user(self, user_id: str, limit: int = 100) -> list[dict]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM esp_performance_submissions WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
                (user_id, max(1, min(int(limit), 500))),
            ).fetchall()
        return [self._decode(row) for row in rows if row]

    def summary(self, user_id: str) -> dict:
        rows = self.list_for_user(user_id, 200)
        live = [row for row in rows if row["kind"] == "live"]
        video = [row for row in rows if row["kind"] == "video"]
        return {
            "total": len(rows),
            "live": len(live),
            "video": len(video),
            "latest": rows[0] if rows else None,
            "latest_live": live[0] if live else None,
            "latest_video": video[0] if video else None,
        }

    def guidance(self, user_id: str, kind: str, metrics: dict) -> list[str]:
        profile = EspNicheStore(self.esp).get(user_id)
        niche = niche_definition((profile or {}).get("niche"))
        actions: list[str] = []

        def number(name: str) -> float | None:
            value = metrics.get(name)
            try:
                return float(value) if value is not None else None
            except (TypeError, ValueError):
                return None

        if kind == "live":
            duration = number("duration_minutes")
            watch = number("avg_watch_seconds")
            followers = number("new_followers")
            shares = number("shares")
            if duration is not None and duration < 120:
                actions.append("Where practical, build toward structured 2+ hour LIVE sessions so recurring segments have time to compound.")
            if watch is not None and watch < 60:
                actions.append("Prioritise the opening minute: state what the LIVE is, give viewers an immediate reason to stay, then reset the room regularly.")
            if followers is not None and followers <= 0:
                actions.append("Add a natural follow reason tied to the next recurring segment rather than repeatedly asking for follows.")
            if shares is not None and shares <= 0:
                actions.append("Create at least one clearly shareable moment or audience prompt during the next LIVE.")
            actions.append("Review the LIVE in 15–20 minute blocks and note where arrivals, conversation and retention rose or dropped.")
        else:
            views = number("views")
            completion = number("completion_rate")
            shares = number("shares")
            saves = number("saves")
            if completion is not None and completion < 35:
                actions.append("Test a faster first-second hook and remove setup that delays the video's payoff.")
            if shares is not None and shares <= 0:
                actions.append("Build one useful, emotional or surprising moment that gives viewers a reason to share the post.")
            if saves is not None and saves <= 0:
                actions.append("For educational or reference-style content, add a practical takeaway worth saving.")
            if views is not None and views < 500:
                actions.append("Test several hook/cover/caption variants around the same core idea before abandoning the topic.")
            actions.append("Compare this post with the creator's best recent post and identify one repeatable difference in hook, topic, pacing or presentation.")

        for item in niche.get("training", [])[:2]:
            actions.append(f"Niche priority — {item}")
        return actions[:8]

    @staticmethod
    def _decode(row) -> dict | None:
        if not row:
            return None
        item = dict(row)
        try:
            item["metrics"] = json.loads(item.pop("metrics_json") or "{}")
        except Exception:
            item["metrics"] = {}
        try:
            item["aura_guidance"] = json.loads(item.pop("aura_guidance_json") or "[]")
        except Exception:
            item["aura_guidance"] = []
        return item


def save_progress_upload(user_id: str, filename: str, content: bytes) -> tuple[str, str]:
    safe = _safe_filename(filename)
    suffix = Path(safe).suffix.lower()
    if suffix not in _ALLOWED_EXTENSIONS:
        raise ValueError("Upload must be CSV, JSON, XLSX, TXT, PDF, PNG, JPG/JPEG or WEBP")
    if len(content) > _MAX_UPLOAD_BYTES:
        raise ValueError("Progress upload must be 10 MB or smaller")
    root = Path(os.getenv("ESP_PROGRESS_ROOT", "data/esp_progress")).resolve() / user_id
    root.mkdir(parents=True, exist_ok=True)
    target = (root / f"{uuid4().hex}_{safe}").resolve()
    if root not in target.parents:
        raise ValueError("Invalid upload path")
    target.write_bytes(content)
    return safe, str(target)
