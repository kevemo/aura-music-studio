from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from starlette.requests import Request

import aura_music_studio.shared_sky_live_bootstrap as bootstrap
import aura_music_studio.shared_sky_live_watch_ui_v2 as watch


def _request(path: str = "/watch/live-1", *, headers: list[tuple[bytes, bytes]] | None = None, query: bytes = b"") -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "https",
            "path": path,
            "raw_path": path.encode(),
            "query_string": query,
            "headers": headers or [(b"user-agent", b"pytest-watch-v2")],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 443),
        }
    )


def test_browser_descriptor_accepts_safe_replay_and_nested_rendition_urls():
    result = watch._browser_descriptor(
        {
            "available": True,
            "state": "ready",
            "replay_url": "/shared-sky/replay/asset-1.mp4",
            "renditions": [
                {"name": "720p", "profile": {"manifest_url": "https://media.example/720.m3u8"}},
                {"name": "bad", "url": "javascript:alert(1)"},
            ],
            "captions": [
                {"url": "/captions/en.vtt", "language": "en", "label": "English"},
                {"url": "data:text/plain,bad", "language": "xx"},
            ],
            "dvr": False,
        }
    )

    assert result["available"] is True
    assert result["manifest_url"] == "/shared-sky/replay/asset-1.mp4"
    assert result["renditions"][0]["manifest_url"] == "https://media.example/720.m3u8"
    assert "manifest_url" not in result["renditions"][1]
    assert result["captions"] == [
        {"src": "/captions/en.vtt", "srclang": "en", "label": "English", "default": False}
    ]


def test_safe_url_rejects_credentials_protocol_relative_and_script_urls():
    assert watch._safe_url("/media/live.m3u8") == "/media/live.m3u8"
    assert watch._safe_url("https://media.example/live.m3u8") == "https://media.example/live.m3u8"
    assert watch._safe_url("https://user:secret@media.example/live.m3u8") == ""
    assert watch._safe_url("//media.example/live.m3u8") == ""
    assert watch._safe_url("javascript:alert(1)") == ""
    assert watch._safe_url("https://media.example/live.m3u8\nInjected: yes") == ""


def test_anonymous_interactives_presence_token_uses_header_not_query(monkeypatch):
    captured: list[str | None] = []

    monkeypatch.setattr(watch, "_access_or_raise", lambda broadcast_id, request: None)
    monkeypatch.setattr(
        watch.live.community,
        "presence_actor",
        lambda broadcast_id, token: captured.append(token) or "anon:key",
    )
    monkeypatch.setattr(watch, "_interactive_state", lambda broadcast_id, viewer_key: {"viewer_key": viewer_key})

    response = watch.watch_interactive_state(
        "live-1",
        _request(
            path="/shared-sky/live/api/watch/live-1/interactives",
            headers=[(b"x-shared-sky-presence", b"opaque-presence-token")],
            query=b"presence_token=must-not-be-used",
        ),
    )

    assert captured == ["opaque-presence-token"]
    assert response == {"viewer_key": "anon:key"}


class _InteractiveCommunity:
    def __init__(self, db_path: Path):
        self.db_path = db_path

    def _connect(self):
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        return con

    def poll(self, poll_id: str, viewer_key: str | None = None):
        return {
            "id": poll_id,
            "question": "Pick one",
            "state": "live",
            "options": [],
            "voted": False,
            "viewer_key": viewer_key,
        }


def test_public_interactive_state_does_not_expose_qa_asker_identity(tmp_path: Path, monkeypatch):
    db = tmp_path / "watch-v2.sqlite3"
    with sqlite3.connect(db) as con:
        con.executescript(
            """
            CREATE TABLE shared_sky_polls(id TEXT PRIMARY KEY,broadcast_id TEXT,state TEXT,starts_at TEXT);
            CREATE TABLE shared_sky_qa(
                id TEXT PRIMARY KEY,broadcast_id TEXT,asker_user_id TEXT,question TEXT,state TEXT,
                selected INTEGER,created_at TEXT,updated_at TEXT
            );
            INSERT INTO shared_sky_polls VALUES('poll-1','live-1','live','2026-09-05T01:00:00+00:00');
            INSERT INTO shared_sky_qa VALUES(
                'q-1','live-1','private-user-id','Will there be a replay?','approved',1,
                '2026-09-05T01:00:00+00:00','2026-09-05T01:00:00+00:00'
            );
            """
        )

    monkeypatch.setattr(watch.live, "community", _InteractiveCommunity(db))
    state = watch._interactive_state("live-1", "anon:key")

    assert state["polls"][0]["id"] == "poll-1"
    assert state["questions"][0]["question"] == "Will there be a replay?"
    assert "asker_user_id" not in state["questions"][0]


def test_watch_page_escapes_content_disables_member_writes_and_never_queries_presence_token(monkeypatch):
    detail = {
        "broadcast": {
            "id": "live-1",
            "creator_user_id": "creator-1",
            "creator_display_name": "Creator <One>",
            "title": "<script>alert(1)</script>",
            "description": "<img src=x onerror=alert(1)>",
            "state": "live",
            "authoritative_state": "live",
            "started_at": "2026-09-05T01:00:00+00:00",
            "ended_at": None,
        },
        "viewer_count": 4,
        "following": False,
        "playback": {"available": False, "state": "unavailable", "reason": "browser_bearer_playback_runtime_pending"},
        "replay": None,
        "chat_settings": {"enabled": True},
        "reactions": {},
        "gift_display": {"available": False},
        "battle_display": {"available": False},
    }

    monkeypatch.setattr(watch, "_member_id", lambda request: None)
    monkeypatch.setattr(watch.live.community, "detail", lambda broadcast_id, request, user_id: detail)

    response = watch.watch_page_v2("live-1", _request())
    html = response.body.decode()

    assert response.status_code == 200
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "Creator &lt;One&gt;" in html
    assert "Sign in to chat" in html
    assert "Sign in to ask a question" in html
    assert "Sign in to follow" in html
    assert "X-Shared-Sky-Presence" in html
    assert "interactives?presence_token=" not in html
    assert "This browser needs the Shared Sky HLS runtime" in html
    assert "an external post is not assumed" in html


def test_bootstrap_keeps_wave6_engagement_as_single_canonical_watch_route(monkeypatch):
    monkeypatch.setattr(bootstrap, "configure_neighbor_live_integrations", lambda: {})
    monkeypatch.setattr(bootstrap, "harden_browser_playback_integration", lambda: {})
    monkeypatch.setattr(bootstrap, "install_chat2_browser_playback_bridge", lambda: {})
    monkeypatch.setattr(bootstrap, "install_chat6_battle_viewer_bridge", lambda: {})
    monkeypatch.setattr(bootstrap, "install_live_community_hardening", lambda: None)
    app = FastAPI()

    bootstrap.install_shared_sky_live_community(app)

    watch_routes = [
        route
        for route in app.router.routes
        if getattr(route, "path", None) == "/watch/{broadcast_id}"
        and "GET" in (getattr(route, "methods", set()) or set())
    ]
    interactive_routes = [
        route
        for route in app.router.routes
        if getattr(route, "path", None) == "/shared-sky/live/api/watch/{broadcast_id}/interactives"
        and "GET" in (getattr(route, "methods", set()) or set())
    ]

    assert len(watch_routes) == 1
    assert watch_routes[0].endpoint.__name__ == "watch_page_engagement_wave6"
    assert len(interactive_routes) == 1
    assert interactive_routes[0].endpoint.__name__ == "watch_interactive_state"
