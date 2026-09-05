from __future__ import annotations

from starlette.requests import Request

import aura_music_studio.revision_portal as portal


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "https",
            "path": "/history",
            "raw_path": b"/history",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 443),
            "root_path": "",
        }
    )


def _body(response) -> str:
    return response.body.decode("utf-8")


def test_pro_history_exposes_cross_editor_domains_and_ab_compare(monkeypatch):
    monkeypatch.setattr(
        portal.store,
        "resolve_session",
        lambda _cookie: {"status": "active", "plan_id": "pro", "display_name": "Studio Pro"},
    )

    response = portal.history_portal(_request())
    body = _body(response)

    assert response.status_code == 200
    assert "ESP CROSS-EDITOR PROJECT HISTORY" in body
    assert "A/B checkpoint comparison" in body
    assert "const DEEP=true;" in body
    assert "/revisions/compare?left=" in body
    assert "Music / DAW" in body
    assert "Creative Project" in body
    assert "Image + Video Editor" in body
    assert "Production Metadata" in body
    assert "Project Core" in body
    assert "Restore full checkpoint" in body
    assert "source media not duplicated" in body
    assert "media binaries are not duplicated" in body
    assert "restorable as a complete supported project checkpoint" in body
    assert "__DEEP__" not in body
    assert "__COMPARE_NOTE__" not in body


def test_basic_history_keeps_full_restore_but_pro_compare_is_locked(monkeypatch):
    monkeypatch.setattr(
        portal.store,
        "resolve_session",
        lambda _cookie: {"status": "active", "plan_id": "base", "display_name": "Basic Member"},
    )

    response = portal.history_portal(_request())
    body = _body(response)

    assert response.status_code == 200
    assert "const ENABLED=true;" in body
    assert "const DEEP=false;" in body
    assert "Cross-editor A/B checkpoint comparison unlocks on Pro" in body
    assert 'id="compareButton" class="primary" onclick="compareRevisions()" disabled' in body
    assert "Create checkpoint" in body
    assert "Restore full checkpoint" in body
    assert "Basic still preserves and restores complete supported checkpoints" in body


def test_free_history_remains_locked_without_hiding_non_destructive_truth(monkeypatch):
    monkeypatch.setattr(
        portal.store,
        "resolve_session",
        lambda _cookie: {"status": "active", "plan_id": "free", "display_name": "Free Member"},
    )

    response = portal.history_portal(_request())
    body = _body(response)

    assert response.status_code == 200
    assert "Revision history unlocks on Basic" in body
    assert "const ENABLED=false;" in body
    assert "const DEEP=false;" in body
    assert 'onclick="snapshot()" disabled' in body
    assert "Audio, image and video source files remain referenced in place" in body


def test_history_redirects_when_no_member_session(monkeypatch):
    monkeypatch.setattr(portal.store, "resolve_session", lambda _cookie: None)

    response = portal.history_portal(_request())

    assert response.status_code == 303
    assert response.headers["location"] == "/signin"
