from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from starlette.requests import Request

from aura_music_studio.accounts import AccountStore
from aura_music_studio.shared_sky_live_community import (
    ChatSendRequest,
    FollowRequest,
    LiveCommunityStore,
    LiveMetadataPatch,
    ModerationRequest,
    PollCreateRequest,
    PollVoteRequest,
    QARequest,
    QAModerationRequest,
    ReactionRequest,
    ShareRequest,
)
import aura_music_studio.shared_sky_live_community as live_module


class ReadyPlayback:
    def descriptor(self, broadcast_id: str, viewer_user_id: str | None) -> dict:
        return {
            "available": True,
            "state": "ready",
            "broadcast_id": broadcast_id,
            "manifest_url": f"https://media.example.invalid/{broadcast_id}.m3u8",
            "token_expires_at": None,
            "renditions": [{"id": "auto", "label": "Auto"}],
            "captions": [],
            "dvr": False,
        }

    def replay(self, broadcast_id: str, viewer_user_id: str | None) -> dict:
        return {"available": False, "state": "processing", "broadcast_id": broadcast_id}


class UnavailablePlayback:
    def descriptor(self, broadcast_id: str, viewer_user_id: str | None) -> dict:
        return {"available": False, "state": "unavailable", "reason": "not_ready", "manifest_url": None}

    def replay(self, broadcast_id: str, viewer_user_id: str | None) -> dict:
        return {"available": False, "state": "unavailable"}


class FakeNotifications:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def create(self, user_id: str, **kwargs):
        self.calls.append((user_id, kwargs))
        return {"id": "notice"}


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "https",
            "path": "/watch/live-1",
            "raw_path": b"/watch/live-1",
            "query_string": b"",
            "headers": [(b"user-agent", b"pytest-shared-sky")],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 443),
        }
    )


def _insert_user(con: sqlite3.Connection, user_id: str, name: str) -> None:
    con.execute(
        """INSERT INTO users
           (id,email,display_name,password_salt,password_hash,status,plan_id,requested_plan_id,billing_status,created_at)
           VALUES(?,?,?,?,?,'active','free','free','not_required',?)""",
        (user_id, f"{user_id}@example.invalid", name, "00", "00", datetime.now(timezone.utc).isoformat()),
    )


