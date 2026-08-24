from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from aura_music_studio import visual_fx_api
from aura_music_studio.visual_fx_render_ownership import (
    VisualFxRenderOwnership,
    VisualFxRenderOwnershipError,
)


def _ledger(tmp_path: Path) -> tuple[VisualFxRenderOwnership, Path]:
    db_path = tmp_path / "studio.sqlite3"
    output_root = tmp_path / "visual_fx"
    ledger = VisualFxRenderOwnership(db_path, output_root)
    return ledger, output_root


def _render_file(root: Path, render_id: str, kind: str = "mp4") -> Path:
    path = root / f"{render_id}.{kind}"
    path.write_bytes(b"x" * 2048)
    return path


def test_registered_render_resolves_only_for_owner(tmp_path: Path):
    ledger, root = _ledger(tmp_path)
    output = _render_file(root, "render-a")

    item = ledger.register(
        user_id="user-a",
        project_id="project-a",
        render_id="render-a",
        output_kind="mp4",
        output_path=output,
    )

    assert item["user_id"] == "user-a"
    assert item["project_id"] == "project-a"
    assert item["output_name"] == "render-a.mp4"
    assert ledger.resolve(user_id="user-a", render_id="render-a", output_kind="mp4") == output.resolve()

    with pytest.raises(VisualFxRenderOwnershipError, match="unavailable"):
        ledger.resolve(user_id="user-b", render_id="render-a", output_kind="mp4")


def test_unregistered_physical_file_fails_closed(tmp_path: Path):
    ledger, root = _ledger(tmp_path)
    _render_file(root, "legacy-file")

    with pytest.raises(VisualFxRenderOwnershipError, match="unavailable"):
        ledger.resolve(user_id="user-a", render_id="legacy-file", output_kind="mp4")


def test_wrong_output_kind_is_not_an_alias(tmp_path: Path):
    ledger, root = _ledger(tmp_path)
    output = _render_file(root, "render-kind", "mp4")
    ledger.register(
        user_id="user-a",
        project_id="project-a",
        render_id="render-kind",
        output_kind="mp4",
        output_path=output,
    )

    with pytest.raises(VisualFxRenderOwnershipError, match="unavailable"):
        ledger.resolve(user_id="user-a", render_id="render-kind", output_kind="png")


def test_register_rejects_output_outside_export_boundary(tmp_path: Path):
    ledger, _ = _ledger(tmp_path)
    outside = tmp_path / "elsewhere" / "render-escape.mp4"
    outside.parent.mkdir(parents=True)
    outside.write_bytes(b"x" * 2048)

    with pytest.raises(VisualFxRenderOwnershipError, match="outside"):
        ledger.register(
            user_id="user-a",
            project_id="project-a",
            render_id="render-escape",
            output_kind="mp4",
            output_path=outside,
        )


def test_render_id_cannot_be_rebound_to_another_user(tmp_path: Path):
    ledger, root = _ledger(tmp_path)
    output = _render_file(root, "fixed-id")
    ledger.register(
        user_id="user-a",
        project_id="project-a",
        render_id="fixed-id",
        output_kind="mp4",
        output_path=output,
    )

    with pytest.raises(VisualFxRenderOwnershipError, match="already owned"):
        ledger.register(
            user_id="user-b",
            project_id="project-b",
            render_id="fixed-id",
            output_kind="mp4",
            output_path=output,
        )


def test_user_render_listing_never_exposes_filesystem_path(tmp_path: Path):
    ledger, root = _ledger(tmp_path)
    output = _render_file(root, "render-list")
    ledger.register(
        user_id="user-a",
        project_id="project-a",
        render_id="render-list",
        output_kind="mp4",
        output_path=output,
    )

    items = ledger.list_for_user("user-a")
    assert items == [
        {
            "render_id": "render-list",
            "project_id": "project-a",
            "output_kind": "mp4",
            "created_at": items[0]["created_at"],
        }
    ]
    assert "output_path" not in items[0]
    assert "output_name" not in items[0]
    assert ledger.list_for_user("user-b") == []


def test_render_api_returns_opaque_download_url_not_server_path(monkeypatch, tmp_path: Path):
    output = tmp_path / "opaque-render.mp4"
    output.write_bytes(b"x" * 2048)
    member = SimpleNamespace(user_id="user-a")
    registered: dict = {}

    monkeypatch.setattr(visual_fx_api, "_member", lambda request: member)
    monkeypatch.setattr(
        visual_fx_api.store,
        "render_project",
        lambda **kwargs: {
            "id": "opaque-render",
            "status": "completed",
            "output_kind": "mp4",
            "output_path": str(output),
        },
    )
    monkeypatch.setattr(
        visual_fx_api.render_ownership,
        "register",
        lambda **kwargs: registered.update(kwargs) or {"render_id": kwargs["render_id"]},
    )

    response = visual_fx_api.render_project(
        "project-a",
        visual_fx_api.RenderBody(output_kind="mp4"),
        request=None,
    )

    assert response == {
        "id": "opaque-render",
        "status": "completed",
        "output_kind": "mp4",
        "download_url": "/api/visual-fx/renders/opaque-render/mp4",
    }
    assert "output_path" not in response
    assert registered["user_id"] == "user-a"
    assert registered["project_id"] == "project-a"


def test_download_route_masks_cross_user_or_missing_render(monkeypatch):
    member = SimpleNamespace(user_id="user-b")
    monkeypatch.setattr(visual_fx_api, "_member", lambda request: member)

    def denied(**kwargs):
        raise VisualFxRenderOwnershipError("Rendered output is unavailable")

    monkeypatch.setattr(visual_fx_api.render_ownership, "resolve", denied)

    with pytest.raises(HTTPException) as exc_info:
        visual_fx_api.download_render("render-a", "mp4", request=None)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Rendered output is unavailable"
