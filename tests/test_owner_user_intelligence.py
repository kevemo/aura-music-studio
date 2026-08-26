from __future__ import annotations

import json

from aura_music_studio.owner_user_intelligence import _social_summary, router


def test_owner_social_summary_reads_only_member_isolated_metadata(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_SOCIAL_ROOT", str(tmp_path / "social"))
    user_id = "abc123"
    root = tmp_path / "social" / user_id
    root.mkdir(parents=True)
    (root / "index.json").write_text(
        json.dumps({"schema_version": 1, "spaces": [{"id": "space_one", "name": "One"}, {"id": "space_two", "name": "Two"}]}),
        encoding="utf-8",
    )
    (root / "space_one.json").write_text(
        json.dumps({
            "id": "space_one",
            "updated_at": "2026-08-26T00:00:00+00:00",
            "content": [{"status": "pending_approval"}, {"status": "published"}],
            "projects": [{"id": "p1"}],
            "tasks": [{"id": "t1"}, {"id": "t2"}],
            "activity": [{"id": "a1"}],
        }),
        encoding="utf-8",
    )
    (root / "space_two.json").write_text(
        json.dumps({
            "id": "space_two",
            "updated_at": "2026-08-26T01:00:00+00:00",
            "content": [{"status": "draft"}],
            "projects": [],
            "tasks": [],
            "activity": [{"id": "a2"}, {"id": "a3"}],
            "connections": [{"token_secret_ref": "must-not-be-returned"}],
        }),
        encoding="utf-8",
    )

    summary = _social_summary(user_id)
    assert summary == {
        "spaces": 2,
        "content": 3,
        "tasks": 2,
        "campaigns": 1,
        "pending_approval": 1,
        "published": 1,
        "activity": 3,
        "latest_activity": "2026-08-26T01:00:00+00:00",
    }
    assert "token" not in json.dumps(summary).lower()


def test_owner_social_summary_rejects_unsafe_user_path(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_SOCIAL_ROOT", str(tmp_path / "social"))
    summary = _social_summary("../other-member")
    assert summary["spaces"] == 0
    assert summary["content"] == 0


def test_owner_intelligence_router_has_detail_overlay_and_intelligence_page():
    paths = {getattr(route, "path", None) for route in router.routes}
    assert "/owner/users/{user_id}" in paths
    assert "/owner/users/{user_id}/intelligence" in paths
