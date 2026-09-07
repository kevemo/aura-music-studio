from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from aura_music_studio.session import Clip, StudioSession, Track
from aura_music_studio.song_dna import SongDNA, SongDNAStore, SongEditDirective, SongSection
from aura_music_studio.song_section_regeneration import (
    commit_multitrack_section_candidate,
    discard_multitrack_section_candidate,
    generate_multitrack_section_candidate,
)


def _project(tmp_path: Path, *, comped: bool = False) -> tuple[Path, str]:
    project = tmp_path / "section-project"
    project.mkdir()
    vocal_source = project / "vocal.wav"
    guitar_source = project / "guitar.wav"
    vocal_source.write_bytes(b"vocal-source")
    guitar_source.write_bytes(b"guitar-source")

    if comped:
        vocal_clips = [
            Clip(
                id="vocal-comp-a",
                name="Vocal comp A",
                kind="audio",
                source=str(vocal_source),
                start=0.0,
                duration=15.0,
                take_lane=2,
                metadata={"committed": True, "comp_segment": True},
            ),
            Clip(
                id="vocal-comp-b",
                name="Vocal comp B",
                kind="audio",
                source=str(vocal_source),
                start=15.0,
                duration=15.0,
                source_offset=15.0,
                take_lane=2,
                metadata={"committed": True, "comp_segment": True},
            ),
        ]
    else:
        vocal_clips = [
            Clip(
                id="vocal-base",
                name="Lead vocal",
                kind="audio",
                source=str(vocal_source),
                start=0.0,
                duration=30.0,
                take_lane=0,
                metadata={"committed": True},
            )
        ]

    session = StudioSession(
        id="section-session",
        name="Section Test",
        bpm=120,
        key="E minor",
        tracks=[
            Track(id="track-vocal", name="Lead Vocal", role="vocals", clips=vocal_clips),
            Track(
                id="track-guitar",
                name="Guitar",
                role="guitar",
                clips=[
                    Clip(
                        id="guitar-base",
                        name="Guitar",
                        kind="audio",
                        source=str(guitar_source),
                        start=0.0,
                        duration=30.0,
                        take_lane=0,
                        metadata={"committed": True},
                    )
                ],
            ),
        ],
    )
    session.save(project / "aura_session.json")

    section = SongSection(id="sec-chorus", name="Chorus", order=1, start_seconds=8.0, end_seconds=16.0)
    directive = SongEditDirective(
        id="edit-chorus",
        action="regenerate_section",
        instruction="Make the chorus bigger and more emotionally intense while keeping the song identity.",
        target_ids=[section.id],
        status="planned",
        metadata={"section_name": section.name},
    )
    SongDNAStore(project).save(
        SongDNA(
            project_name=project.name,
            title="Section Test",
            bpm=120,
            key="E minor",
            sections=[section],
            directives=[directive],
        )
    )
    return project, directive.id


def _fake_generation_runtime(monkeypatch):
    calls = []

    def fake_generate(source, request, work_dir, take_number):
        calls.append((Path(source).name, request.start_seconds, request.end_seconds, request.prompt))
        out = Path(work_dir) / f"candidate-{take_number}.wav"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"generated-section-take")
        return out

    def fake_render(session, project_root, output, work_dir=None):
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_bytes(b"audition-or-mix")
        return Path(output)

    monkeypatch.setattr("aura_music_studio.song_section_regeneration.generate_region_take", fake_generate)
    monkeypatch.setattr("aura_music_studio.song_section_regeneration.render_session", fake_render)
    return calls


def test_generation_creates_per_track_candidates_and_does_not_change_active_session(tmp_path, monkeypatch):
    project, directive_id = _project(tmp_path)
    calls = _fake_generation_runtime(monkeypatch)
    session_path = project / "aura_session.json"
    before = session_path.read_text(encoding="utf-8")

    result = generate_multitrack_section_candidate(project, directive_id)

    assert result.state == "ready"
    assert result.committed is False
    assert result.candidate_kind == "multitrack_section_regeneration_mix"
    assert result.metadata["candidate_track_count"] == 2
    assert len(calls) == 2
    assert all(start == 8.0 for _source, start, _end, _prompt in calls)
    assert all(end == 16.0 for _source, _start, end, _prompt in calls)
    assert any("exact singer identity" in prompt for _source, _start, _end, prompt in calls)
    assert any("same guitar instrument identity" in prompt for _source, _start, _end, prompt in calls)
    assert session_path.read_text(encoding="utf-8") == before

    directive = SongDNAStore(project).load().directives[0]
    assert directive.status == "ready"
    assert directive.metadata["candidate_track_count"] == 2
    assert len(directive.metadata["section_candidates"]) == 2
    assert Path(directive.metadata["candidate_path"]).is_file()


