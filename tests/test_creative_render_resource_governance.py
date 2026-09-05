from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from aura_music_studio.creative_project import CreativeDirective, CreativeProjectStore
from aura_music_studio.creative_project_api import QueueRendererRequest
from aura_music_studio.creative_render_resource_governance import (
    CreativeRenderResourceStore,
    render_units,
)
import aura_music_studio.creative_version_autopromotion as overlay


def test_render_units_scale_with_pixels_and_video_frames():
    assert render_units("image", width=1024, height=1024, frames=1) == 1
    assert render_units("image", width=2048, height=2048, frames=1) == 4
    assert render_units("video", width=1024, height=1024, frames=121) == 1
    assert render_units("video", width=2048, height=2048, frames=242) == 8


def test_store_enforces_daily_and_burst_limits_atomically(tmp_path, monkeypatch):
    monkeypatch.setenv("CREATIVE_RENDER_FREE_DAILY_UNITS", "3")
    monkeypatch.setenv("CREATIVE_RENDER_FREE_BURST_JOBS", "2")
    monkeypatch.setenv("CREATIVE_RENDER_FREE_MAX_REQUEST_UNITS", "2")
    store = CreativeRenderResourceStore(tmp_path / "db.sqlite3")
    now = datetime(2026, 8, 28, 8, 0, tzinfo=timezone.utc)

    first = store.reserve(
        user_id="u1", plan_id="free", project_name="p", directive_id="d1",
        media_kind="image", width=1024, height=1024, frames=1, now=now,
    )
    second = store.reserve(
        user_id="u1", plan_id="free", project_name="p", directive_id="d2",
        media_kind="image", width=1024, height=1024, frames=1, now=now,
    )
    assert first["billing_charge"] is False
    assert second["daily_units_used"] == 2
    with pytest.raises(PermissionError, match="burst limit"):
        store.reserve(
            user_id="u1", plan_id="free", project_name="p", directive_id="d3",
            media_kind="image", width=1024, height=1024, frames=1, now=now,
        )


def test_store_rejects_single_request_over_plan_compute_ceiling(tmp_path, monkeypatch):
    monkeypatch.setenv("CREATIVE_RENDER_BASE_MAX_REQUEST_UNITS", "2")
    store = CreativeRenderResourceStore(tmp_path / "db.sqlite3")
    with pytest.raises(PermissionError, match="per-request compute ceiling"):
        store.reserve(
            user_id="u1", plan_id="base", project_name="p", directive_id="d",
            media_kind="video", width=2048, height=2048, frames=121,
        )


def test_cancel_releases_failed_submission_reservation(tmp_path):
    store = CreativeRenderResourceStore(tmp_path / "db.sqlite3")
    reservation = store.reserve(
        user_id="u1", plan_id="free", project_name="p", directive_id="d",
        media_kind="image", width=1024, height=1024, frames=1,
    )
    assert store.cancel(reservation["reservation_id"], user_id="u1") is True
    assert store.cancel(reservation["reservation_id"], user_id="u1") is False


def _project(tmp_path):
    project = tmp_path / "project"
    store = CreativeProjectStore(project)
    store.initialize(project_name="project", title="Project")
    directive = CreativeDirective(
        instruction="Create an original cosmic cover",
        operation="create",
        target_kind="image",
    )
    store.add_directive(directive)
    return project, directive


def _request():
    return SimpleNamespace(
        state=SimpleNamespace(
            member=SimpleNamespace(user_id="member-1", plan=SimpleNamespace(id="base"))
        )
    )


def test_overlay_reserves_before_delegating_and_returns_evidence(tmp_path, monkeypatch):
    project, directive = _project(tmp_path)
    calls = []

    class Guard:
        def reserve(self, **kwargs):
            calls.append(("reserve", kwargs))
            return {"reservation_id": "r1", "units": 1, "billing_charge": False}

        def cancel(self, reservation_id, *, user_id):
            calls.append(("cancel", reservation_id, user_id))
            return True

    monkeypatch.setattr(overlay, "creative_render_resource_store", Guard())
    monkeypatch.setattr(overlay, "project_path", lambda name, must_exist=True: project)
    monkeypatch.setattr(
        overlay,
        "base_queue_creative_render",
        lambda project_name, directive_id, body, request: {"submission": {"prompt_id": "p1"}},
    )

    response = overlay.queue_render_with_resource_governance(
        "project", directive.id, QueueRendererRequest(width=1024, height=1024), _request()
    )
    assert calls[0][0] == "reserve"
    assert calls[0][1]["user_id"] == "member-1"
    assert response["resource_governance"]["reservation_id"] == "r1"
    assert all(call[0] != "cancel" for call in calls)


def test_overlay_rolls_back_reservation_when_renderer_submission_fails(tmp_path, monkeypatch):
    project, directive = _project(tmp_path)
    cancelled = []

    class Guard:
        def reserve(self, **kwargs):
            return {"reservation_id": "r2", "units": 1}

        def cancel(self, reservation_id, *, user_id):
            cancelled.append((reservation_id, user_id))
            return True

    monkeypatch.setattr(overlay, "creative_render_resource_store", Guard())
    monkeypatch.setattr(overlay, "project_path", lambda name, must_exist=True: project)

    def fail(*args, **kwargs):
        raise RuntimeError("renderer unavailable")

    monkeypatch.setattr(overlay, "base_queue_creative_render", fail)
    with pytest.raises(RuntimeError, match="renderer unavailable"):
        overlay.queue_render_with_resource_governance(
            "project", directive.id, QueueRendererRequest(), _request()
        )
    assert cancelled == [("r2", "member-1")]


def test_overlay_route_precedes_generic_creative_router_contract():
    paths = [getattr(route, "path", "") for route in overlay.router.routes]
    assert "/creative/projects/{project_name}/directives/{directive_id}/render" in paths
