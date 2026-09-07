from pathlib import Path

import pytest
from fastapi import HTTPException
from PIL import Image

from aura_music_studio import image_effect_api
from aura_music_studio.access_control import BASIC_CREATE, _required_feature
from aura_music_studio.aura_image_effect_system import compose_image_effect_system
from aura_music_studio.executable_image_effects import ImageEffectGraph
from aura_music_studio.image_effect_api import (
    PreviewImageEffectRequest,
    SaveImageEffectPresetRequest,
    get_image_effect_preset,
    preview_project_image_effect,
    save_image_effect_preset,
)


def _graph() -> ImageEffectGraph:
    composed = compose_image_effect_system("increase brightness and contrast")
    return ImageEffectGraph.model_validate(composed["graph"])


def test_image_effect_api_uses_existing_basic_create_entitlement() -> None:
    assert _required_feature("/image-effects/compose", "POST") == BASIC_CREATE
    assert _required_feature("/image-effects/presets/portrait", "POST") == BASIC_CREATE
    assert _required_feature("/projects/demo/image-effects/preview", "POST") == BASIC_CREATE
    assert _required_feature("/projects/demo/image-effects/previews/abc.png", "GET") == BASIC_CREATE


def test_preview_is_project_scoped_and_does_not_expose_host_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "project"
    project.mkdir()
    source = project / "input.png"
    Image.new("RGBA", (6, 6), (20, 30, 40, 200)).save(source)
    monkeypatch.setattr(image_effect_api, "project_path", lambda _name: project)

    result = preview_project_image_effect(
        "demo",
        PreviewImageEffectRequest(source="input.png", graph=_graph()),
    )

    assert result["project_scoped"] is True
    assert result["path_exposed"] is False
    assert result["preview_url"].startswith("/projects/demo/image-effects/previews/")
    assert str(tmp_path) not in repr(result)
    preview = project / "output" / "image-effects" / f"{result['preview_id']}.png"
    assert preview.exists()


def test_preview_rejects_project_traversal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "project"
    project.mkdir()
    Image.new("RGB", (2, 2), (1, 2, 3)).save(tmp_path / "secret.png")
    monkeypatch.setattr(image_effect_api, "project_path", lambda _name: project)

    with pytest.raises(HTTPException) as exc:
        preview_project_image_effect(
            "demo",
            PreviewImageEffectRequest(source="../secret.png", graph=_graph()),
        )

    assert exc.value.status_code == 400


def test_private_presets_are_tenant_scoped_beside_projects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    projects = tmp_path / "user-a" / "projects"
    projects.mkdir(parents=True)
    monkeypatch.setattr(image_effect_api, "projects_root", lambda: projects)
    graph = _graph()
    fingerprint = graph.fingerprint()

    saved = save_image_effect_preset(
        "portrait",
        SaveImageEffectPresetRequest(graph=graph, preview_token=fingerprint),
    )
    loaded = get_image_effect_preset("portrait")

    preset_file = tmp_path / "user-a" / "image_effect_presets" / "portrait.json"
    assert preset_file.exists()
    assert saved["private_reusable_preset"] is True
    assert saved["marketplace_published"] is False
    assert saved["sale_enabled"] is False
    assert loaded["fingerprint"] == fingerprint
    assert str(tmp_path) not in repr(saved)
    assert str(tmp_path) not in repr(loaded)


def test_canonical_app_mounts_each_image_effect_route_once() -> None:
    from aura_music_studio.api import app

    required = {
        ("/image-effects/compose", "POST"),
        ("/image-effects/presets/{preset_name}", "POST"),
        ("/image-effects/presets/{preset_name}", "GET"),
        ("/projects/{project_name}/image-effects/preview", "POST"),
        ("/projects/{project_name}/image-effects/previews/{preview_id}.png", "GET"),
    }
    found: dict[tuple[str, str], int] = {item: 0 for item in required}
    for route in app.routes:
        path = getattr(route, "path", "")
        for method in getattr(route, "methods", set()) or set():
            key = (path, method)
            if key in found:
                found[key] += 1

    assert found == {item: 1 for item in required}
