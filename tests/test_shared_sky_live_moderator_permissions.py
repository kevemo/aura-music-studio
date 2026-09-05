from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.shared_sky_live_community import LiveCommunityStore
from aura_music_studio.shared_sky_live_moderator_permissions import (
    ModeratorPermissionService,
    _strict_moderator_allowed,
)


def _insert_user(con: sqlite3.Connection, user_id: str, name: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    con.execute(
        """INSERT INTO users
           (id,email,display_name,password_salt,password_hash,status,plan_id,requested_plan_id,billing_status,created_at)
           VALUES(?,?,?,?,?,'active','free','free','not_required',?)""",
        (user_id, f"{user_id}@example.invalid", name, "00", "00", now),
    )
    columns = {str(row[1]) for row in con.execute("PRAGMA table_info(users)").fetchall()}
    if {"esp_status", "esp_roles"}.issubset(columns) and user_id == "agent-1":
        con.execute(
            "UPDATE users SET esp_status='active',esp_roles='agent' WHERE id=?",
            (user_id,),
        )


@pytest.fixture()
def moderation_store(tmp_path: Path) -> tuple[LiveCommunityStore, ModeratorPermissionService]:
    db = tmp_path / "shared-sky-moderator-permissions.sqlite3"
    AccountStore(db)
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(db) as con:
        _insert_user(con, "creator-1", "Creator One")
        _insert_user(con, "agent-1", "Agent One")
        _insert_user(con, "viewer-1", "Viewer One")
        con.executescript(
            """
            CREATE TABLE shared_sky_broadcasts (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                project_id TEXT,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                state TEXT NOT NULL DEFAULT 'draft',
                destination_ids_json TEXT NOT NULL DEFAULT '[]',
                passthrough INTEGER NOT NULL DEFAULT 1,
                started_at TEXT,
                ended_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            """
        )
        con.execute(
            """INSERT INTO shared_sky_broadcasts
               (id,user_id,project_id,title,description,state,started_at,created_at,updated_at)
               VALUES('live-1','creator-1','project-1','Moderator test','', 'live',?,?,?)""",
            (now, now, now),
        )
    store = LiveCommunityStore(db)
    service = ModeratorPermissionService(store)
    service.ensure_schema()
    return store, service


def test_agent_or_legacy_live_assignment_alone_never_grants_moderation(moderation_store):
    store, service = moderation_store
    with store._connect() as con:
        con.execute(
            """INSERT INTO shared_sky_live_moderators(broadcast_id,user_id,granted_by,created_at)
               VALUES('live-1','agent-1','creator-1',?)""",
            (datetime.now(timezone.utc).isoformat(),),
        )

    assert service.is_live_assigned("live-1", "agent-1") is True
    assert service.is_enabled("agent-1") is False
    assert _strict_moderator_allowed(store, "live-1", "agent-1") is False


def test_global_moderator_grant_alone_does_not_grant_live_authority(moderation_store):
    store, service = moderation_store
    permission = service.set_permission(
        "agent-1",
        True,
        actor_user_id="owner-kev",
        reason="Approved for limited LIVE moderation",
    )
    assert permission["enabled"] is True
    assert permission["effective"] is True
    assert service.is_live_assigned("live-1", "agent-1") is False
    assert _strict_moderator_allowed(store, "live-1", "agent-1") is False


def test_live_assignment_requires_prior_owner_enabled_global_permission(moderation_store):
    _, service = moderation_store
    with pytest.raises(PermissionError, match="Owner-enabled Moderator permission"):
        service.set_live_assignment(
            "live-1",
            "agent-1",
            True,
            actor_user_id="creator-1",
            owner=False,
            reason="Help moderate tonight",
        )


def test_global_permission_plus_live_assignment_grants_limited_moderator_authority(moderation_store):
    store, service = moderation_store
    service.set_permission(
        "agent-1",
        True,
        actor_user_id="owner-kev",
        reason="Approved Moderator permission",
    )
    assigned = service.set_live_assignment(
        "live-1",
        "agent-1",
        True,
        actor_user_id="creator-1",
        owner=False,
        reason="Assigned to this LIVE",
    )

    assert assigned["assigned"] is True
    assert assigned["global_moderator_enabled"] is True
    assert _strict_moderator_allowed(store, "live-1", "agent-1") is True


def test_revoking_global_permission_removes_all_live_assignments_and_future_authority(moderation_store):
    store, service = moderation_store
    service.set_permission(
        "agent-1", True, actor_user_id="owner-mary", reason="Temporary Moderator approval"
    )
    service.set_live_assignment(
        "live-1",
        "agent-1",
        True,
        actor_user_id="creator-1",
        owner=False,
        reason="Moderate this session",
    )

    revoked = service.set_permission(
        "agent-1", False, actor_user_id="owner-mary", reason="Moderator access removed"
    )

    assert revoked["enabled"] is False
    assert revoked["effective"] is False
    assert service.is_live_assigned("live-1", "agent-1") is False
    assert _strict_moderator_allowed(store, "live-1", "agent-1") is False


def test_inactive_account_fails_closed_even_if_permission_record_is_enabled(moderation_store):
    store, service = moderation_store
    service.set_permission(
        "agent-1", True, actor_user_id="owner-kev", reason="Approved Moderator access"
    )
    service.set_live_assignment(
        "live-1",
        "agent-1",
        True,
        actor_user_id="creator-1",
        owner=False,
        reason="Assigned to LIVE",
    )
    with store._connect() as con:
        con.execute("UPDATE users SET status='disabled' WHERE id='agent-1'")

    assert service.is_enabled("agent-1") is False
    assert service.permission("agent-1")["effective"] is False
    assert _strict_moderator_allowed(store, "live-1", "agent-1") is False


def test_inactive_account_cannot_receive_new_global_moderator_grant(moderation_store):
    store, service = moderation_store
    with store._connect() as con:
        con.execute("UPDATE users SET status='disabled' WHERE id='viewer-1'")
    with pytest.raises(PermissionError, match="active account"):
        service.set_permission(
            "viewer-1",
            True,
            actor_user_id="owner-kev",
            reason="Would otherwise be approved",
        )


def test_only_live_creator_or_owner_can_assign_session_moderator(moderation_store):
    _, service = moderation_store
    service.set_permission(
        "agent-1", True, actor_user_id="owner-kev", reason="Approved Moderator access"
    )
    with pytest.raises(PermissionError, match="LIVE creator or an Owner"):
        service.set_live_assignment(
            "live-1",
            "agent-1",
            True,
            actor_user_id="viewer-1",
            owner=False,
            reason="Unauthorized assignment attempt",
        )

    owner_assignment = service.set_live_assignment(
        "live-1",
        "agent-1",
        True,
        actor_user_id="owner-kev",
        owner=True,
        reason="Owner assigned Moderator",
    )
    assert owner_assignment["assigned"] is True


def test_creator_and_owner_authority_are_preserved_without_moderator_rows(moderation_store):
    store, _ = moderation_store
    assert _strict_moderator_allowed(store, "live-1", "creator-1") is True
    assert _strict_moderator_allowed(store, "live-1", "viewer-1", owner=True) is True
    assert _strict_moderator_allowed(store, "live-1", None) is False


def test_permission_and_assignment_changes_are_audited(moderation_store):
    store, service = moderation_store
    service.set_permission(
        "agent-1", True, actor_user_id="owner-kev", reason="Approved Moderator access"
    )
    service.set_live_assignment(
        "live-1",
        "agent-1",
        True,
        actor_user_id="creator-1",
        owner=False,
        reason="Assigned to LIVE",
    )
    service.set_live_assignment(
        "live-1",
        "agent-1",
        False,
        actor_user_id="creator-1",
        owner=False,
        reason="Session moderation complete",
    )
    service.set_permission(
        "agent-1", False, actor_user_id="owner-kev", reason="Global Moderator access removed"
    )

    with store._connect() as con:
        permissions = con.execute(
            "SELECT enabled,actor_user_id,reason FROM shared_sky_moderator_permission_audit WHERE user_id='agent-1' ORDER BY created_at,id"
        ).fetchall()
        assignments = con.execute(
            "SELECT assigned,actor_user_id,reason FROM shared_sky_live_moderator_assignment_audit WHERE user_id='agent-1' ORDER BY created_at,id"
        ).fetchall()

    assert [int(row["enabled"]) for row in permissions] == [1, 0]
    assert [int(row["assigned"]) for row in assignments] == [1, 0]
    assert permissions[0]["actor_user_id"] == "owner-kev"
    assert assignments[0]["actor_user_id"] == "creator-1"


def test_service_rejects_unbounded_or_empty_reasons(moderation_store):
    _, service = moderation_store
    with pytest.raises(ValueError):
        service.set_permission("agent-1", True, actor_user_id="owner-kev", reason="  ")


def test_wave5_bootstrap_exposes_owner_and_live_assignment_routes():
    from aura_music_studio import shared_sky_live_bootstrap as bootstrap

    signatures = {
        (getattr(route, "path", ""), tuple(sorted(getattr(route, "methods", set()) or set())))
        for route in bootstrap._LIVE_MODERATOR_PERMISSION_ROUTES
    }
    assert (
        "/owner/shared-sky/live/api/moderator-permissions/{user_id}",
        ("PUT",),
    ) in signatures
    assert (
        "/shared-sky/live/api/watch/{broadcast_id}/moderators/{user_id}",
        ("PUT",),
    ) in signatures
