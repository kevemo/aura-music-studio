from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import aura_music_studio.creative_version_autopromotion as integration
import aura_music_studio.professional_editor_visual_entitlements as ent
from aura_music_studio.plans import AUTOMATION, BASIC_TIMELINE
from aura_music_studio.professional_editor_api import PatchRequest
from aura_music_studio.professional_editor_render_api import EditorRenderRequest


class _Plan:
    def __init__(self, *, pro: bool):
        self.pro = pro

    def has(self, feature: str) -> bool:
        if feature == BASIC_TIMELINE:
            return True
        if feature == AUTOMATION:
            return self.pro
        return True


class _Member:
    def __init__(self, *, pro: bool):
        self.plan = _Plan(pro=pro)


def _request(*, pro: bool):
    return SimpleNamespace(state=SimpleNamespace(member=_Member(pro=pro)))


def _visual_state(*, advanced: bool) -> dict:
    track = {
        "id": "track_1",
        "item_ids": ["item_1"],
        "blend_mode": "multiply" if advanced else "normal",
        "opacity": 0.6 if advanced else 1.0,
        "effects": ([{"id": "fx_track", "type": "invert"}] if advanced else []),
        "keyframes": ({"opacity": [{"time": 0.0, "value": 1.0}]} if advanced else {}),
    }
    item = {
        "id": "item_1",
        "blend_mode": "screen" if advanced else "normal",
        "effects": ([{"id": "fx_item", "type": "contrast"}] if advanced else []),
        "masks": ([{"id": "mask_1", "shape": "rectangle"}] if advanced else []),
        "keyframes": ({"transform.x": [{"time": 0.0, "value": 0.0}]} if advanced else {}),
    }
    return {
        "branch": {
            "sequences": [{"id": "seq_1", "track_ids": ["track_1"]}],
            "tracks": [track],
            "items": [item],
        }
    }


def test_visual_patch_classification_keeps_basic_item_opacity_basic():
    assert ent.item_patch_requires_pro({"blend_mode": "multiply"}) is True
    assert ent.item_patch_requires_pro({"effects": [{"type": "invert"}]}) is True
    assert ent.item_patch_requires_pro({"masks": [{"shape": "rectangle"}]}) is True
    assert ent.item_patch_requires_pro({"keyframes": {"opacity": [{"time": 0.0, "value": 1.0}]}}) is True
    assert ent.item_patch_requires_pro({"blend_mode": "normal"}) is False
    assert ent.item_patch_requires_pro({"effects": [], "masks": [], "keyframes": {}}) is False
    assert ent.item_patch_requires_pro({"opacity": 0.25}) is False

    assert ent.track_patch_requires_pro({"blend_mode": "screen"}) is True
    assert ent.track_patch_requires_pro({"opacity": 0.25}) is True
    assert ent.track_patch_requires_pro({"effects": [{"type": "blur"}]}) is True
    assert ent.track_patch_requires_pro({"keyframes": {"opacity": [{"time": 0.0, "value": 1.0}]}}) is True
    assert ent.track_patch_requires_pro({"blend_mode": "normal", "opacity": 1.0, "effects": [], "keyframes": {}}) is False


def test_basic_advanced_item_patch_is_blocked_before_base_mutation(monkeypatch):
    called = False

    def base(*args, **kwargs):
        nonlocal called
        called = True
        return {"unexpected": True}

    monkeypatch.setattr(ent, "base_patch_item", base)
    for changes in (
        {"blend_mode": "multiply"},
        {"effects": [{"type": "invert"}]},
        {"masks": [{"shape": "rectangle"}]},
        {"keyframes": {"opacity": [{"time": 0.0, "value": 1.0}]}},
    ):
        with pytest.raises(HTTPException) as exc:
            ent.patch_item_with_visual_entitlements(
                "Demo",
                "item_1",
                PatchRequest(changes=changes),
                _request(pro=False),
            )
        assert exc.value.status_code == 403
        assert "require Pro" in str(exc.value.detail)
    assert called is False


def test_basic_advanced_track_patch_is_blocked_before_base_mutation(monkeypatch):
    called = False

    def base(*args, **kwargs):
        nonlocal called
        called = True
        return {"unexpected": True}

    monkeypatch.setattr(ent, "base_patch_track", base)
    for changes in (
        {"blend_mode": "overlay"},
        {"opacity": 0.5},
        {"effects": [{"type": "blur"}]},
        {"keyframes": {"opacity": [{"time": 0.0, "value": 1.0}]}},
    ):
        with pytest.raises(HTTPException) as exc:
            ent.patch_track_with_visual_entitlements(
                "Demo",
                "track_1",
                PatchRequest(changes=changes),
                _request(pro=False),
            )
        assert exc.value.status_code == 403
        assert "require Pro" in str(exc.value.detail)
    assert called is False