@pytest.fixture()
def live_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> LiveCommunityStore:
    db = tmp_path / "shared-sky-live.sqlite3"
    AccountStore(db)
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(db) as con:
        _insert_user(con, "creator-1", "Creator One")
        _insert_user(con, "viewer-1", "Viewer One")
        _insert_user(con, "viewer-2", "Viewer Two")
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
               VALUES('live-1','creator-1','project-1','Making a song','A real Shared Sky session','live',?,?,?)""",
            (now, now, now),
        )
    store = LiveCommunityStore(db)
    monkeypatch.setattr(live_module, "_playback_adapter", ReadyPlayback())
    return store


def test_authoritative_directory_reconciles_and_stale_live_cannot_survive(live_store: LiveCommunityStore):
    rows = live_store.directory(None)
    assert [row["id"] for row in rows] == ["live-1"]
    assert rows[0]["viewer_count"] == 0
    assert rows[0]["rank_model"] == "deterministic_follow_plus_freshness_v1"

    with live_store._connect() as con:
        con.execute(
            "UPDATE shared_sky_broadcasts SET state='ended',ended_at=?,updated_at=? WHERE id='live-1'",
            (datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat()),
        )

    assert live_store.directory(None) == []
    reconciled = live_store.reconcile()
    assert reconciled["stale_not_discoverable"] == 1


def test_live_directory_fails_closed_when_playback_contract_is_not_ready(live_store: LiveCommunityStore, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(live_module, "_playback_adapter", UnavailablePlayback())
    assert live_store.directory(None) == []


def test_search_filter_and_following_ranking_are_deterministic(live_store: LiveCommunityStore):
    live_store.reconcile()
    live_store.update_metadata(
        "live-1",
        "creator-1",
        LiveMetadataPatch(category="music", tags=["original", "guitar"], language="en", expected_version=1),
    )
    assert len(live_store.directory(None, q="guitar", category="music")) == 1
    assert live_store.directory(None, q="speedrun") == []

    live_store.follow("viewer-1", "creator-1", True, True)
    rows = live_store.directory("viewer-1", following_only=True)
    assert len(rows) == 1
    assert rows[0]["following"] is True
    assert rows[0]["rank_score"] >= 100


def test_followers_visibility_and_creator_block_are_server_authoritative(live_store: LiveCommunityStore):
    live_store.reconcile()
    live_store.update_metadata(
        "live-1",
        "creator-1",
        LiveMetadataPatch(visibility="followers", expected_version=1),
    )
    assert live_store.access("live-1", "viewer-1", direct=True).allowed is False
    live_store.follow("viewer-1", "creator-1", True, True)
    assert live_store.access("live-1", "viewer-1", direct=True).allowed is True

    live_store.moderate(
        "live-1",
        "creator-1",
        False,
        ModerationRequest(
            action="block_user",
            target_user_id="viewer-1",
            reason="creator safety choice",
            idempotency_key="block-viewer-1",
        ),
    )
    assert live_store.access("live-1", "viewer-1", direct=True).allowed is False
    assert live_store.is_following("viewer-1", "creator-1") is False


def test_presence_count_is_server_lease_based_and_authenticated_tabs_dedupe(live_store: LiveCommunityStore):
    live_store.reconcile()
    request = _request()
    first = live_store.join_presence("live-1", request, "viewer-1")
    second = live_store.join_presence("live-1", request, "viewer-1")

    assert first["presence_id"] == second["presence_id"]
    assert live_store.presence_count("live-1") == 1
    assert first["count_definition"] == "current unexpired unique Shared Sky presence leases"

    heartbeat = live_store.heartbeat("live-1", second["presence_id"], second["resume_token"])
    assert heartbeat["viewer_count"] == 1

    with live_store._connect() as con:
        con.execute(
            "UPDATE shared_sky_presence SET expires_at=? WHERE id=?",
            ((datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat(), second["presence_id"]),
        )
    assert live_store.presence_count("live-1") == 0


def test_anonymous_presence_uses_opaque_resume_token_and_not_client_count(live_store: LiveCommunityStore):
    live_store.reconcile()
    request = _request()
    first = live_store.join_presence("live-1", request, None)
    resumed = live_store.join_presence("live-1", request, None, first["resume_token"])
    assert resumed["presence_id"] == first["presence_id"]
    assert len(first["resume_token"]) >= 32
    assert live_store.presence_count("live-1") == 1

    with live_store._connect() as con:
        stored = con.execute("SELECT token_hash FROM shared_sky_presence WHERE id=?", (first["presence_id"],)).fetchone()[0]
    assert stored != first["resume_token"]


def test_realtime_sequence_and_idempotency_deduplicate_replays(live_store: LiveCommunityStore):
    live_store.reconcile()
    one = live_store.emit("live-1", "viewer-1", "chat.notice", {"value": 1}, idempotency_key="same-event")
    duplicate = live_store.emit("live-1", "viewer-1", "chat.notice", {"value": 999}, idempotency_key="same-event")
    two = live_store.emit("live-1", "viewer-1", "chat.notice", {"value": 2}, idempotency_key="next-event")

    assert duplicate["seq"] == one["seq"]
    assert duplicate["payload"] == {"value": 1}
    assert two["seq"] > one["seq"]
    assert [event["seq"] for event in live_store.events_after("live-1", one["seq"])] == [two["seq"]]


def test_chat_is_durable_idempotent_control_safe_and_link_bounded(live_store: LiveCommunityStore):
    live_store.reconcile()
    body = ChatSendRequest(
        message="<script>alert('x')</script>\x00 hello https://example.invalid/path javascript:alert(1)",
        idempotency_key="chat-retry-0001",
    )
    first = live_store.send_chat("live-1", "viewer-1", body)
    second = live_store.send_chat("live-1", "viewer-1", body)

    assert first["id"] == second["id"]
    assert "\x00" not in first["body"]
    assert first["links"] == ["https://example.invalid/path"]
    assert len(live_store.chat_history("live-1")) == 1


def test_chat_timeout_blocks_sending_and_viewer_cannot_moderate(live_store: LiveCommunityStore):
    live_store.reconcile()
    with pytest.raises(PermissionError):
        live_store.moderate(
            "live-1",
            "viewer-1",
            False,
            ModerationRequest(action="set_chat_enabled", value=False, idempotency_key="viewer-bypass-1"),
        )

    live_store.moderate(
        "live-1",
        "creator-1",
        False,
        ModerationRequest(
            action="timeout_user",
            target_user_id="viewer-1",
            duration_seconds=300,
            reason="flooding",
            idempotency_key="timeout-1",
        ),
    )
    with pytest.raises(PermissionError):
        live_store.send_chat(
            "live-1",
            "viewer-1",
            ChatSendRequest(message="blocked", idempotency_key="chat-after-timeout"),
        )


def test_chat_settings_use_optimistic_concurrency(live_store: LiveCommunityStore):
    live_store.reconcile()
    initial = live_store.chat_settings("live-1")
    live_store.moderate(
        "live-1",
        "creator-1",
        False,
        ModerationRequest(
            action="set_slow_mode",
            value=15,
            expected_version=initial["version"],
            idempotency_key="slow-mode-1",
        ),
    )
    with pytest.raises(RuntimeError):
        live_store.moderate(
            "live-1",
            "creator-1",
            False,
            ModerationRequest(
                action="set_chat_enabled",
                value=False,
                expected_version=initial["version"],
                idempotency_key="stale-setting-1",
            ),
        )


def test_reaction_retry_does_not_double_count_and_share_is_only_intent(live_store: LiveCommunityStore):
    live_store.reconcile()
    reaction = ReactionRequest(reaction="like", amount=2, idempotency_key="reaction-retry-1")
    first = live_store.react("live-1", "user:viewer-1", "viewer-1", reaction)
    second = live_store.react("live-1", "user:viewer-1", "viewer-1", reaction)
    assert first == {"accepted": True, "reaction": "like", "total": 2}
    assert second == {"accepted": False, "reaction": "like", "total": 2}

    share = ShareRequest(action="share_sheet_open", destination="social", idempotency_key="share-retry-1")
    result = live_store.share("live-1", "user:viewer-1", "viewer-1", share)
    assert result["external_post_confirmed"] is False
    assert result["analytics_semantics"] == "intent_or_share_surface_open_only"


def test_poll_vote_is_server_counted_and_idempotent(live_store: LiveCommunityStore):
    live_store.reconcile()
    poll = live_store.create_poll(
        "live-1",
        "creator-1",
        PollCreateRequest(
            question="Which song next?",
            options=["Original", "Acoustic"],
            idempotency_key="poll-create-1",
        ),
    )
    option_id = poll["options"][0]["id"]
    vote = PollVoteRequest(option_ids=[option_id], idempotency_key="poll-vote-1")
    first = live_store.vote_poll(poll["id"], "user:viewer-1", "viewer-1", vote)
    second = live_store.vote_poll(poll["id"], "user:viewer-1", "viewer-1", vote)
    assert first["options"][0]["votes"] == 1
    assert second["options"][0]["votes"] == 1


def test_qa_state_is_moderator_authoritative(live_store: LiveCommunityStore):
    live_store.reconcile()
    question = live_store.submit_qa(
        "live-1",
        "viewer-1",
        QARequest(question="How did you build that harmony?", idempotency_key="qa-submit-1"),
    )
    assert question["state"] == "pending"

    with pytest.raises(PermissionError):
        live_store.moderate_qa(
            "live-1",
            question["id"],
            "viewer-1",
            QAModerationRequest(action="approve", idempotency_key="qa-bypass-1"),
        )

    approved = live_store.moderate_qa(
        "live-1",
        question["id"],
        "creator-1",
        QAModerationRequest(action="approve", idempotency_key="qa-approve-1"),
    )
    assert approved["state"] == "approved"


def test_live_notification_emission_is_deduplicated(live_store: LiveCommunityStore, monkeypatch: pytest.MonkeyPatch):
    fake = FakeNotifications()
    monkeypatch.setattr(live_module, "notification_store", fake)
    live_store.follow("viewer-1", "creator-1", True, True)
    live_store.reconcile()
    live_store.reconcile()
    assert len(fake.calls) == 1
    assert fake.calls[0][0] == "viewer-1"
    assert fake.calls[0][1]["resource_id"] == "live-1"


def test_watch_history_is_real_and_user_clearable(live_store: LiveCommunityStore):
    live_store.reconcile()
    live_store.join_presence("live-1", _request(), "viewer-1")
    with live_store._connect() as con:
        assert con.execute(
            "SELECT COUNT(*) FROM shared_sky_watch_history WHERE user_id='viewer-1' AND broadcast_id='live-1'"
        ).fetchone()[0] == 1
    assert live_store.clear_watch_history("viewer-1") == 1


def test_gift_and_battle_are_display_only_contracts(live_store: LiveCommunityStore):
    live_store.reconcile()
    detail = live_store.detail("live-1", _request(), "viewer-1")
    assert detail["gift_display"]["available"] is False
    assert detail["battle_display"]["available"] is False
    assert not hasattr(live_store, "send_gift")
    assert not hasattr(live_store, "create_battle")
    source = Path(live_module.__file__).read_text(encoding="utf-8")
    assert "from .credit_wallet" not in source


def test_public_route_bootstrap_keeps_write_authority_in_handlers():
    from fastapi import FastAPI
    from aura_music_studio import access_control
    from aura_music_studio.shared_sky_live_bootstrap import install_shared_sky_live_community

    app = FastAPI()
    install_shared_sky_live_community(app)
    paths = {getattr(route, "path", "") for route in app.routes}
    assert "/live-now" in paths
    assert "/watch/{broadcast_id}" in paths
    assert "/shared-sky/live/api/watch/{broadcast_id}/chat" in paths
    assert "/live-now" in access_control.PUBLIC_EXACT
    assert "/watch/" in access_control.PUBLIC_PREFIXES
    assert "/shared-sky/live/api/" in access_control.PUBLIC_PREFIXES


def test_follow_request_is_a_final_state_operation_not_a_counter(live_store: LiveCommunityStore):
    request = FollowRequest(following=True, notify_live=True, idempotency_key="follow-final-state-1")
    first = live_store.follow("viewer-1", "creator-1", request.following, request.notify_live)
    second = live_store.follow("viewer-1", "creator-1", request.following, request.notify_live)
    assert first["following"] is True
    assert second["following"] is True
    assert second["follower_count"] == 1
