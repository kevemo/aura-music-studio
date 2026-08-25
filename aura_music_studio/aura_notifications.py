from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .aura_chat_store import AuraChatStore

router = APIRouter(tags=["Aura Notifications"])
store = AuraChatStore()


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class NotificationStore:
    def __init__(self, chat_store: AuraChatStore | None = None):
        self.chat_store = chat_store or store
        self.ensure_schema()

    def ensure_schema(self) -> None:
        with self.chat_store._connect() as con:
            con.execute(
                """CREATE TABLE IF NOT EXISTS aura_notifications (
                       id TEXT PRIMARY KEY,
                       user_id TEXT NOT NULL,
                       thread_id TEXT,
                       kind TEXT NOT NULL,
                       title TEXT NOT NULL,
                       body TEXT NOT NULL,
                       resource_kind TEXT,
                       resource_id TEXT,
                       created_at TEXT NOT NULL,
                       read_at TEXT,
                       FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                   )"""
            )
            con.execute("CREATE INDEX IF NOT EXISTS idx_aura_notifications_user_created ON aura_notifications(user_id,created_at DESC)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_aura_notifications_user_read ON aura_notifications(user_id,read_at)")

    @staticmethod
    def _public(row) -> dict:
        return {
            "id": row["id"],
            "thread_id": row["thread_id"],
            "kind": row["kind"],
            "title": row["title"],
            "body": row["body"],
            "resource_kind": row["resource_kind"],
            "resource_id": row["resource_id"],
            "created_at": row["created_at"],
            "read_at": row["read_at"],
            "unread": row["read_at"] is None,
        }

    def create(
        self,
        user_id: str,
        *,
        kind: str,
        title: str,
        body: str,
        thread_id: str | None = None,
        resource_kind: str | None = None,
        resource_id: str | None = None,
    ) -> dict:
        if thread_id and not self.chat_store.thread(user_id, thread_id):
            raise KeyError("Aura conversation not found")
        notification_id = uuid4().hex
        now = _iso_now()
        clean_kind = (kind or "general").strip().lower()[:60] or "general"
        clean_title = " ".join((title or "Aura notification").split())[:200] or "Aura notification"
        clean_body = str(body or "").strip()[:5000]
        with self.chat_store._connect() as con:
            con.execute(
                """INSERT INTO aura_notifications(id,user_id,thread_id,kind,title,body,resource_kind,resource_id,created_at,read_at)
                   VALUES (?,?,?,?,?,?,?,?,?,NULL)""",
                (
                    notification_id,
                    user_id,
                    thread_id,
                    clean_kind,
                    clean_title,
                    clean_body,
                    (resource_kind or "")[:80] or None,
                    (resource_id or "")[:200] or None,
                    now,
                ),
            )
            row = con.execute("SELECT * FROM aura_notifications WHERE id=?", (notification_id,)).fetchone()
        return self._public(row)

    def list(self, user_id: str, *, unread_only: bool = False, limit: int = 100) -> list[dict]:
        maximum = max(1, min(int(limit), 300))
        query = "SELECT * FROM aura_notifications WHERE user_id=?"
        args: list = [user_id]
        if unread_only:
            query += " AND read_at IS NULL"
        query += " ORDER BY created_at DESC LIMIT ?"
        args.append(maximum)
        with self.chat_store._connect() as con:
            rows = con.execute(query, tuple(args)).fetchall()
        return [self._public(row) for row in rows]

    def unread_count(self, user_id: str) -> int:
        with self.chat_store._connect() as con:
            row = con.execute("SELECT COUNT(*) AS n FROM aura_notifications WHERE user_id=? AND read_at IS NULL", (user_id,)).fetchone()
        return int(row["n"] or 0)

    def mark_read(self, user_id: str, notification_id: str, *, read: bool = True) -> dict:
        value = _iso_now() if read else None
        with self.chat_store._connect() as con:
            cur = con.execute("UPDATE aura_notifications SET read_at=? WHERE id=? AND user_id=?", (value, notification_id, user_id))
            if cur.rowcount < 1:
                raise KeyError(notification_id)
            row = con.execute("SELECT * FROM aura_notifications WHERE id=? AND user_id=?", (notification_id, user_id)).fetchone()
        return self._public(row)

    def mark_all_read(self, user_id: str) -> int:
        now = _iso_now()
        with self.chat_store._connect() as con:
            cur = con.execute("UPDATE aura_notifications SET read_at=? WHERE user_id=? AND read_at IS NULL", (now, user_id))
        return int(cur.rowcount)

    def delete(self, user_id: str, notification_id: str) -> bool:
        with self.chat_store._connect() as con:
            cur = con.execute("DELETE FROM aura_notifications WHERE id=? AND user_id=?", (notification_id, user_id))
        return cur.rowcount > 0


notification_store = NotificationStore(store)


class ReadRequest(BaseModel):
    read: bool = True


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    return member


@router.get("/aura-intelligence/api/notifications")
def list_notifications(request: Request, unread_only: bool = False, limit: int = 100):
    member = _member(request)
    return {
        "notifications": notification_store.list(member.user_id, unread_only=unread_only, limit=limit),
        "unread_count": notification_store.unread_count(member.user_id),
    }


@router.patch("/aura-intelligence/api/notifications/{notification_id}")
def mark_notification(notification_id: str, body: ReadRequest, request: Request):
    member = _member(request)
    try:
        return notification_store.mark_read(member.user_id, notification_id, read=body.read)
    except KeyError as exc:
        raise HTTPException(404, "Aura notification not found") from exc


@router.post("/aura-intelligence/api/notifications/read-all")
def mark_all_notifications_read(request: Request):
    member = _member(request)
    return {"updated": notification_store.mark_all_read(member.user_id), "unread_count": 0}


@router.delete("/aura-intelligence/api/notifications/{notification_id}")
def delete_notification(notification_id: str, request: Request):
    member = _member(request)
    if not notification_store.delete(member.user_id, notification_id):
        raise HTTPException(404, "Aura notification not found")
    return {"deleted": True, "notification_id": notification_id}


__all__ = ["router", "NotificationStore", "notification_store"]