def test_basic_can_reset_advanced_visual_state_to_defaults(monkeypatch):
    item_calls = []
    track_calls = []
    monkeypatch.setattr(ent, "base_patch_item", lambda *args: item_calls.append(args) or {"ok": True})
    monkeypatch.setattr(ent, "base_patch_track", lambda *args: track_calls.append(args) or {"ok": True})

    ent.patch_item_with_visual_entitlements(
        "Demo",
        "item_1",
        PatchRequest(changes={"blend_mode": "normal", "effects": [], "masks": [], "keyframes": {}}),
        _request(pro=False),
    )
    ent.patch_track_with_visual_entitlements(
        "Demo",
        "track_1",
        PatchRequest(changes={"blend_mode": "normal", "opacity": 1.0, "effects": [], "keyframes": {}}),
        _request(pro=False),
    )

    assert len(item_calls) == 1
    assert len(track_calls) == 1


def test_pro_can_write_item_and_track_advanced_state(monkeypatch):
    item_calls = []
    track_calls = []
    monkeypatch.setattr(ent, "base_patch_item", lambda *args: item_calls.append(args) or {"ok": True})
    monkeypatch.setattr(ent, "base_patch_track", lambda *args: track_calls.append(args) or {"ok": True})

    ent.patch_item_with_visual_entitlements(
        "Demo",
        "item_1",
        PatchRequest(changes={"blend_mode": "overlay", "effects": [{"type": "invert"}]}),
        _request(pro=True),
    )
    ent.patch_track_with_visual_entitlements(
        "Demo",
        "track_1",
        PatchRequest(changes={"blend_mode": "multiply", "opacity": 0.55, "effects": [{"type": "blur"}]}),
        _request(pro=True),
    )

    assert len(item_calls) == 1
    assert len(track_calls) == 1


def test_preserved_professional_visual_state_is_detected_for_downgrade_safety():
    assert ent.professional_visual_state_reasons(_visual_state(advanced=False), "seq_1") == []
    assert ent.professional_visual_state_reasons(_visual_state(advanced=True), "seq_1") == [
        "track:track_1:blend_mode",
        "track:track_1:opacity",
        "track:track_1:effects",
        "track:track_1:keyframes",
        "item:item_1:blend_mode",
        "item:item_1:effects",
        "item:item_1:masks",
        "item:item_1:keyframes",
    ]


def test_basic_render_of_preserved_pro_visual_state_fails_closed(monkeypatch):
    renderer = SimpleNamespace(store=SimpleNamespace(public_state=lambda: _visual_state(advanced=True)))
    rendered = False

    def base_render(*args, **kwargs):
        nonlocal rendered
        rendered = True
        return {"unexpected": True}

    monkeypatch.setattr(ent, "base_renderer", lambda project_name: renderer)
    monkeypatch.setattr(ent, "base_render_editor_sequence", base_render)

    with pytest.raises(HTTPException) as exc:
        ent.render_editor_sequence_with_visual_entitlements(
            "Demo",
            "seq_1",
            EditorRenderRequest(format="mp4"),
            _request(pro=False),
        )
    assert exc.value.status_code == 403
    assert "authored Pro state is preserved" in str(exc.value.detail)
    assert rendered is False


def test_pro_render_delegates_without_downgrade_block(monkeypatch):
    monkeypatch.setattr(ent, "base_renderer", lambda project_name: (_ for _ in ()).throw(AssertionError("not needed")))
    monkeypatch.setattr(ent, "base_render_editor_sequence", lambda *args: {"ok": True})

    result = ent.render_editor_sequence_with_visual_entitlements(
        "Demo",
        "seq_1",
        EditorRenderRequest(format="mp4"),
        _request(pro=True),
    )
    assert result == {"ok": True}


def test_entitlement_overlay_owns_protected_routes_and_is_mounted_first():
    checks = {
        "/creative/projects/{project_name}/editor/items/{item_id}": "patch_item_with_visual_entitlements",
        "/creative/projects/{project_name}/editor/tracks/{track_id}": "patch_track_with_visual_entitlements",
        "/creative/projects/{project_name}/editor/sequences/{sequence_id}/render": "render_editor_sequence_with_visual_entitlements",
    }
    for path, endpoint_name in checks.items():
        routes = [route for route in ent.router.routes if getattr(route, "path", None) == path]
        assert len(routes) == 1
        assert getattr(routes[0].endpoint, "__name__", "") == endpoint_name

    source = Path(integration.__file__).read_text(encoding="utf-8")
    assert "from .professional_editor_visual_entitlements import router as professional_editor_visual_entitlement_router" in source
    entitlement_mount = source.index("router.include_router(professional_editor_visual_entitlement_router)")
    assert entitlement_mount < source.index("router.include_router(professional_editor_router)")
    assert entitlement_mount < source.index("router.include_router(professional_editor_lifecycle_router)")
    assert entitlement_mount < source.index("router.include_router(professional_editor_render_router)")
