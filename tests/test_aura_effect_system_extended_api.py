from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

import aura_music_studio.aura_effect_system_extended_api as extended
from aura_music_studio.aura_effect_system_extended_api import (
    EffectAutomationRequest,
    ReusableEffectSystemPublishRequest,
    effect_system_extended_route_registrations,
)


class _Request:
    def __init__(self, member=None):
        self.state = SimpleNamespace(member=member)


def _member():
    return SimpleNamespace(user_id="member-1", plan=SimpleNamespace(id="basic"))


def test_extended_routes_cover_automation_and_private_reuse():
    prefix = "/command-center/api/universal-library"
    rows = effect_system_extended_route_registrations(prefix)
    triples = {(path, method) for path, _endpoint, method in rows}
    automation = (
        f"{prefix}/effect-systems/projects/{{project_name}}/tracks/{{track_id}}/"
        "systems/{system_id}/nodes/{node_id}/mix-automation"
    )

    assert (automation, "GET") in triples
    assert (automation, "PUT") in triples
    assert (automation, "DELETE") in triples
    assert (f"{prefix}/effect-systems/library", "GET") in triples
    assert (f"{prefix}/effect-systems/library/{{item_id}}", "GET") in triples
    assert (f"{prefix}/effect-systems/library/{{item_id}}", "DELETE") in triples
    assert (f"{prefix}/effect-systems/library/{{item_id}}/import", "POST") in triples
    assert any(path.endswith("/{system_id}/publish-private") and method == "POST" for path, method in triples)
    assert any("automation/restore/{revision_id}" in path and method == "POST" for path, method in triples)


def test_automation_request_is_bounded_and_allowlisted():
    valid = EffectAutomationRequest(
        points=[{"time": 0, "value": 0.1}, {"time": 2.5, "value": 0.8}],
        interpolation="smooth",
    )
    assert valid.interpolation == "smooth"
    assert len(valid.points) == 2

    with pytest.raises(ValidationError):
        EffectAutomationRequest(points=[], interpolation="cubic")
    with pytest.raises(ValidationError):
        EffectAutomationRequest(points=[{"time": float(i), "value": 0.5} for i in range(2001)])


def test_publish_request_bounds_tags_and_item_id():
    body = ReusableEffectSystemPublishRequest(item_id="effect-system.vocal-polish", tags=["Vocals", "Warm"])
    assert body.item_id == "effect-system.vocal-polish"
    assert body.tags == ["Vocals", "Warm"]
    with pytest.raises(ValidationError):
        ReusableEffectSystemPublishRequest(item_id="x", tags=[f"tag-{i}" for i in range(21)])


def test_member_required_before_extended_library_access():
    with pytest.raises(HTTPException) as exc:
        extended.list_member_reusable_effect_systems(_Request())
    assert exc.value.status_code == 401


def test_set_automation_wrapper_preserves_revision_and_source_truth(monkeypatch, tmp_path: Path):
    project = tmp_path / "song"
    project.mkdir()
    monkeypatch.setattr(extended, "_project", lambda _name: project)
    captured = {}

    def fake_set(project_arg, track_id, system_id, node_id, points, **kwargs):
        captured.update(
            project=project_arg,
            track_id=track_id,
            system_id=system_id,
            node_id=node_id,
            points=points,
            kwargs=kwargs,
        )
        return {
            "updated": True,
            "revision_id": "rev-1",
            "project_metadata_mutated": True,
            "source_media_mutated": False,
            "undo_available": True,
        }

    monkeypatch.setattr(extended, "set_effect_system_mix_automation", fake_set)
    result = extended.set_member_effect_system_mix_automation(
        "song",
        "track-1",
        "vocal-polish",
        "space",
        EffectAutomationRequest(points=[{"time": 0, "value": 0.2}], interpolation="linear"),
        _Request(_member()),
    )

    assert captured["project"] == project
    assert captured["points"] == [{"time": 0.0, "value": 0.2}]
    assert captured["kwargs"]["actor"] == "Aura Effect/System Creator"
    assert result["revision_backed"] is True
    assert result["source_media_mutated"] is False
    assert result["undo_available"] is True
    assert result["plan"] == "basic"


def test_private_publish_wrapper_never_widens_marketplace(monkeypatch, tmp_path: Path):
    project = tmp_path / "song"
    library = tmp_path / "member-root"
    project.mkdir()
    library.mkdir()
    monkeypatch.setattr(extended, "_project", lambda _name: project)
    monkeypatch.setattr(extended, "projects_root", lambda: library)

    def fake_publish(project_arg, system_id, *, item_id, tags, library_root):
        assert project_arg == project
        assert system_id == "vocal-polish"
        assert item_id == "effect-system.vocal-polish"
        assert tags == ["Vocals"]
        assert library_root == library
        return {
            "item_id": item_id,
            "visibility": "private",
            "marketplace_published": False,
            "sale_enabled": False,
            "source_media_mutated": False,
            "published": True,
        }

    monkeypatch.setattr(extended, "publish_project_effect_system", fake_publish)
    result = extended.publish_member_reusable_effect_system(
        "song",
        "vocal-polish",
        ReusableEffectSystemPublishRequest(item_id="effect-system.vocal-polish", tags=["Vocals"]),
        _Request(_member()),
    )

    assert result["visibility"] == "private"
    assert result["marketplace_published"] is False
    assert result["sale_enabled"] is False
    assert result["source_media_mutated"] is False
    assert result["reusable_library"] is True
