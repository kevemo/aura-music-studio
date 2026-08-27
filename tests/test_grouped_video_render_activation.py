from __future__ import annotations

from pathlib import Path

import aura_music_studio.professional_editor_render_api as api


class _Plan:
    def has(self, feature: str) -> bool:
        return True


class _Member:
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


class _Result:
    filename = "grouped-export.mp4"

    def model_dump(self, *, mode: str):
        assert mode == "json"
        return {"filename": self.filename, "renderer": "grouped-proof"}


def test_mp4_export_dispatches_to_grouped_unified_compositor(monkeypatch):
    calls = {}

    class _Grouped:
        def __init__(self, project_dir):
            calls["project_dir"] = project_dir

        def render_video_advanced(self, sequence_id):
            calls["sequence_id"] = sequence_id
            return _Result()

    monkeypatch.setattr(api, "_member", lambda request: _Member())
    monkeypatch.setattr(api, "_renderer", lambda project_name: _Renderer())
    monkeypatch.setattr(api, "_project", lambda project_name: Path("/tmp/grouped-video-project"))
    monkeypatch.setattr(api, "GroupedUnifiedAdvancedVideoCompositor", _Grouped)

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
    assert response["non_destructive"] is True
    assert response["source_media_mutated"] is False
    assert response["frame_time"] is None


def test_render_api_no_longer_instantiates_legacy_unified_video_compositor():
    source = Path(api.__file__).read_text(encoding="utf-8")
    assert "GroupedUnifiedAdvancedVideoCompositor(_project(project_name))" in source
    assert "UnifiedAdvancedVideoCompositor(_project(project_name))" not in source
