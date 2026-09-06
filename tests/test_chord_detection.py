from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf
from fastapi import HTTPException

from aura_music_studio.chord_detection import (
    DETECTION_ENGINE,
    _load_candidate,
    _member,
    _write_candidate,
    detect_chord_segments,
    detect_chords_from_file,
    safe_project_audio_source,
)
from aura_music_studio.chord_detection_portal import discover_project_audio_refs
from aura_music_studio.plans import get_plan
from aura_music_studio.song_dna_execution_overlay import router as execution_router


def _triad(midi_notes: tuple[int, int, int], *, sr: int = 22050, seconds: float = 2.0) -> np.ndarray:
    time = np.arange(max(1, int(sr * seconds)), dtype=float) / float(sr)
    output = np.zeros_like(time)
    for note in midi_notes:
        frequency = 440.0 * (2.0 ** ((float(note) - 69.0) / 12.0))
        output += np.sin(2.0 * np.pi * frequency * time)
        output += 0.30 * np.sin(2.0 * np.pi * frequency * 2.0 * time)
        output += 0.15 * np.sin(2.0 * np.pi * frequency * 3.0 * time)
    peak = float(np.max(np.abs(output)))
    return output / max(peak, 1e-9)


def test_detects_stable_major_then_minor_triads():
    sr = 22050
    audio = np.concatenate(
        [
            _triad((60, 64, 67), sr=sr),  # C major
            _triad((57, 60, 64), sr=sr),  # A minor
        ]
    )

    segments = detect_chord_segments(
        audio,
        sr,
        hop_length=1024,
        minimum_segment_seconds=0.35,
        confidence_threshold=0.40,
    )

    symbols = [segment["symbol"] for segment in segments]
    assert symbols[0] == "C"
    assert symbols[-1] == "Am"
    assert all(0.0 <= segment["confidence"] <= 1.0 for segment in segments)
    assert segments[0]["start_seconds"] == pytest.approx(0.0, abs=0.06)
    assert segments[-1]["end_seconds"] == pytest.approx(4.0, abs=0.10)


def test_file_detection_is_local_deterministic_and_bounded(tmp_path):
    source = tmp_path / "c-major.wav"
    sf.write(source, _triad((60, 64, 67), seconds=1.5), 22050)

    result = detect_chords_from_file(
        source,
        minimum_segment_seconds=0.3,
        confidence_threshold=0.40,
    )

    assert result["engine"] == DETECTION_ENGINE
    assert result["quality_scope"] == "major_minor_triads"
    assert result["sample_rate"] == 22050
    assert result["segments"][0]["symbol"] == "C"
    assert result["duration_seconds"] == pytest.approx(1.5, abs=0.02)


def test_silent_source_is_rejected():
    with pytest.raises(ValueError, match="silent"):
        detect_chord_segments(np.zeros(22050, dtype=float), 22050)


def test_project_audio_source_cannot_escape_tenant_project(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    inside = project / "input" / "take.wav"
    inside.parent.mkdir()
    sf.write(inside, _triad((60, 64, 67), seconds=0.4), 22050)
    outside = tmp_path / "outside.wav"
    sf.write(outside, _triad((60, 64, 67), seconds=0.4), 22050)

    assert safe_project_audio_source(project, "input/take.wav") == inside.resolve()
    with pytest.raises(ValueError, match="escaped"):
        safe_project_audio_source(project, "../outside.wav")
    with pytest.raises(ValueError, match="project-relative"):
        safe_project_audio_source(project, str(outside.resolve()))


def test_portal_audio_discovery_is_bounded_to_supported_project_roots(tmp_path):
    project = tmp_path / "project"
    (project / "input").mkdir(parents=True)
    (project / "work").mkdir(parents=True)
    sf.write(project / "input" / "song.wav", _triad((60, 64, 67), seconds=0.3), 22050)
    sf.write(project / "work" / "hidden.wav", _triad((57, 60, 64), seconds=0.3), 22050)
    (project / "input" / "notes.txt").write_text("not audio", encoding="utf-8")

    refs = discover_project_audio_refs(project)
    assert refs == ["input/song.wav"]


def test_detection_requires_harmony_architect_entitlement():
    free_request = SimpleNamespace(state=SimpleNamespace(member=SimpleNamespace(plan=get_plan("free"))))
    with pytest.raises(HTTPException) as exc:
        _member(free_request)
    assert exc.value.status_code == 403

    base_member = SimpleNamespace(plan=get_plan("base"))
    base_request = SimpleNamespace(state=SimpleNamespace(member=base_member))
    assert _member(base_request) is base_member


def test_candidate_integrity_rejects_tampering(tmp_path):
    candidate_id = "chdet_" + "a" * 32
    payload = _write_candidate(
        tmp_path,
        {
            "schema_version": 1,
            "candidate_id": candidate_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_ref": "input/song.wav",
            "source_sha256": "b" * 64,
            "engine": DETECTION_ENGINE,
            "quality_scope": "major_minor_triads",
            "events": [],
        },
    )
    loaded = _load_candidate(tmp_path, candidate_id, payload["candidate_sha256"])
    assert loaded["candidate_sha256"] == payload["candidate_sha256"]

    path = tmp_path / "work" / "chord_detection" / f"{candidate_id}.json"
    tampered = json.loads(path.read_text(encoding="utf-8"))
    tampered["source_ref"] = "input/other.wav"
    path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ValueError, match="integrity"):
        _load_candidate(tmp_path, candidate_id, payload["candidate_sha256"])


def test_detection_routes_are_mounted_in_song_dna_execution_overlay():
    paths = {getattr(route, "path", "") for route in execution_router.routes}
    assert "/projects/{project_name}/song-dna/chords/detect-preview" in paths
    assert "/projects/{project_name}/song-dna/chords/detect-commit" in paths
    assert "/song-editor/{project_name}/chord-detection" in paths
