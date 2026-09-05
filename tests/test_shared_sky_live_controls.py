from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from aura_music_studio.accounts import AccountStore
from aura_music_studio.shared_sky_live_community import LiveCommunityStore, PollCreateRequest
import aura_music_studio.shared_sky_live_community as live_module
import aura_music_studio.shared_sky_live_controls as controls


class ReadyPlayback:
    def descriptor(self, broadcast_id: str, viewer_user_id: str | None) -> dict:
        return {"available": True, "state": "ready", "manifest_url": "https://media.example.invalid/live.m3u8", "renditions": [], "captions": [], "dvr": False}

    def replay(self, broadcast_id: str, viewer_user_id: str | None) -> dict:
        return {"available": True, "state": "ready", "url": "https://media.example.invalid/replay.m3u8"}


def _request(user_id: str) -> Request:
    request = Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 443),
        }
    )
    request.state.member = SimpleNamespace(user_id=user_id)
    return request


def _user(con: sqlite3.Connection, user_id: str, display: str) -> None:
    con.execute(
        """INSERT INTO users
           (id,email,display_name,password_salt,password_hash,status,plan_id,requested_plan_id,billing_status,created_at)
           VALUES(?,?,?,?,?,'active','free','free','not_required',?)""",
        (user_id, f"{user_id}@example.invalid", display, "00", "00", datetime.now(timezone.utc).isoformat()),
    )


@pytest.fixture()
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> LiveCommunityStore:
    db = tmp_path / "controls.sqlite3"
    AccountStore(db)
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(db) as con:
        _user(con, "creator-1", "Creator One")
        _user(con, "viewer-1", "Viewer One")
        con.executescript(
            """
            CREATE TABLE shared_sky_broadcasts (
                id TEXT PRIMARY KEY,user_id TEXT NOT NULL,project_id TEXT,title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',state TEXT NOT NULL,destination_ids_json TEXT NOT NULL DEFAULT '[]',
                passthrough INTEGER NOT NULL DEFAULT 1,started_at TEXT,ended_at TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            """
        )
        con.execute(
            """INSERT INTO shared_sky_broadcasts
               (id,user_id,project_id,title,description,state,started_at,created_at,updated_at)
               VALUES('live-1','creator-1','project-1','Control test','', 'live',?,?,?)""",
            (now, now, now),
        )
    value = LiveCommunityStore(db)
    monkeypatch.setattr(live_module, "_playback_adapter", ReadyPlayback())
    monkeypatch.setattr(controls, "community", value)
    controls._ensure_schema()
    value.reconcile()
    return value


def test_poll_can_be_closed_idempotently_by_creator(store: LiveCommunityStore):
    poll = store.create_poll(
        "live-1",
        "creator-1",
        PollCreateRequest(question="Finish this poll?", options=["Yes", "No"], idempotency_key="make-poll-1"),
    )
    request = _request("creator-1")
    body = controls.PollStateRequest(state="ended", idempotency_key="close-poll-1")
    first = controls.set_poll_state(poll["id"], body, request)
    second = controls.set_poll_state(poll["id"], body, request)
    assert first == second
    assert first["state"] == "ended"
    assert store.poll(poll["id"])["state"] == "ended"


def test_viewer_cannot_close_creator_poll(store: LiveCommunityStore):
    poll = store.create_poll(
        "live-1",
        "creator-1",
        PollCreateRequest(question="Protected?", options=["Yes", "No"], idempotency_key="make-poll-2"),
    )
    with pytest.raises(Exception) as exc:
        controls.set_poll_state(
            poll["id"],
            controls.PollStateRequest(state="cancelled", idempotency_key="bad-close-1"),
            _request("viewer-1"),
        )
    assert getattr(exc.value, "status_code", None) == 403


def test_live_safety_preferences_persist_per_user(store: LiveCommunityStore):
    request = _request("viewer-1")
    updated = controls.update_live_preferences(
        controls.LivePreferenceRequest(reduced_motion=True, hide_reactions=True, hide_gift_animations=True, profanity_filter=True),
        request,
    )
    assert updated["reduced_motion"] is True
    assert updated["hide_reactions"] is True
    assert updated["hide_gift_animations"] is True
    assert updated["profanity_filter"] is True
    assert controls.get_live_preferences(request) == updated


def test_keyword_filter_configuration_is_moderator_only_and_idempotent(store: LiveCommunityStore):
    creator = _request("creator-1")
    body = controls.KeywordFilterRequest(terms=[" Spam ", "spam", "unsafe phrase"], idempotency_key="keywords-1")
    first = controls.set_keyword_filters("live-1", body, creator)
    second = controls.set_keyword_filters("live-1", body, creator)
    assert first == second
    assert first == {"terms": ["Spam", "unsafe phrase"], "enforcement": "configuration_hook"}

    with pytest.raises(Exception) as exc:
        controls.get_keyword_filters("live-1", _request("viewer-1"))
    assert getattr(exc.value, "status_code", None) == 403


def test_watch_history_filters_access_and_supports_replay_progress_delete(store: LiveCommunityStore):
    viewer = _request("viewer-1")
    store.join_presence("live-1", viewer, "viewer-1")
    assert len(controls.list_watch_history(viewer)["history"]) == 1

    with store._connect() as con:
        ended = datetime.now(timezone.utc).isoformat()
        con.execute("UPDATE shared_sky_broadcasts SET state='ended',ended_at=?,updated_at=? WHERE id='live-1'", (ended, ended))

    progress = controls.update_replay_progress(
        "live-1", controls.ReplayProgressRequest(progress_seconds=42.5), viewer
    )
    assert progress["replay_progress_seconds"] == 42.5
    assert controls.delete_watch_history_item("live-1", viewer)["deleted"] is True
    assert controls.list_watch_history(viewer)["history"] == []
