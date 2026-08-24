from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from .accounts import AccountStore
from .social_management import SocialHouseStore

MediaKind = Literal["image", "video", "audio", "document", "creative_project", "other"]
CommentVisibility = Literal["internal", "approval"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class EspSocialIntelligenceStore:
    """Durable Rella-class support data for the private ESP Social Hub.

    SocialHouse JSON remains the planning/project document. This store keeps potentially
    high-volume media metadata, analytics snapshots and approval discussions in SQLite so
    they can be queried efficiently without bloating every SocialHouse file.
    """

    def __init__(self, accounts: AccountStore | None = None):
        self.accounts = accounts or AccountStore()
        self.db_path = self.accounts.db_path
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
                CREATE TABLE IF NOT EXISTS esp_social_media (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    space_id TEXT NOT NULL,
                    label TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    source_ref TEXT NOT NULL,
                    folder TEXT NOT NULL DEFAULT '',
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    rights_confirmed INTEGER NOT NULL DEFAULT 0,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_esp_social_media_space
                    ON esp_social_media(user_id,space_id,created_at DESC);

                CREATE TABLE IF NOT EXISTS esp_social_analytics (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    space_id TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    period_label TEXT NOT NULL DEFAULT '',
                    content_id TEXT,
                    metrics_json TEXT NOT NULL DEFAULT '{}',
                    source_ref TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_esp_social_analytics_space
                    ON esp_social_analytics(user_id,space_id,created_at DESC);

                CREATE TABLE IF NOT EXISTS esp_social_comments (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    space_id TEXT NOT NULL,
                    content_id TEXT NOT NULL,
                    visibility TEXT NOT NULL,
                    author TEXT NOT NULL,
                    body TEXT NOT NULL,
                    resolved INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    resolved_at TEXT,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_esp_social_comments_content
                    ON esp_social_comments(user_id,space_id,content_id,created_at ASC);
                """
            )

    @staticmethod
    def _loads(value: str | None, fallback):
        try:
            return json.loads(value or "")
        except Exception:
            return fallback

    def add_media(
        self,
        user_id: str,
        space_id: str,
        *,
        label: str,
        kind: MediaKind,
        source_ref: str,
        folder: str = "",
        tags: list[str] | None = None,
        rights_confirmed: bool,
        metadata: dict | None = None,
    ) -> dict:
        if not rights_confirmed:
            raise ValueError("Rights/authorization confirmation is required for Social Hub media")
        media_id = uuid4().hex
        cleaned_tags = [str(value).strip()[:80] for value in (tags or []) if str(value).strip()][:40]
        with self._connect() as con:
            con.execute(
                """INSERT INTO esp_social_media
                   (id,user_id,space_id,label,kind,source_ref,folder,tags_json,rights_confirmed,metadata_json,created_at)
                   VALUES (?,?,?,?,?,?,?,?,1,?,?)""",
                (
                    media_id,
                    user_id,
                    space_id,
                    (label or "Media").strip()[:200],
                    kind,
                    source_ref.strip()[:1200],
                    (folder or "").strip()[:160],
                    json.dumps(cleaned_tags, ensure_ascii=False),
                    json.dumps(metadata or {}, ensure_ascii=False),
                    _now(),
                ),
            )
        return self.media(user_id, space_id, media_id) or {}

    def media(self, user_id: str, space_id: str, media_id: str) -> dict | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM esp_social_media WHERE id=? AND user_id=? AND space_id=?",
                (media_id, user_id, space_id),
            ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["tags"] = self._loads(item.pop("tags_json"), [])
        item["metadata"] = self._loads(item.pop("metadata_json"), {})
        item["rights_confirmed"] = bool(item["rights_confirmed"])
        return item

    def list_media(self, user_id: str, space_id: str, folder: str | None = None, limit: int = 200) -> list[dict]:
        with self._connect() as con:
            if folder is None:
                rows = con.execute(
                    "SELECT * FROM esp_social_media WHERE user_id=? AND space_id=? ORDER BY created_at DESC LIMIT ?",
                    (user_id, space_id, max(1, min(limit, 500))),
                ).fetchall()
            else:
                rows = con.execute(
                    "SELECT * FROM esp_social_media WHERE user_id=? AND space_id=? AND folder=? ORDER BY created_at DESC LIMIT ?",
                    (user_id, space_id, folder[:160], max(1, min(limit, 500))),
                ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["tags"] = self._loads(item.pop("tags_json"), [])
            item["metadata"] = self._loads(item.pop("metadata_json"), {})
            item["rights_confirmed"] = bool(item["rights_confirmed"])
            result.append(item)
        return result

    def add_analytics(
        self,
        user_id: str,
        space_id: str,
        *,
        platform: str,
        period_label: str = "",
        content_id: str | None = None,
        metrics: dict | None = None,
        source_ref: str | None = None,
    ) -> dict:
        clean_metrics: dict[str, float | int | str] = {}
        for key, value in (metrics or {}).items():
            clean_key = str(key).strip()[:100]
            if not clean_key:
                continue
            if isinstance(value, (int, float)):
                clean_metrics[clean_key] = value
            elif isinstance(value, str):
                clean_metrics[clean_key] = value[:300]
        analytics_id = uuid4().hex
        now = _now()
        with self._connect() as con:
            con.execute(
                """INSERT INTO esp_social_analytics
                   (id,user_id,space_id,platform,period_label,content_id,metrics_json,source_ref,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    analytics_id,
                    user_id,
                    space_id,
                    (platform or "custom").strip().lower()[:80],
                    (period_label or "").strip()[:180],
                    (content_id or "").strip()[:160] or None,
                    json.dumps(clean_metrics, ensure_ascii=False),
                    (source_ref or "").strip()[:1200] or None,
                    now,
                ),
            )
            row = con.execute("SELECT * FROM esp_social_analytics WHERE id=?", (analytics_id,)).fetchone()
        item = dict(row)
        item["metrics"] = self._loads(item.pop("metrics_json"), {})
        return item

    def list_analytics(self, user_id: str, space_id: str, limit: int = 200) -> list[dict]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM esp_social_analytics WHERE user_id=? AND space_id=? ORDER BY created_at DESC LIMIT ?",
                (user_id, space_id, max(1, min(limit, 500))),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["metrics"] = self._loads(item.pop("metrics_json"), {})
            result.append(item)
        return result

    def add_comment(
        self,
        user_id: str,
        space_id: str,
        content_id: str,
        *,
        visibility: CommentVisibility,
        author: str,
        body: str,
    ) -> dict:
        comment_id = uuid4().hex
        now = _now()
        with self._connect() as con:
            con.execute(
                """INSERT INTO esp_social_comments
                   (id,user_id,space_id,content_id,visibility,author,body,resolved,created_at)
                   VALUES (?,?,?,?,?,?,?,0,?)""",
                (
                    comment_id,
                    user_id,
                    space_id,
                    content_id,
                    visibility,
                    (author or "ESP Member").strip()[:160],
                    body.strip()[:5000],
                    now,
                ),
            )
            row = con.execute("SELECT * FROM esp_social_comments WHERE id=?", (comment_id,)).fetchone()
        item = dict(row)
        item["resolved"] = bool(item["resolved"])
        return item

    def comments(self, user_id: str, space_id: str, content_id: str) -> list[dict]:
        with self._connect() as con:
            rows = con.execute(
                """SELECT * FROM esp_social_comments
                   WHERE user_id=? AND space_id=? AND content_id=? ORDER BY created_at ASC""",
                (user_id, space_id, content_id),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["resolved"] = bool(item["resolved"])
            result.append(item)
        return result

    def resolve_comment(self, user_id: str, comment_id: str) -> dict:
        now = _now()
        with self._connect() as con:
            con.execute(
                "UPDATE esp_social_comments SET resolved=1,resolved_at=? WHERE id=? AND user_id=?",
                (now, comment_id, user_id),
            )
            row = con.execute(
                "SELECT * FROM esp_social_comments WHERE id=? AND user_id=?",
                (comment_id, user_id),
            ).fetchone()
        if not row:
            raise KeyError(comment_id)
        item = dict(row)
        item["resolved"] = bool(item["resolved"])
        return item

    @staticmethod
    def calendar(house) -> list[dict]:
        rows: list[dict] = []
        for content in house.content:
            for variant in content.variants:
                if not variant.scheduled_at:
                    continue
                rows.append(
                    {
                        "content_id": content.id,
                        "title": content.title,
                        "status": content.status,
                        "platform": variant.platform,
                        "content_type": variant.content_type,
                        "scheduled_at": variant.scheduled_at,
                        "timezone": variant.timezone,
                        "auto_publish": variant.auto_publish,
                        "publish_state": variant.publish_state,
                    }
                )
        return sorted(rows, key=lambda row: row["scheduled_at"])

    def aura_insights(self, user_id: str, space_id: str, niche_profile: dict | None = None) -> dict:
        snapshots = self.list_analytics(user_id, space_id, limit=100)
        recommendations: list[str] = []
        numeric: dict[str, list[float]] = {}
        for snapshot in snapshots:
            for key, value in snapshot.get("metrics", {}).items():
                if isinstance(value, (int, float)):
                    numeric.setdefault(key.lower(), []).append(float(value))

        def average(*keys: str) -> float | None:
            values: list[float] = []
            for key in keys:
                values.extend(numeric.get(key, []))
            return sum(values) / len(values) if values else None

        completion = average("completion_rate", "completion_percent", "video_completion_rate")
        retention = average("retention_rate", "retention_percent")
        shares = average("shares")
        saves = average("saves")
        comments = average("comments")
        followers = average("new_followers", "followers_gained")

        if completion is not None and completion < 30:
            recommendations.append("Short-form completion is weak: tighten the opening hook and move the payoff earlier.")
        elif completion is not None and completion >= 60:
            recommendations.append("Completion is strong: turn the winning format into a repeatable series rather than changing the structure immediately.")
        if retention is not None and retention < 25:
            recommendations.append("Retention needs attention: remove slow setup and create a clear reason to stay within the first moments.")
        if shares is not None and shares <= 1:
            recommendations.append("Build a stronger share trigger: make the post useful, surprising, relatable or worth sending to one specific person.")
        if saves is not None and saves <= 1:
            recommendations.append("Increase save value with a checklist, tip sequence, reference point or repeatable takeaway.")
        if comments is not None and comments <= 1:
            recommendations.append("Use one natural question or decision point that gives the audience an easy reason to comment.")
        if followers is not None and followers <= 0:
            recommendations.append("Views are not converting into followers yet: make the account promise and recurring series clearer in the content itself.")

        niche = (niche_profile or {}).get("catalog") or {}
        training = niche.get("training") or []
        if training:
            recommendations.append(f"Niche priority: {training[0]}")
        if not snapshots:
            recommendations.append("Add recent platform analytics or creator progress data so Aura can compare results instead of relying only on general guidance.")

        return {
            "snapshot_count": len(snapshots),
            "platforms": sorted({row.get("platform") for row in snapshots if row.get("platform")}),
            "averages": {key: round(sum(values) / len(values), 3) for key, values in numeric.items() if values},
            "recommendations": recommendations[:12],
        }
