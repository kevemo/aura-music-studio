from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from aura_music_studio.professional_captions import (
    CaptionCueInput,
    CaptionPatchRequest,
    CaptionStyle,
    CaptionTrackRequest,
    add_caption_track,
    export_caption_text,
    parse_caption_text,
    router as captions_router,
)
from aura_music_studio.professional_captions_hardening import (
    install_professional_captions_hardening,
    patch_caption_hardened,
)
from aura_music_studio.professional_editor import ProfessionalEditorStore
from aura_music_studio.professional_video_mask_effects_colour_compositor import UniversalVisualVideoCompositor


def _editor(tmp_path: Path, *, duration: float = 3.0):
    project = (tmp_path / "project").resolve()
    project.mkdir()
    store = ProfessionalEditorStore(project)
    store.initialize("captions-project")
    sequence = store.create_sequence(
        kind="video",
        name="Caption render",
        width=640,
        height=360,
        fps=24.0,
        duration=duration,
        actor="test",
    )
    return project, store, sequence


def _request():
    return CaptionTrackRequest(
        name="English Subtitles",
        kind="subtitle",
        language="en-GB",
        style=CaptionStyle(size=40, color="#ffffffff", stroke_width=2, position="bottom"),
        cues=[
            CaptionCueInput(start=0.25, end=1.10, text="First subtitle"),
            CaptionCueInput(start=1.25, end=2.25, text="Second subtitle"),
        ],
    )


def test_srt_and_vtt_parse_to_bounded_plain_text_cues():
    srt = """1\n00:00:00,250 --> 00:00:01,100\n<i>First</i> subtitle\n\n2\n00:00:01,250 --> 00:00:02,250\n{\\an8}Second subtitle\n"""
    cues = parse_caption_text(srt, "srt")
    assert [(cue.start, cue.end, cue.text) for cue in cues] == [
        (0.25, 1.1, "First subtitle"),
        (1.25, 2.25, "Second subtitle"),
    ]

    vtt = """WEBVTT\n\ncue-one\n00:00.250 --> 00:01.100 align:center\nFirst subtitle\n\nNOTE private note\nignored\n\n00:01.250 --> 00:02.250\nSecond subtitle\n"""
    vtt_cues = parse_caption_text(vtt, "vtt")
    assert [cue.text for cue in vtt_cues] == ["First subtitle", "Second subtitle"]


def test_caption_track_is_atomic_undoable_editor_state_and_roundtrips_srt(tmp_path: Path):
    _project, store, sequence = _editor(tmp_path)
    before_ops = len(store.public_state()["branch"]["operations"])
    result = add_caption_track(store, sequence.id, _request(), actor="caption-test")
    state = store.public_state()["branch"]

    assert result["burn_in_supported"] is True
    assert result["render_runtime"] == "advanced_video_text_layer"
    assert result["track"]["kind"] == "text"
    assert result["track"]["role"] == "subtitles"
    assert len(result["cues"]) == 2
    assert len(state["operations"]) == before_ops + 1
    assert state["operations"][-1]["operation"] == "add_caption_track"
    assert all((cue["metadata"] or {})["caption_cue"] for cue in result["cues"])
    assert all(cue["kind"] == "text" for cue in result["cues"])

    exported = export_caption_text(result["cues"], "srt")
    reparsed = parse_caption_text(exported, "srt")
    assert [(cue.start, cue.end, cue.text) for cue in reparsed] == [
        (0.25, 1.1, "First subtitle"),
        (1.25, 2.25, "Second subtitle"),
    ]


def test_caption_track_rejects_cue_beyond_sequence_duration(tmp_path: Path):
    _project, store, sequence = _editor(tmp_path, duration=1.0)
    body = CaptionTrackRequest(
        cues=[CaptionCueInput(start=0.5, end=1.2, text="Too late")],
    )
    with pytest.raises(ValueError, match="beyond the video sequence duration"):
        add_caption_track(store, sequence.id, body)


def test_hardened_caption_patch_preserves_new_text_when_style_changes_and_checks_duration(tmp_path: Path, monkeypatch):
    _project, store, sequence = _editor(tmp_path)
    created = add_caption_track(store, sequence.id, _request(), actor="caption-test")
    item_id = created["cues"][0]["id"]
    member = SimpleNamespace(user={"display_name": "Caption Tester"})

    import aura_music_studio.professional_captions_hardening as hardening

    monkeypatch.setattr(hardening, "_member", lambda request: member)
    monkeypatch.setattr(hardening, "_store", lambda project_name: store)
    monkeypatch.setattr(hardening, "_state", lambda current: current.public_state())

    response = patch_caption_hardened(
        "captions-project",
        item_id,
        CaptionPatchRequest(
            text="New text survives",
            start=0.4,
            end=1.3,
            style=CaptionStyle(size=44, color="#ffeeccff", stroke_width=3, position="top"),
        ),
        object(),
    )
    assert response["cue"]["text"]["content"] == "New text survives"
    assert response["cue"]["text"]["size"] == 44
    assert response["cue"]["start"] == pytest.approx(0.4)
    assert response["cue"]["duration"] == pytest.approx(0.9)
    assert response["cue"]["metadata"]["caption_position"] == "top"

    with pytest.raises(Exception) as exc_info:
        patch_caption_hardened(
            "captions-project",
            item_id,
            CaptionPatchRequest(start=2.8, end=3.2),
            object(),
        )
    assert getattr(exc_info.value, "status_code", None) == 400
    assert "beyond the video sequence duration" in str(getattr(exc_info.value, "detail", ""))


def test_hardening_installs_patch_route_before_base_route():
    install_professional_captions_hardening()
    matches = [
        route
        for route in captions_router.routes
        if getattr(route, "path", None) == "/creative/projects/{project_name}/editor/captions/{item_id}"
        and "PATCH" in getattr(route, "methods", set())
    ]
    assert matches
    assert matches[0].endpoint is patch_caption_hardened


def test_production_advanced_video_compositor_burns_caption_text_into_real_mp4(tmp_path: Path):
    if shutil.which("ffmpeg") is None:
        pytest.skip("FFmpeg is unavailable on this runner")
    project, store, sequence = _editor(tmp_path, duration=1.5)
    body = CaptionTrackRequest(
        name="Burn-in",
        kind="caption",
        language="en",
        style=CaptionStyle(size=36, position="middle"),
        cues=[CaptionCueInput(start=0.1, end=1.2, text="Rendered caption")],
    )
    add_caption_track(store, sequence.id, body, actor="render-test")

    result = UniversalVisualVideoCompositor(project).render_video_advanced(sequence.id)
    output = project / "output" / "editor" / result.filename
    metadata = project / result.metadata_ref
    assert output.is_file() and output.stat().st_size > 0
    payload = json.loads(metadata.read_text(encoding="utf-8"))
    assert payload["advanced_video_compositor"] is True
    assert payload["supports_text_layers"] is True
    assert payload["source_media_mutated"] is False


def test_caption_routes_are_real_editor_api_surfaces():
    paths = {(getattr(route, "path", None), frozenset(getattr(route, "methods", set()))) for route in captions_router.routes}
    assert ("/creative/projects/{project_name}/editor/sequences/{sequence_id}/captions", frozenset({"POST"})) in paths
    assert ("/creative/projects/{project_name}/editor/sequences/{sequence_id}/captions/import", frozenset({"POST"})) in paths
    assert ("/creative/projects/{project_name}/editor/sequences/{sequence_id}/captions", frozenset({"GET"})) in paths
    assert ("/creative/projects/{project_name}/editor/sequences/{sequence_id}/captions/export", frozenset({"GET"})) in paths
