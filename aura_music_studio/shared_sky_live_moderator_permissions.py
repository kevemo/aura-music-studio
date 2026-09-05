from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import shared_sky_live_community as live
from .owner_identity import owner_session_authorized


router = APIRouter(tags=["Shared Sky LIVE Moderator Permissions"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ModeratorPermissionRequest(BaseModel):
    enabled: bool
    reason: str = Field(min_length=3, max_length=500)


class LiveModeratorAssignmentRequest(BaseModel):
    assigned: bool
    reason: str = Field(min_length=3, max_length=500)


class ModeratorPermissionService:
    """Independent Moderator permission dimension for Shared Sky LIVE.

    Agent, Creator, Admin and commercial-plan state do not imply Moderator authority. Owners enable
    or revoke the global Moderator permission separately. A non-owner/non-creator moderator can act
    on a specific LIVE only when both the global permission and a LIVE assignment are present.
    """

    def __init__(self, community_store: Any):
        self.community = community_store

    def ensure_schema(self) -> None:
        with self.community._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS shared_sky_moderator_permissions (
                    user_id TEXT PRIMARY KEY,
                    enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
                    granted_by TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_shared_sky_moderator_permissions_enabled
                    ON shared_sky_moderator_permissions(enabled,user_id);

                CREATE TABLE IF NOT EXISTS shared_sky_moderator_permission_audit (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
                    actor_user_id TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_shared_sky_moderator_permission_audit_user
                    ON shared_sky_moderator_permission_audit(user_id,created_at DESC);

                CREATE TABLE IF NOT EXISTS shared_sky_live_moderator_assignment_audit (
                    id TEXT PRIMARY KEY,
                    broadcast_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    assigned INTEGER NOT NULL CHECK(assigned IN (0,1)),
                    actor_user_id TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(broadcast_id) REFERENCES shared_sky_broadcasts(id) ON DELETE CASCADE,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_shared_sky_live_moderator_assignment_audit
                    ON shared_sky_live_moderator_assignment_audit(broadcast_id,created_at DESC);
                """
            )

    def _user_exists(self, user_id: str) -> bool:
        with self.community._connect() as con:
            return bool(con.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone())

    def is_enabled(self, user_id: str) -> bool:
        self.ensure_schema()
        with self.community._connect() as con:
            row = con.execute(
                "SELECT enabled FROM shared_sky_moderator_permissions WHERE user_id=?",
                (user_id,),
            ).fetchone()
        return bool(row and row["enabled"])

    def is_live_assigned(self, broadcast_id: str, user_id: str) -> bool:
        with self.community._connect() as con:
            return bool(
                con.execute(
                    "SELECT 1 FROM shared_sky_live_moderators WHERE broadcast_id=? AND user_id=?",
                    (broadcast_id, user_id),
                ).fetchone()
            )

    def set_permission(self, user_id: str, enabled: bool, *, actor_user_id: str, reason: str) -> dict:
        self.ensure_schema()
        if not self._user_exists(user_id):
            raise KeyError(user_id)
        now = _now()
        with self.community._connect() as con:
            con.isolation_level = None
            con.execute("BEGIN IMMEDIATE")
            existing = con.execute(
                "SELECT * FROM shared_sky_moderator_permissions WHERE user_id=?",
                (user_id,),
            ).fetchone()
            created_at = str(existing["created_at"]) if existing else now
            con.execute(
                """INSERT INTO shared_sky_moderator_permissions
                   (user_id,enabled,granted_by,reason,created_at,updated_at)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(user_id) DO UPDATE SET
                     enabled=excluded.enabled,granted_by=excluded.granted_by,
                     reason=excluded.reason,updated_at=excluded.updated_at""",
                (user_id, int(enabled), actor_user_id, reason.strip(), created_at, now),
            )
            # Revocation also removes every session assignment. The runtime guard would already
            # deny stale rows, but deleting them prevents surprise reactivation on a later grant.
            if not enabled:
                con.execute("DELETE FROM shared_sky_live_moderators WHERE user_id=?", (user_id,))
            con.execute(
                """INSERT INTO shared_sky_moderator_permission_audit
                   (id,user_id,enabled,actor_user_id,reason,created_at) VALUES(?,?,?,?,?,?)""",
                (uuid4().hex, user_id, int(enabled), actor_user_id, reason.strip(), now),
            )
            con.execute("COMMIT")
        return self.permission(user_id)

    def permission(self, user_id: str) -> dict:
        self.ensure_schema()
        with self.community._connect() as con:
            row = con.execute(
                "SELECT * FROM shared_sky_moderator_permissions WHERE user_id=?",
                (user_id,),
            ).fetchone()
        if not row:
            return {"user_id": user_id, "enabled": False, "configured": False}
        item = dict(row)
        item["enabled"] = bool(item["enabled"])
        item["configured"] = True
        return item

    def list_permissions(self, *, enabled_only: bool = False) -> list[dict]:
        self.ensure_schema()
        sql = "SELECT * FROM shared_sky_moderator_permissions"
        if enabled_only:
            sql += " WHERE enabled=1"
        sql += " ORDER BY updated_at DESC,user_id"
        with self.community._connect() as con:
            rows = con.execute(sql).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["enabled"] = bool(item["enabled"])
            result.append(item)
        return result

    def set_live_assignment(
        self,
        broadcast_id: str,
        user_id: str,
        assigned: bool,
        *,
        actor_user_id: str,
        owner: bool,
        reason: str,
    ) -> dict:
        self.ensure_schema()
        broadcast = self.community._broadcast(broadcast_id)
        if not owner and str(broadcast["user_id"]) != actor_user_id:
            raise PermissionError("Only the LIVE creator or an Owner can assign LIVE moderators")
        if not self._user_exists(user_id):
            raise KeyError(user_id)
        now = _now()
        with self.community._connect() as con:
            con.isolation_level = None
            con.execute("BEGIN IMMEDIATE")
            if assigned:
                permission = con.execute(
                    "SELECT enabled FROM shared_sky_moderator_permissions WHERE user_id=?",
                    (user_id,),
                ).fetchone()
                if not permission or not bool(permission["enabled"]):
                    con.execute("ROLLBACK")
                    raise PermissionError(
                        "Owner-enabled Moderator permission is required before LIVE assignment"
                    )
                con.execute(
                    """INSERT INTO shared_sky_live_moderators(broadcast_id,user_id,granted_by,created_at)
                       VALUES(?,?,?,?)
                       ON CONFLICT(broadcast_id,user_id) DO UPDATE SET granted_by=excluded.granted_by""",
                    (broadcast_id, user_id, actor_user_id, now),
                )
            else:
                con.execute(
                    "DELETE FROM shared_sky_live_moderators WHERE broadcast_id=? AND user_id=?",
                    (broadcast_id, user_id),
                )
            con.execute(
                """INSERT INTO shared_sky_live_moderator_assignment_audit
                   (id,broadcast_id,user_id,assigned,actor_user_id,reason,created_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (uuid4().hex, broadcast_id, user_id, int(assigned), actor_user_id, reason.strip(), now),
            )
            con.execute("COMMIT")
        return {
            "broadcast_id": broadcast_id,
            "user_id": user_id,
            "assigned": assigned,
            "global_moderator_enabled": self.is_enabled(user_id),
        }

    def list_live_assignments(self, broadcast_id: str, *, actor_user_id: str, owner: bool) -> list[dict]:
        broadcast = self.community._broadcast(broadcast_id)
        if not owner and str(broadcast["user_id"]) != actor_user_id:
            raise PermissionError("Only the LIVE creator or an Owner can view LIVE moderator assignments")
        self.ensure_schema()
        with self.community._connect() as con:
            rows = con.execute(
                """SELECT m.broadcast_id,m.user_id,m.granted_by,m.created_at,
                          COALESCE(p.enabled,0) AS global_enabled
                   FROM shared_sky_live_moderators m
                   LEFT JOIN shared_sky_moderator_permissions p ON p.user_id=m.user_id
                   WHERE m.broadcast_id=? ORDER BY m.created_at,m.user_id""",
                (broadcast_id,),
            ).fetchall()
        return [
            {
                **dict(row),
                "global_enabled": bool(row["global_enabled"]),
                "effective": bool(row["global_enabled"]),
            }
            for row in rows
        ]


def _strict_moderator_allowed(self: Any, broadcast_id: str, user_id: str | None, owner: bool = False) -> bool:
    if owner:
        return True
    if not user_id:
        return False
    broadcast = self._broadcast(broadcast_id)
    if str(broadcast["user_id"]) == user_id:
        return True
    permissions = ModeratorPermissionService(self)
    return bool(
        permissions.is_enabled(user_id)
        and permissions.is_live_assigned(broadcast_id, user_id)
    )


_INSTALLED = False


def install_shared_sky_moderator_permissions() -> None:
    global _INSTALLED
    ModeratorPermissionService(live.community).ensure_schema()
    if _INSTALLED:
        return
    live.LiveCommunityStore.moderator_allowed = _strict_moderator_allowed
    _INSTALLED = True


def _owner_actor(request: Request) -> str:
    member = getattr(request.state, "member", None)
    return str(getattr(member, "user_id", "owner") or "owner")


def _member_actor(request: Request) -> str:
    member = live.require_member(request)
    return str(member.user_id)


@router.get("/owner/shared-sky/live/api/moderator-permissions")
def owner_list_moderator_permissions(request: Request, enabled_only: bool = False):
    if not owner_session_authorized(request):
        raise HTTPException(401, "Owner authentication required")
    return {
        "moderators": ModeratorPermissionService(live.community).list_permissions(enabled_only=enabled_only),
        "agent_role_alone_grants_moderation": False,
    }


@router.put("/owner/shared-sky/live/api/moderator-permissions/{user_id}")
def owner_set_moderator_permission(user_id: str, body: ModeratorPermissionRequest, request: Request):
    if not owner_session_authorized(request):
        raise HTTPException(401, "Owner authentication required")
    try:
        return ModeratorPermissionService(live.community).set_permission(
            user_id,
            body.enabled,
            actor_user_id=_owner_actor(request),
            reason=body.reason,
        )
    except KeyError as exc:
        raise HTTPException(404, "User not found") from exc


@router.get("/shared-sky/live/api/watch/{broadcast_id}/moderators")
def list_live_moderators(broadcast_id: str, request: Request):
    actor_user_id = _member_actor(request)
    try:
        rows = ModeratorPermissionService(live.community).list_live_assignments(
            broadcast_id,
            actor_user_id=actor_user_id,
            owner=owner_session_authorized(request),
        )
    except KeyError as exc:
        raise HTTPException(404, "Shared Sky LIVE not found") from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    return {"broadcast_id": broadcast_id, "moderators": rows}


@router.put("/shared-sky/live/api/watch/{broadcast_id}/moderators/{user_id}")
def set_live_moderator(
    broadcast_id: str,
    user_id: str,
    body: LiveModeratorAssignmentRequest,
    request: Request,
):
    actor_user_id = _member_actor(request)
    try:
        return ModeratorPermissionService(live.community).set_live_assignment(
            broadcast_id,
            user_id,
            body.assigned,
            actor_user_id=actor_user_id,
            owner=owner_session_authorized(request),
            reason=body.reason,
        )
    except KeyError as exc:
        raise HTTPException(404, "User or Shared Sky LIVE not found") from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


__all__ = [
    "router",
    "ModeratorPermissionService",
    "ModeratorPermissionRequest",
    "LiveModeratorAssignmentRequest",
    "install_shared_sky_moderator_permissions",
]
