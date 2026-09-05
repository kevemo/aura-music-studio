from __future__ import annotations

from pathlib import Path

import pytest

from aura_music_studio.professional_editor_api import router as professional_editor_router
from aura_music_studio.professional_editor_security_overlay import (
    install_professional_editor_patch_guard,
    normalize_item_patch_sources,
    patch_item_source_guard,
)


PATCH_PATH = "/creative/projects/{project_name}/editor/items/{item_id}"


def test_patch_source_normalizes_portable_relative_reference(tmp_path: Path):
    changes = {"source_ref": r"output\\clips\\scene-01.mp4", "opacity": 0.75}

    normalized = normalize_item_patch_sources(tmp_path, changes)

    assert normalized == {"source_ref": "output/clips/scene-01.mp4", "opacity": 0.75}
    assert changes["source_ref"] == r"output\\clips\\scene-01.mp4"


@pytest.mark.parametrize(
    "source_ref",
    [
        "../outside.mp4",
        r"output\\..\\outside.mp4",
        "/etc/passwd",
        r"C:\\Windows\\system32\\config",
        r"\\\\server\\share\\clip.mp4",
        "https://example.invalid/clip.mp4",
        "file:///etc/passwd",
        "bad\x00name.mp4",
    ],
)
def test_patch_source_rejects_non_project_references(tmp_path: Path, source_ref: str):
    with pytest.raises(ValueError):
        normalize_item_patch_sources(tmp_path, {"source_ref": source_ref})


def test_patch_without_source_ref_preserves_other_changes(tmp_path: Path):
    changes = {"opacity": 0.4, "visible": False, "metadata": {"label": "cut"}}

    normalized = normalize_item_patch_sources(tmp_path, changes)

    assert normalized == changes
    assert normalized is not changes
    assert normalized["metadata"] is not changes["metadata"]


def test_guarded_patch_route_precedes_legacy_generic_patch_route():
    install_professional_editor_patch_guard()
    matching = [
        route
        for route in professional_editor_router.routes
        if getattr(route, "path", None) == PATCH_PATH
        and "PATCH" in getattr(route, "methods", set())
    ]

    assert len(matching) >= 2
    assert matching[0].endpoint is patch_item_source_guard


def test_guard_install_is_idempotent():
    install_professional_editor_patch_guard()
    before = [
        route
        for route in professional_editor_router.routes
        if getattr(route, "endpoint", None) is patch_item_source_guard
    ]
    install_professional_editor_patch_guard()
    after = [
        route
        for route in professional_editor_router.routes
        if getattr(route, "endpoint", None) is patch_item_source_guard
    ]

    assert len(before) == 1
    assert len(after) == 1