def test_comped_track_fails_closed_before_any_renderer_call(tmp_path, monkeypatch):
    project, directive_id = _project(tmp_path, comped=True)
    monkeypatch.setattr(
        "aura_music_studio.song_section_regeneration.generate_region_take",
        lambda *args, **kwargs: pytest.fail("renderer must not run for an unconsolidated comp"),
    )

    with pytest.raises(RuntimeError, match="multiple comp segments"):
        generate_multitrack_section_candidate(project, directive_id)

    assert SongDNAStore(project).load().directives[0].status == "planned"


def test_discard_keeps_original_session_and_resets_directive(tmp_path, monkeypatch):
    project, directive_id = _project(tmp_path)
    _fake_generation_runtime(monkeypatch)
    session_path = project / "aura_session.json"
    before = session_path.read_text(encoding="utf-8")
    generate_multitrack_section_candidate(project, directive_id)

    result = discard_multitrack_section_candidate(project, directive_id)

    assert result["state"] == "planned"
    assert session_path.read_text(encoding="utf-8") == before
    directive = SongDNAStore(project).load().directives[0]
    assert directive.status == "planned"
    assert "section_candidates" not in directive.metadata
    assert directive.metadata["candidate_history"][-1]["outcome"] == "rejected"


def test_commit_rejects_candidate_if_daw_changed_after_generation(tmp_path, monkeypatch):
    project, directive_id = _project(tmp_path)
    _fake_generation_runtime(monkeypatch)
    generate_multitrack_section_candidate(project, directive_id)

    session_path = project / "aura_session.json"
    changed = StudioSession.load(session_path)
    changed.name = "Changed after candidate"
    changed.save(session_path)

    with pytest.raises(RuntimeError, match="DAW session changed"):
        commit_multitrack_section_candidate(project, directive_id)


def test_commit_adds_new_take_lanes_preserves_old_audio_and_runs_quality_gate(tmp_path, monkeypatch):
    project, directive_id = _project(tmp_path)
    _fake_generation_runtime(monkeypatch)
    generate_multitrack_section_candidate(project, directive_id)

    class FakeWorkspace:
        def __init__(self, root):
            self.root = Path(root)
            self.output_dir = self.root / "outputs"

        def load_manifest(self):
            return SimpleNamespace(
                mix=SimpleNamespace(
                    mastering_preset="streaming",
                    target_lufs=-14.0,
                    true_peak_db=-1.0,
                    mastering_reference=None,
                ),
                renderer=SimpleNamespace(minimum_quality_score=0.8),
            )

        def resolve_asset(self, value):
            return self.root / str(value)

    def fake_master(source, output, **kwargs):
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_bytes(b"mastered")
        return Path(output), {"ok": True}

    quality = {"technical_gate_passed": True, "perceptual_review_required": True}
    monkeypatch.setattr("aura_music_studio.song_section_regeneration.ProjectWorkspace", FakeWorkspace)
    monkeypatch.setattr("aura_music_studio.song_section_regeneration.master", fake_master)
    monkeypatch.setattr("aura_music_studio.song_section_regeneration.build_release_quality_report", lambda *args, **kwargs: quality)
    monkeypatch.setattr("aura_music_studio.song_section_regeneration.enforce_release_quality", lambda report: None)
    monkeypatch.setattr("aura_music_studio.song_section_regeneration.create_revision", lambda *args, **kwargs: None)

    result = commit_multitrack_section_candidate(project, directive_id)

    assert result.state == "complete"
    assert result.committed is True
    assert len(result.metadata["committed_clip_ids"]) == 2
    session = StudioSession.load(project / "aura_session.json")
    vocal = session.find_track("track-vocal")
    guitar = session.find_track("track-guitar")
    assert {clip.id for clip in vocal.clips} >= {"vocal-base"}
    assert {clip.id for clip in guitar.clips} >= {"guitar-base"}
    assert len(vocal.clips) == 2
    assert len(guitar.clips) == 2
    assert vocal.clips[-1].take_lane > vocal.clips[0].take_lane
    assert guitar.clips[-1].take_lane > guitar.clips[0].take_lane
    assert vocal.clips[-1].metadata["section_regeneration"] is True
    assert guitar.clips[-1].metadata["section_regeneration"] is True
    assert vocal.clips[-1].metadata["committed"] is True
    assert vocal.clips[0].metadata["committed"] is False
    assert (project / "outputs" / "Aura_Final_Master.wav").is_file()


def test_song_dna_guard_mounts_section_overlay_without_bypassing_lyric_guard():
    source = Path("aura_music_studio/song_dna_execution_guard.py").read_text(encoding="utf-8")
    guard_definition = source.index("def guarded_generate_song_edit_candidate")
    section_mount = source.index("router.include_router(section_regeneration_router)")
    assert guard_definition < section_mount
    assert "if directive.action == \"replace_lyric_line\"" in source
    assert "if directive.action == \"regenerate_section\"" in source

    app_source = Path("app.py").read_text(encoding="utf-8")
    assert app_source.index("app.include_router(song_dna_execution_guard_router)") < app_source.index(
        "app.include_router(song_dna_router)"
    )
