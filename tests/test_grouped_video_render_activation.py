from __future__ import annotations

from pathlib import Path

import aura_music_studio.professional_editor_render_api as api
from aura_music_studio.professional_video_mask_effects_colour_compositor import UniversalVisualVideoCompositor
from aura_music_studio.professional_video_grouped_unified_compositor import GroupedUnifiedAdvancedVideoCompositor


class _Plan:
    def has(self, feature: str) -> bool:
        return True


class _Member:
    user_id = "grouped-video-test-user"
    plan = _Plan()


class _Store:
    def public_state(self):
        return {
            "branch": {
                "sequences": [
                    {
                        "id": "seq_video",
                        "kind": "video",
                        "track_ids": [],
                    }
                ],
                "tracks": [],
                "items": [],
            }
        }


class _Renderer:
    store = _Store()

    def advanced_state(self, state, sequence_id):
        assert sequence_id == "seq_video"
        return {"advanced": False}

    def resolve_export(self, filename):
        return Path("/tmp") / filename


class _Result:
    filename = "grouped-export.mp4"

    def model_dump(self, *, mode: str):
        assert mode == "json"
        return {"filename": self.filename, "renderer": "grouped-proof"}


class _Provenance:
    def record_export(self, **kwargs):
        assert kwargs["user_id"] == "grouped-video-test-user"
        assert kwargs["commercial_use_requested"] is False
        assert kwargs["rights_attested"] is False
        return {
            "id": "grouped-video-provenance",
            "commercial_platform_export_allowed": False,
            "automatic_legal_clearance": False,
        }


def test_mp4_export_dispatches_to_universal_visual_compositor(monkeypatch):
    calls = {}

    class _UniversalVisual:
        def __init__(self, project_dir):
            calls["project_dir"] = project_dir

        def render_video_advanced(self, sequence_id):
            calls["sequence_id"] = sequence_id
            return _Result()

    monkeypatch.setattr(api, "_member", lambda request: _Member())
    monkeypatch.setattr(api, "_renderer", lambda project_name: _Renderer())
    monkeypatch.setattr(api, "_project", lambda project_name: Path("/tmp/grouped-video-project"))
    monkeypatch.setattr(api, "UniversalVisualVideoCompositor", _UniversalVisual)
    monkeypatch.setattr(api, "export_provenance_store", _Provenance())

    response = api.render_editor_sequence(
        "DemoProject",
        "seq_video",
        api.EditorRenderRequest(format="mp4"),
        object(),
    )

    assert calls == {
        "project_dir": Path("/tmp/grouped-video-project"),
        "sequence_id": "seq_video",
    }
    assert response["export"]["renderer"] == "grouped-proof"
    assert response["download_url"].endswith("/editor/exports/grouped-export.mp4")
    assert response["provenance"]["id"] == "grouped-video-provenance"
    assert response["automatic_legal_clearance"] is False
    assert response["non_destructive"] is True
    assert response["source_media_mutated"] is False
    assert response["frame_time"] is None


def test_universal_visual_compositor_preserves_grouped_unified_renderer_foundation():
    # Wave 12 extends the mask/crop -> track-keyframe -> keyframed-mask -> chroma -> grouped stack
    # rather than bypassing masks, effects, blends, crop or grouped-track state.
    assert issubclass(UniversalVisualVideoCompositor, GroupedUnifiedAdvancedVideoCompositor)


def test_render_api_uses_universal_visual_compositor_without_reintroducing_legacy_unified_renderer():
    source = Path(api.__file__).read_text(encoding="utf-8")
    assert "UniversalVisualVideoCompositor(_project(project_name))" in source
    assert (
        "from .professional_video_mask_effects_colour_compositor import UniversalVisualVideoCompositor"
        in source
    )
    assert "professional_video_mask_crop_compositor ->" in source
    assert "professional_video_track_keyframe_universal_compositor ->" in source
    assert "professional_video_track_keyframe_compositor ->" in source
    assert "professional_keyframed_mask_video_compositor ->" in source
    assert "from .professional_video_unified_compositor import UnifiedAdvancedVideoCompositor" not in source
    assert not hasattr(api, "UnifiedAdvancedVideoCompositor")
